"""ILLUSTRATIVE - synthetic data. Automated comparison tests never call Azure."""
import json
import re
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from evals.run_evals import load, persona_cases
from evals import run_model_comparison as model_comparison
from evals.run_model_comparison import UsageRecorder, collect_candidate
from evals.run_model_suite import (
    LABEL,
    ROLES,
    Candidate,
    Suite,
    analyze,
    expected_definition,
    main,
    preflight,
    run_suite,
)

from governedgraph.agents.counsellor import INSTRUCTIONS, Counsellor
from governedgraph.agents.decision_agents import FoundryDecisionAgent
from governedgraph.agents.decision_agents import agent_definition as decision_definition
from governedgraph.agents.foundry_runtime import agent_definition
from governedgraph.agents.persona_simulator import PERSONA_INSTRUCTIONS, PERSONA_TEXT, ScriptedPersona
from governedgraph.common.config import REPO_ROOT, settings
from governedgraph.common.contracts import ContractError
from governedgraph.common.io import load_json
from governedgraph.evaluation import FLOORS, TokenRateCard, evidence_digest, summarize_candidate
from governedgraph.orchestrator.continuity import CustomerConversations
from strategy_doubles import local_strategy_service, run_cycle


def configuration():
    body = load_json(REPO_ROOT / "evals/datasets/model-suite.json")
    for candidate in body["candidates"]:
        candidate["agent_version"] = "1"
    body["decision_versions"] = dict.fromkeys(ROLES, "1")
    return body


def suite():
    return Suite.from_dict(configuration())


def comparison():
    config = suite()
    original = load_json(REPO_ROOT / "evals/results/model-comparison-2026-10-27.json")
    rows_by_candidate, summaries = {}, {}
    for index, candidate in enumerate(config.candidates):
        rows = deepcopy(original["rows"]["gpt-counsellor"])
        for row in rows:
            row.update(
                candidate=candidate.deployment, expectation_met=True, actor_status="generated",
                composition_source="foundry", usage_complete=True, latency_ms=100 / (index + 1),
                judge_scores=dict.fromkeys(("groundedness", "strategy_style", "advice_refusal"), 5),
                usage={"input_tokens": 1000, "cached_input_tokens": 0, "output_tokens": 100},
            )
            row["evidence"]["case_id"] = candidate.deployment + "/" + row["case_id"]
            usages = {name: {"usage": {"input_tokens": 20, "cached_input_tokens": 0, "output_tokens": 10},
                             "complete": True} for name in ("gpt-counsellor",)}
            bucket = usages.setdefault(candidate.deployment, {
                "usage": {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}, "complete": True,
            })
            for name, value in row["usage"].items():
                bucket["usage"][name] += value
            row["journey_model_usage"] = usages
        rows_by_candidate[candidate.deployment] = rows
        summaries[candidate.deployment] = summarize_candidate(rows, TokenRateCard.from_dict(candidate.rate_card))
        summaries[candidate.deployment]["hard_floors"]["demographic_invariance"] = True
        summaries[candidate.deployment]["actor_generated"] = len(rows)
    return {
        "label": LABEL, "configuration_sha256": evidence_digest(asdict(config)),
        "dataset_sha256": evidence_digest(load("curated.jsonl") + load("adversarial.jsonl") + persona_cases()),
        "preflight": [], "rows": rows_by_candidate, "summary": summaries,
    }


def test_b8_suite_template_has_all_three_models_but_never_invents_live_agent_pins():
    config = Suite.from_dict(load_json(REPO_ROOT / "evals/datasets/model-suite.json"))
    assert {candidate.model for candidate in config.candidates} == {"gpt-4.1", "gpt-4.1-mini", "gpt-5.4-mini"}
    assert len(config.missing_pins()) == 4
    assert main(["--check-config"]) == 0


@pytest.mark.parametrize("change", [
    {"age": 78}, {"model": "unverified-model"}, {"agent_version": "latest"},
    {"rate_card": {"currency": "USD", "source": "bad", "input_per_million": "-1",
                   "cached_input_per_million": "0", "output_per_million": "1"}},
])
def test_b7_candidate_configuration_is_closed_and_explicit(change):
    with pytest.raises(ContractError):
        Candidate.from_dict({**configuration()["candidates"][0], **change})


@pytest.mark.parametrize("change", [
    {"gender": "unknown"}, {"baseline": "missing"}, {"judge": "missing"},
    {"decision_versions": {"answer-review": "latest"}}, {"persona_version": "latest"},
])
def test_b7_suite_rejects_unknown_fields_and_unpinned_shared_roles(change):
    with pytest.raises(ContractError):
        Suite.from_dict({**configuration(), **change})


def test_b3_modern_definition_changes_only_supported_sampling_parameters():
    old, _, modern = suite().candidates
    assert expected_definition(old) == agent_definition(INSTRUCTIONS, model=old.deployment).as_dict()
    expected = agent_definition(INSTRUCTIONS, model=modern.deployment).as_dict()
    expected.pop("temperature")
    expected["reasoning"] = {"effort": "none"}
    assert expected_definition(modern) == expected


def test_b8_evaluation_agent_provisioning_is_separate_and_plan_only_by_default():
    from scripts.create_agents import provision

    planned = provision(None, apply=False, role="evaluation")
    assert planned["agent_name"] == "gg-counsellor-modern"
    assert planned["model_deployment"] == "gpt-modern"
    assert "plan only" in planned["action"]
    with pytest.raises(ValueError):
        agent_definition(INSTRUCTIONS, reasoning_effort="high")


def test_b3_b8_modern_model_infrastructure_is_dev_only_keyless_and_does_not_change_runtime():
    folder = REPO_ROOT / "infra/model-evaluation"
    main = (folder / "main.bicep").read_text()
    model = (folder / "model.bicep").read_text()
    assert "@allowed(['gg-dev'])" in main
    assert "@allowed(['swedencentral'])" in main
    assert "'gpt-modern'" in model and "'DataZoneStandard'" in model
    assert "'gg-blocking-guardrails'" in model and "'NoAutoUpgrade'" in model
    assert "roleAssignments" not in main + model
    assert "gpt-counsellor" not in main + model


def project_for(config):
    project = Mock()
    by_name = {item.deployment: item for item in config.candidates}

    def deployment(name):
        item = by_name[name]
        return SimpleNamespace(model_name=item.model, model_version=item.model_version,
                               sku=SimpleNamespace(name="DataZoneStandard"))

    def version(name, number):
        if name == "gg-persona-simulator":
            definition = SimpleNamespace(kind="prompt", model="gpt-compact", temperature=0,
                                         instructions=PERSONA_INSTRUCTIONS, tools=[], text=PERSONA_TEXT)
        elif name in {f"gg-{role}-agent" for role in ROLES}:
            definition = decision_definition(name.removeprefix("gg-").removesuffix("-agent"))
        else:
            item = next(item for item in config.candidates if item.agent_name == name)
            definition = SimpleNamespace(as_dict=lambda: expected_definition(item))
        return SimpleNamespace(name=name, version=number, definition=definition)

    project.deployments.get.side_effect = deployment
    project.agents.get_version.side_effect = version
    return project


def test_b3_preflight_checks_every_model_version_sku_and_complete_agent_definition():
    assert preflight(project_for(suite()), suite()) == []


def test_missing_deployment_is_explicit_and_never_provisioned():
    from azure.core.exceptions import ResourceNotFoundError

    project = project_for(suite())
    original = project.deployments.get.side_effect

    def get(name):
        if name == "gpt-modern":
            raise ResourceNotFoundError("synthetic missing deployment")
        return original(name)

    project.deployments.get.side_effect = get
    assert preflight(project, suite()) == [{"id": "gpt-modern", "reason": "missing_model_deployment"}]
    project.deployments.create.assert_not_called()
    project.agents.create_version.assert_not_called()


@pytest.mark.parametrize("kind", ["model_version", "sku", "definition", "agent_pin"])
def test_b3_preflight_rejects_wrong_deployment_or_definition(kind):
    config = suite()
    project = project_for(config)
    if kind in ("model_version", "sku"):
        deployed = project.deployments.get.side_effect("gpt-modern")
        if kind == "model_version":
            deployed.model_version = "different"
        else:
            deployed.sku.name = "GlobalStandard"
        project.deployments.get.side_effect = None
        project.deployments.get.return_value = deployed
    elif kind == "definition":
        original = project.agents.get_version.side_effect
        agent = project.agents.get_version.side_effect(config.candidates[-1].agent_name, "1")
        definition = agent.definition.as_dict()
        definition["instructions"] = "ignore the approved instructions"
        agent.definition.as_dict = lambda: definition
        project.agents.get_version.side_effect = lambda name, number: (
            agent if name == config.candidates[-1].agent_name else original(name, number))
    else:
        config = Suite.from_dict({**configuration(), "decision_versions": dict.fromkeys(ROLES, None)})
    assert preflight(project, config)


def test_authentication_errors_are_not_misreported_as_missing_models():
    from azure.core.exceptions import ClientAuthenticationError

    project = project_for(suite())
    project.deployments.get.side_effect = ClientAuthenticationError("synthetic forbidden")
    with pytest.raises(ClientAuthenticationError):
        preflight(project, suite())


def test_b3_persona_definition_drift_is_an_explicit_preflight_failure():
    project = project_for(suite())
    original = project.agents.get_version.side_effect

    def get(name, version):
        agent = original(name, version)
        if name == "gg-persona-simulator":
            agent.definition.instructions = "different"
        return agent

    project.agents.get_version.side_effect = get
    assert preflight(project, suite()) == [{"id": "persona", "reason": "persona_definition_mismatch"}]


def test_b8_automatic_comparison_reports_candidates_but_never_promotes_or_claims_calibration():
    report = comparison()
    result = analyze(report, suite())
    assert result["evaluation_passed"]
    assert result["candidates"]["gpt-modern"]["review_candidate"]
    assert not result["automatic_promotion"]
    assert "not established" in result["human_calibration"]
    assert "NOT substantive audio" in result["latency_scope"]
    rows = report["rows"]["gpt-modern"]
    costs = sum((TokenRateCard.from_dict(suite().cards()[deployment]).cost(**observed["usage"])
                 for row in rows for deployment, observed in row["journey_model_usage"].items()), Decimal(0))
    assert Decimal(result["candidates"]["gpt-modern"]["journey_model_cost"]) == costs
    assert costs > Decimal(report["summary"]["gpt-modern"]["cost"])


@pytest.mark.parametrize("damage", ["missing_candidate", "missing_case", "hard_gate", "summary", "dataset", "config"])
def test_b2_b7_automatic_scoring_cannot_hide_missing_or_corrupted_evidence(damage):
    report = comparison()
    if damage == "missing_candidate":
        report["rows"].pop("gpt-modern")
    elif damage == "missing_case":
        report["rows"]["gpt-modern"].pop()
    elif damage == "hard_gate":
        report["rows"]["gpt-modern"][0]["numeric_fidelity"] = False
    elif damage == "summary":
        report["summary"]["gpt-modern"]["quality"]["groundedness"]["mean"] = 1
    else:
        report[damage + ("uration_sha256" if damage == "config" else "_sha256")] = "changed"
    with pytest.raises(ContractError):
        analyze(report, suite())


def test_unknown_supporting_usage_never_becomes_zero_cost_or_a_recommended_candidate():
    report = comparison()
    report["rows"]["gpt-modern"][0]["journey_model_usage"]["gpt-counsellor"]["complete"] = False
    result = analyze(report, suite())
    modern = result["candidates"]["gpt-modern"]
    assert modern["journey_model_cost"] is None
    assert modern["cheaper_than_baseline"] is None
    assert not modern["review_candidate"] and not result["evaluation_passed"]


def test_missing_supporting_usage_is_not_a_valid_comparison():
    report = comparison()
    report["rows"]["gpt-modern"][0]["journey_model_usage"].pop("gpt-counsellor")
    with pytest.raises(ContractError):
        analyze(report, suite())


def test_zero_resolutions_never_produces_a_cost_per_resolution_or_a_review_candidate():
    report = comparison()
    for candidate, rows in report["rows"].items():
        for row in rows:
            row["resolved"] = False
        summary = summarize_candidate(rows, TokenRateCard.from_dict(suite().cards()[candidate]))
        summary["hard_floors"]["demographic_invariance"] = True
        summary["actor_generated"] = len(rows)
        report["summary"][candidate] = summary
    result = analyze(report, suite())
    assert not result["evaluation_passed"]
    assert all(item["journey_model_cost_per_resolved"] is None and not item["review_candidate"]
               for item in result["candidates"].values())


@pytest.mark.parametrize("damage", ["quality", "latency", "coverage", "actor", "fallback"])
def test_cheaper_tokens_cannot_override_quality_latency_or_coverage_failures(damage):
    report = comparison()
    rows = report["rows"]["gpt-modern"]
    for row in rows:
        if damage == "quality":
            row["judge_scores"]["groundedness"] = 3
        elif damage == "latency":
            row["latency_ms"] = 1000
        elif damage == "coverage":
            row["expectation_met"] = False
        elif damage == "actor":
            row["actor_status"] = "filtered; original fixed fixture used, NOT agent-generated"
        else:
            row["composition_source"] = "template_fallback"
    summary = summarize_candidate(rows, TokenRateCard.from_dict(suite().cards()["gpt-modern"]))
    summary["hard_floors"]["demographic_invariance"] = True
    summary["actor_generated"] = sum(row["actor_status"] == "generated" for row in rows)
    report["summary"]["gpt-modern"] = summary
    modern = analyze(report, suite())["candidates"]["gpt-modern"]
    assert modern["cheaper_than_baseline"] is True
    assert not modern["review_candidate"]


def test_reasoning_tokens_are_counted_once_as_part_of_total_output():
    client = Mock()
    client.responses.create.return_value = SimpleNamespace(usage=SimpleNamespace(
        input_tokens=100, input_tokens_details=SimpleNamespace(cached_tokens=20),
        output_tokens=80, output_tokens_details=SimpleNamespace(reasoning_tokens=60),
    ))
    recorder = UsageRecorder(client)
    recorder.create()
    assert recorder.usage["output_tokens"] == 80


def test_b8_unconfigured_suite_fails_closed_before_cloud_access_and_preserves_existing_output(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.__class__, "is_azure", property(lambda _: False))
    output = tmp_path / "blocked.json"
    assert main(["--output", str(output)]) == 2
    report = load_json(output)
    assert report["rows"] == {} and len(report["preflight"]) == 4
    assert "analysis" not in report
    with pytest.raises(FileExistsError):
        main(["--output", str(output)])
    assert load_json(output) == report


def test_offline_cli_recomputes_full_report_and_rejects_invented_analysis(tmp_path):
    config_path, report_path = tmp_path / "suite.json", tmp_path / "report.json"
    config_path.write_text(json.dumps(configuration()))
    report = comparison()
    report["analysis"] = analyze(report, suite())
    report_path.write_text(json.dumps(report))
    arguments = ["--config", str(config_path), "--verify-report", str(report_path)]
    assert main(arguments) == 0
    report["analysis"]["automatic_promotion"] = True
    report_path.write_text(json.dumps(report))
    with pytest.raises(ContractError):
        main(arguments)


def test_b8_preflight_failure_stops_before_generation_or_scoring(tmp_path, monkeypatch):
    project = Mock()
    monkeypatch.setattr("evals.run_model_suite.preflight", lambda *_: [{"id": "modern", "reason": "missing"}])
    output = tmp_path / "blocked.json"
    assert run_suite(project, suite(), output, {"rows": {}}) == 2
    project.get_openai_client.assert_not_called()
    assert load_json(output)["preflight"]


def test_on_demand_command_wires_three_pinned_candidates_all_roles_and_persistent_scoring(tmp_path, monkeypatch):
    fixture = comparison()
    project = Mock()
    client = MagicMock()
    client.__enter__.return_value = client
    project.get_openai_client.return_value = client
    monkeypatch.setattr("evals.run_model_suite.preflight", lambda *_: [])
    collected = []

    def collect(candidate, composer, _actor_factory, _directory, *, on_row, decision_agents, supporting_recorders):
        assert composer.model == candidate and composer.agent_version == "1"
        assert set(decision_agents) == set(ROLES)
        assert all(agent.agent_version == "1" for agent in decision_agents.values())
        assert set(supporting_recorders) == {"gpt-counsellor"}
        collected.append(candidate)
        for row in fixture["rows"][candidate]:
            on_row(deepcopy(row))

    def score(_project, report, _output, judge, cards):
        assert judge == "gpt-counsellor" and set(cards) == set(fixture["rows"])
        report["summary"] = deepcopy(fixture["summary"])

    monkeypatch.setattr("evals.run_model_suite.collect_candidate", collect)
    monkeypatch.setattr("evals.run_model_suite.score_report", score)
    output = tmp_path / "report.json"
    report = {name: deepcopy(value) for name, value in fixture.items() if name not in ("rows", "summary")}
    report["rows"] = {}
    assert run_suite(project, suite(), output, report) == 0
    assert collected == ["gpt-counsellor", "gpt-compact", "gpt-modern"]
    assert load_json(output)["analysis"] == analyze(load_json(output), suite())
    project.get_openai_client.assert_called_once_with(max_retries=0, timeout=20)


def test_explicit_decision_version_never_changes_runtime_settings():
    old = settings.review_agent_version
    agent = FoundryDecisionAgent("answer-review", client=Mock(), agent_version="123")
    assert agent.agent_version == "123"
    assert settings.review_agent_version == old


def test_b3_b8_replay_uses_injected_decisions_and_same_candidate_for_review_and_repair(
        tmp_path, monkeypatch, local_counsellor, isolated_conversations):
    monkeypatch.setattr(settings.__class__, "run_dir", property(lambda _: tmp_path))
    monkeypatch.setattr(settings.__class__, "is_azure", property(lambda _: False))
    monkeypatch.setattr(model_comparison, "StrategyService", lambda **kwargs: local_strategy_service(
        kwargs["state_dir"], control_share=kwargs["control_share"]))
    monkeypatch.setattr(model_comparison, "run_cycle", run_cycle)
    monkeypatch.setattr(CustomerConversations, "azure", lambda *_: isolated_conversations)
    marker = Mock()
    supporting = UsageRecorder(Mock())

    class Composer:
        agent_name = "synthetic-fake"
        agent_version = "1"
        client = UsageRecorder(Mock())

        def draft(self, tools, _utterance, strategy):
            tools.trace.composition_source = "foundry"
            return json.dumps({
                "text": Counsellor._template_for(SimpleNamespace(tools=tools), strategy),
                "content_ids": list(tools.trace.content_versions),
            })

    composer = Composer()

    def review(counsellor, _utterance, _strategy, draft, *, allow_repair=True):
        assert counsellor._foundry_composer() is composer
        assert counsellor.decision_agents["answer-review"] is marker
        supporting.usage["input_tokens"] += 1
        return draft, SimpleNamespace(verdict="pass", concepts=["rights_transfer"], teach_back_concepts=["rights_transfer"])

    monkeypatch.setattr(Counsellor, "_review_answer", review)
    rows = collect_candidate(
        "gpt-counsellor", composer, lambda item: ScriptedPersona(asdict(item)), tmp_path,
        decision_agents={"answer-review": marker}, supporting_recorders={"gpt-counsellor": supporting},
    )
    assert len(rows) == 17
    assert all(row[name] for row in rows for name in FLOORS)
    assert any(row["journey_model_usage"]["gpt-counsellor"]["usage"]["input_tokens"] > 0 for row in rows)
