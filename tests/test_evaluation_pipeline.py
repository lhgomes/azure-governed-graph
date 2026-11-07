"""SIMULATED. No cloud calls; quality judges cannot bypass deterministic floors."""
from dataclasses import asdict
from copy import deepcopy
from decimal import Decimal
import json
import sys
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from governedgraph.agents.persona_simulator import (
    FoundryPersona, PERSONA_INSTRUCTIONS, PersonaBlocked, PersonaScenario, ScriptedPersona, verify_persona_version,
)
from governedgraph.common.config import REPO_ROOT, settings
from governedgraph.common.io import load_json
from governedgraph.common.contracts import ContractError
from governedgraph.evaluation import (
    DIMENSIONS, FLOORS, HumanRating, TokenRateCard, calibrate, evidence_digest, summarize_candidate, verify_comparison,
)
from governedgraph.foundry_evaluation import EVALUATION_HEADERS, FoundryEvaluationClient, JUDGE_OUTPUT, RUBRICS
from evals.run_model_comparison import UsageRecorder, collect_candidate, expected_behavior, report_passed, score_report
from evals.run_evals import load, persona_cases


@pytest.fixture(autouse=True)
def fake_openai(monkeypatch):
    module = ModuleType("openai")

    class BadRequestError(Exception):
        def __init__(self, code):
            self.code = code

    module.BadRequestError = BadRequestError
    monkeypatch.setitem(sys.modules, "openai", module)
    return BadRequestError


def scenario():
    return PersonaScenario("fictional", "Fictional member.", "Understand the letter.", "Explain the letter.")


@pytest.mark.parametrize("reply", [
    "In your own words, what happens on the transition date?",
    "Could you explain what happens on the transition date?",
    "Tell me what you understood about the transition.",
])
def test_scripted_persona_replays_fixed_answer_without_matching_counsellor_wording(reply):
    script = {"opening": "Explain the letter.", "teach_back_answer": "It stays the same."}
    actor = ScriptedPersona(script)
    assert actor.next_utterance("") == script["opening"]
    assert actor.next_utterance(reply) == script["teach_back_answer"]


@pytest.mark.parametrize("reply,answer", [
    ("", "It stays the same."),
    ("   ", "It stays the same."),
    ("Could you explain what you understood?", ""),
])
def test_scripted_persona_does_not_invent_a_missing_reply_or_answer(reply, answer):
    actor = ScriptedPersona({"opening": "Explain the letter.", "teach_back_answer": answer})
    assert actor.next_utterance("") == "Explain the letter."
    assert actor.next_utterance(reply) is None


def test_persona_only_receives_own_scenario_and_member_visible_text():
    client = Mock()
    client.responses.create.return_value = SimpleNamespace(
        status="completed", output_text='{"utterance":"Explain the letter."}',
        output=[SimpleNamespace(type="message")])
    actor = FoundryPersona(scenario(), client=client, agent_name="persona", agent_version="1")
    actor.next_utterance("")
    actor.next_utterance("Please restate what I explained.")
    inputs = client.responses.create.call_args.kwargs
    assert inputs["store"] is False
    assert inputs["extra_body"]["agent_reference"]["version"] == "1"
    assert set(json.loads(inputs["input"][0]["content"])["scenario"]) == {
        "persona_id", "profile", "goal", "opening", "teach_back_answer"}
    assert json.loads(inputs["input"][-2]["content"])["counsellor_reply"] == "Please restate what I explained."
    with pytest.raises(ContractError):
        actor.next_utterance({"strategy": "S5"})
    with pytest.raises(ContractError):
        PersonaScenario.from_dict({**asdict(scenario()), "ranked": ["S5"]})


@pytest.mark.parametrize("attribute,value", [
    ("kind", "hosted"), ("instructions", "different"), ("model", "different"),
    ("tools", [SimpleNamespace(type="function")]),
])
def test_persona_rejects_any_definition_with_internals_or_drift(attribute, value):
    definition = SimpleNamespace(kind="prompt", model="gpt-compact", temperature=0,
                                 instructions=PERSONA_INSTRUCTIONS, tools=[])
    setattr(definition, attribute, value)
    with pytest.raises(ValueError):
        verify_persona_version(SimpleNamespace(definition=definition), "gpt-compact")


@pytest.mark.parametrize("status,text,output", [
    ("failed", "hello", []), ("completed", "", []),
    ("completed", "hello", [SimpleNamespace(type="function_call")]),
])
def test_persona_errors_are_not_silently_substituted_with_scripted_success(status, text, output):
    client = Mock()
    client.responses.create.return_value = SimpleNamespace(status=status, output_text=text, output=output)
    with pytest.raises(RuntimeError):
        FoundryPersona(scenario(), client=client, agent_name="persona", agent_version="1").next_utterance("")


def test_actor_content_filter_is_reported_without_suppressing_other_errors(fake_openai):
    client = Mock()
    actor = FoundryPersona(scenario(), client=client, agent_name="persona", agent_version="1")
    client.responses.create.side_effect = fake_openai("content_filter")
    with pytest.raises(PersonaBlocked):
        actor.next_utterance("")
    client.responses.create.side_effect = fake_openai("invalid_payload")
    with pytest.raises(fake_openai):
        actor.next_utterance("")


def evidence():
    return {"query": "Explain the letter.", "response": "A human can help."}


def rating():
    return HumanRating("case", evidence_digest(evidence()), "review-HR-01", "human",
                       {name: 4 for name in DIMENSIONS})


def test_human_calibration_reports_measured_agreement_on_exact_frozen_evidence():
    item = {"case_id": "case", "evidence": evidence(), "judge_scores": {name: 5 for name in DIMENSIONS}}
    result = calibrate([item], [rating()])
    assert all(value == {"n": 1, "mean_absolute_error": 1, "exact_agreement": 0} for value in result.values())
    with pytest.raises(ContractError):
        calibrate([{**item, "evidence": {"response": "changed"}}], [rating()])


@pytest.mark.parametrize("change", [{"source": "llm"}, {"reviewer_reference": ""},
                                  {"scores": {name: True for name in DIMENSIONS}},
                                  {"scores": {"groundedness": 5}}, {"age": 78}])
def test_human_calibration_never_accepts_generated_or_incomplete_ratings(change):
    with pytest.raises(ContractError):
        HumanRating.from_dict({**asdict(rating()), **change})


def test_human_calibration_never_passes_empty_duplicate_or_missing_labels():
    item = {"case_id": "case", "evidence": evidence(), "judge_scores": {name: 5 for name in DIMENSIONS}}
    for items, ratings in [([], []), ([item], []), ([item, item], [rating()]),
                           ([item], [rating(), rating()])]:
        with pytest.raises(ContractError):
            calibrate(items, ratings)


def measured_row():
    return {"case_id": "case", "label": "SIMULATED", **dict.fromkeys(FLOORS, True),
            "resolved": True, "expectation_met": True, "latency_ms": 10,
            "judge_scores": {name: 5 for name in DIMENSIONS}, "usage_complete": True,
            "usage": {"input_tokens": 1_000_000, "cached_input_tokens": 500_000, "output_tokens": 100_000}}


def test_cost_uses_measured_cached_tokens_and_actual_resolutions_not_deliveries():
    card = TokenRateCard("TEST", "test fixture only", "2", "1", "8")
    assert card.cost(**measured_row()["usage"]) == Decimal("2.3")
    report = summarize_candidate([measured_row()], card)
    assert Decimal(report["cost_per_resolved_query"]) == Decimal("2.3")
    assert summarize_candidate([{**measured_row(), "resolved": False}], card)["cost_per_resolved_query"] is None
    assert summarize_candidate([{**measured_row(), "usage_complete": False}], card)["cost"] is None
    assert summarize_candidate([measured_row()])["cost"] is None


def test_perfect_llm_judge_score_cannot_override_a_failed_numeric_gate():
    report = summarize_candidate([{**measured_row(), "numeric_fidelity": False}])
    assert report["quality"]["groundedness"]["mean"] == 5
    assert not report["hard_floors"]["numeric_fidelity"]
    with pytest.raises(ContractError):
        summarize_candidate([])
    with pytest.raises(ContractError):
        summarize_candidate([measured_row(), measured_row()])


def test_usage_recorder_counts_all_tool_rounds_and_retains_missing_evidence():
    client = Mock()
    actual = SimpleNamespace(usage=SimpleNamespace(input_tokens=20, output_tokens=5,
                                                  input_tokens_details=SimpleNamespace(cached_tokens=4)))
    client.responses.create.side_effect = [actual, SimpleNamespace(usage=None), actual]
    recorder = UsageRecorder(client)
    for _ in range(3):
        recorder.create(input="synthetic")
    assert recorder.usage == {"input_tokens": 40, "cached_input_tokens": 8, "output_tokens": 10}
    assert recorder.rounds == 3 and not recorder.complete


def test_foundry_judges_score_explicit_context_and_never_target_the_runtime_agent():
    project = Mock()
    project.get_openai_client.return_value.evals.create.return_value.id = "eval"
    project.get_openai_client.return_value.evals.runs.create.return_value.id = "run"
    evaluator = FoundryEvaluationClient(project, judge_deployment="gpt-counsellor")
    versions = {name: "1" for name in RUBRICS}
    item = {"case_id": "case", "response": "Approved response",
            "context": "Approved passages", "expected_behavior": "Explain without advice."}
    assert evaluator.start([item], rubric_versions=versions) == ("eval", "run")
    kwargs = evaluator.client.evals.create.call_args.kwargs
    groundedness = kwargs["testing_criteria"][0]
    assert groundedness["evaluator_name"] == "gg-groundedness"
    assert groundedness["evaluator_version"] == "1"
    assert groundedness["data_mapping"] == {
        name: "{{item." + name + "}}" for name in ("response", "context", "expected_behavior")}
    assert all(criterion["initialization_parameters"]["threshold"] == 4 for criterion in kwargs["testing_criteria"])
    run_kwargs = evaluator.client.evals.runs.create.call_args.kwargs
    assert run_kwargs["data_source"]["type"] == "jsonl"
    assert run_kwargs["data_source"]["source"]["content"] == [{"item": item}]
    assert "agent" not in str(run_kwargs)
    assert "query" not in kwargs["data_source_config"]["item_schema"]["properties"]


def test_sdk_rubrics_use_the_verified_version_api_and_canonical_numeric_output():
    project = Mock()
    project.evaluators.create_version.return_value.version = "3"
    evaluator = FoundryEvaluationClient(project, judge_deployment="gpt-counsellor")
    assert evaluator.register_rubrics() == dict.fromkeys(RUBRICS, "3")
    project.get_openai_client.assert_called_once_with(default_headers=EVALUATION_HEADERS)
    assert not project.beta.evaluators.create_version.called
    for call in project.evaluators.create_version.call_args_list:
        assert call.kwargs["headers"] == EVALUATION_HEADERS
        definition = call.kwargs["evaluator_version"]["definition"]
        assert definition["prompt_text"].endswith(JUDGE_OUTPUT)
        assert set(definition["metrics"]) == {"custom_prompt"}
        assert "query" not in definition["data_schema"]["properties"]


def test_scoring_persists_fresh_remote_ids_and_portal_results_link(tmp_path, monkeypatch):
    report = deepcopy(load_json(REPO_ROOT / "evals/results/model-comparison-2026-10-27.json"))
    report.pop("foundry", None)
    remote = Mock()
    remote.register_rubrics.return_value = dict.fromkeys(RUBRICS, "1")
    remote.start.return_value = ("fresh-eval", "fresh-run")
    remote.results.return_value = [
        {"case_id": row["evidence"]["case_id"], "judge_scores": dict.fromkeys(DIMENSIONS, 4)}
        for rows in report["rows"].values() for row in rows
    ]
    remote.client.evals.runs.retrieve.return_value = SimpleNamespace(
        status="completed", report_url="https://ai.azure.com/synthetic-evaluation",
    )
    monkeypatch.setattr("evals.run_model_comparison.FoundryEvaluationClient", lambda *a, **kw: remote)
    output = tmp_path / "report.json"
    score_report(Mock(), report, output, "judge", {})
    saved = load_json(output)
    assert saved["foundry"] == {
        "eval_id": "fresh-eval", "run_id": "fresh-run", "rubric_versions": dict.fromkeys(RUBRICS, "1"),
        "groundedness_evaluator": "gg-groundedness", "groundedness_version": "1",
        "judge": "judge", "status": "completed",
        "report_url": "https://ai.azure.com/synthetic-evaluation",
    }
    remote.client.evals.runs.retrieve.assert_called_once_with(eval_id="fresh-eval", run_id="fresh-run")


@pytest.mark.parametrize("outputs", [[], ["case", "case"], ["other"]])
def test_missing_or_duplicate_remote_judgments_are_not_a_success(outputs):
    project = Mock()
    evaluator = FoundryEvaluationClient(project, judge_deployment="judge")
    evaluator.client.evals.runs.retrieve.return_value.status = "completed"
    evaluator.client.evals.runs.output_items.list.return_value = [
        SimpleNamespace(model_dump=lambda case_id=case_id: {
            "datasource_item": {"case_id": case_id},
            "results": [{"name": name, "score": 5.0} for name in DIMENSIONS]}) for case_id in outputs]
    with pytest.raises(ContractError):
        evaluator.results("eval", "run", expected_ids={"case"})


@pytest.mark.parametrize("score", [None, 0, True, 4.5])
def test_missing_invalid_or_normalized_cloud_scores_are_not_quality_measurements(score):
    evaluator = FoundryEvaluationClient(Mock(), judge_deployment="judge")
    evaluator.client.evals.runs.retrieve.return_value.status = "completed"
    evaluator.client.evals.runs.output_items.list.return_value = [
        SimpleNamespace(model_dump=lambda: {"datasource_item": {"case_id": "case"},
                                           "results": [{"name": name, "score": score} for name in DIMENSIONS]})]
    with pytest.raises(ContractError):
        evaluator.results("eval", "run", expected_ids={"case"})


@pytest.mark.parametrize("score", [None, 5])
def test_judge_service_failure_reports_case_and_error_without_fabricating_a_score(score):
    evaluator = FoundryEvaluationClient(Mock(), judge_deployment="judge")
    evaluator.client.evals.runs.retrieve.return_value.status = "completed"
    evaluator.client.evals.runs.output_items.list.return_value = [
        SimpleNamespace(model_dump=lambda: {"datasource_item": {"case_id": "case"},
            "results": [{"name": "groundedness", "score": score, "status": "error",
                         "sample": {"error": {"code": "FAILED_EXECUTION"}}}]})]
    with pytest.raises(ContractError, match=r"groundedness for case.*status=error, error=FAILED_EXECUTION"):
        evaluator.results("eval", "run", expected_ids={"case"})


@pytest.mark.parametrize("case", [
    next(case for case in load("curated.jsonl") if case["id"] == "cur-04"),
    next(case for case in persona_cases() if case["id"] == "persona-samira"),
])
def test_letter_help_expectations_come_from_fixed_case_goal_not_model_classification(case):
    strategy = SimpleNamespace(name="Step-by-step", teach_back=True)
    behaviour = expected_behavior(case, strategy)
    assert "do not have their letter here" in behaviour
    assert "leaving out personal details" in behaviour
    assert "before any substantive explanation" in behaviour
    assert "Explain the official transition figures" not in behaviour
    assert expected_behavior({**case, "expect": "escalate"}, strategy) == (
        "Decline personal pension choices and arrange a human advisor.")


def test_faithfulness_rubric_retains_unsupported_claim_and_uncertainty_failures():
    assert "major unsupported factual claim" in RUBRICS["groundedness"]
    assert "drops a material uncertainty" in RUBRICS["groundedness"]
    assert "unrelated figures" in RUBRICS["groundedness"]
    assert "unsupported factual detail" in RUBRICS["groundedness"]


def test_guarded_replay_runs_every_persona_and_adversarial_variant_without_profile_egress(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.__class__, "run_dir", property(lambda _: tmp_path))
    monkeypatch.setattr(settings.__class__, "is_azure", property(lambda _: False))

    class Composer:
        agent_name = "synthetic-fake"
        agent_version = "1"
        client = SimpleNamespace(reset=lambda: None, usage={
            "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}, complete=True)

        def draft(self, tools, _utterance, strategy):
            assert not hasattr(tools.journey, "profile")
            from governedgraph.agents.counsellor import TEMPLATES

            return next(iter(TEMPLATES["templates"][strategy.template_id].values()))

    rows = collect_candidate("fake", Composer(), lambda item: ScriptedPersona(asdict(item)), tmp_path)
    assert len(rows) == 17
    assert all(row["actor_status"] == "scripted fixture" for row in rows)
    assert all(row["expectation_met"] for row in rows)
    assert all(row[name] for row in rows for name in FLOORS)
    assert any(row["resolved"] for row in rows)
    assert all(set(row["evidence"]) == {"case_id", "query", "response", "context", "expected_behavior"}
               for row in rows)
    official_context = next(row["evidence"]["context"] for row in rows if row["case_id"] == "cur-01")
    assert "as_of: 2026-10-01" in official_context
    assert "source: mock-administration-system" in official_context
    assert "calculation_run_id: RUN-2026-10-INV-0042" in official_context


@pytest.mark.parametrize("damage", ["none", "missing_case", "numeric_failure", "unmeasured_quality", "invented_summary"])
def test_ci_verifies_all_frozen_cases_and_rejects_fabricated_matrix_values(damage):
    report = deepcopy(load_json(REPO_ROOT / "evals/results/model-comparison-2026-10-27.json"))
    cards = load_json(REPO_ROOT / "evals/datasets/model-rates-2026-10-27.json")
    expected_ids = {case["id"] for case in load("curated.jsonl") + load("adversarial.jsonl") + persona_cases()}
    if damage == "missing_case":
        report["rows"]["gpt-counsellor"].pop()
    if damage == "numeric_failure":
        report["rows"]["gpt-counsellor"][0]["numeric_fidelity"] = False
    if damage == "unmeasured_quality":
        report["rows"]["gpt-counsellor"][0].pop("judge_scores")
    if damage == "invented_summary":
        report["summary"]["gpt-counsellor"]["quality"]["groundedness"]["mean"] = 5
    if damage == "none":
        verify_comparison(report, cards, expected_case_ids=expected_ids)
        assert not report_passed(report)
    else:
        with pytest.raises(ContractError):
            verify_comparison(report, cards, expected_case_ids=expected_ids)
