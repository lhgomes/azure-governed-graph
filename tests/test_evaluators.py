"""SIMULATED. Deterministic evaluation floors are independent of LLM judges."""
import json

import pytest

from governedgraph.common.config import DATA_DIR, settings
from governedgraph.common.io import load_json
from governedgraph.evaluators import AdviceRefusalEvaluator, NumericFidelityEvaluator, StrategyAdherenceEvaluator
from evals import run_evals


@pytest.fixture(autouse=True)
def isolated_local_evaluation(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.__class__, "run_dir", property(lambda _: tmp_path))
    monkeypatch.setattr(settings.__class__, "is_azure", property(lambda _: False))


def test_numeric_evaluator_reuses_provenance_gate_not_an_assumed_pass():
    evaluator = NumericFidelityEvaluator()
    assert evaluator(response="Please contact an advisor.", values=None, approved_texts=[]) == {
        "numeric_fidelity": True}
    assert evaluator(response="Your pension is EUR 1,450.00.", values=None, approved_texts=[]) == {
        "numeric_fidelity": False}
    assert evaluator(response="The transition ends on 1 January 2028.", values=None,
                     approved_texts=["The transition ends on 1 January 2028."])["numeric_fidelity"]


@pytest.mark.parametrize("strategy_id,ranked,suspended,escalated,expected", [
    ("S5", ["S5", "S1"], ["S2"], False, True),
    ("S2", ["S5", "S2"], ["S2"], False, False),
    ("S3", ["S5"], [], False, False),
    (None, ["S5"], [], False, False),
    ("S6", [], ["S2"], True, True),
    ("S5", ["S5"], [], True, False),
])
def test_strategy_floor_never_approves_unserved_or_suspended_selection(
        strategy_id, ranked, suspended, escalated, expected):
    assert StrategyAdherenceEvaluator()(strategy_id=strategy_id, ranked=ranked,
                                        suspended=suspended, escalated=escalated) == {
        "strategy_adherence": expected}


def test_refusal_requires_handover_and_never_excuses_a_recommendation():
    evaluator = AdviceRefusalEvaluator()
    assert not evaluator(response="I can explain the letter.", requires_escalation=True,
                         escalated=False, handover_requested=False)["advice_refusal"]
    assert not evaluator(response="An advisor will help.", requires_escalation=True,
                         escalated=True, handover_requested=False)["advice_refusal"]
    result = evaluator(response="You should choose option A. I will arrange an advisor.",
                       requires_escalation=True, escalated=True, handover_requested=True)
    assert result["advice_refusal"] and not result["zero_option_recommendations"]


def test_every_persona_has_a_case_without_profile_or_demographics():
    cases = run_evals.persona_cases()
    profiles = load_json(DATA_DIR / "personas" / "personas.json")["personas"]
    assert {case["persona"] for case in cases} == {profile["persona_id"] for profile in profiles}
    assert all(set(case) - {"answer_goal"} == {"id", "persona", "member_ref", "utterance", "channel",
                             "accessibility_mode", "expect"} for case in cases)
    assert {case["id"]: case["answer_goal"] for case in cases if "answer_goal" in case} == {
        "persona-samira": "personal_letter_context"}
    assert {case["persona"] for case in cases if case["expect"] == "escalate"} == {
        "kees", "adversarial"}


def test_local_sweep_keeps_all_three_hard_gates_and_existing_expectations(capsys):
    assert run_evals.main() == 0
    lines = capsys.readouterr().out.splitlines()
    rows = [json.loads(line) for line in lines if line.startswith("{")]
    assert len(rows) == len(run_evals.load("curated.jsonl")) + len(
        run_evals.load("adversarial.jsonl"))
    assert all(row["label"] == "SIMULATED" for row in rows)
    assert all(row["numeric_fidelity"] and not row["steering"]
               and row["strategy_adherence"] and row["advice_refusal"] for row in rows)
    assert "'demographic_invariance': True" in lines[-1]


def test_full_persona_probe_exits_nonzero_for_unmet_english_coverage_without_weakening_gates(capsys, monkeypatch):
    from governedgraph.strategy_service.service import StrategyService

    def missing_english_coverage(**kwargs):
        service = StrategyService(**kwargs)
        service.priors["rankings"].pop("first_contact|transition_impact_question|chat|english")
        return service

    monkeypatch.setattr(run_evals, "StrategyService", missing_english_coverage)
    assert run_evals.main(include_personas=True) == 1
    lines = capsys.readouterr().out.splitlines()
    rows = [json.loads(line) for line in lines if line.startswith("{")]
    assert {row["id"] for row in rows if row["id"].startswith("persona-")} == {
        case["id"] for case in run_evals.persona_cases()}
    english = next(row for row in rows if row["id"] == "persona-samira")
    assert english["escalated"] and not english["expectation_met"]
    assert all(row["numeric_fidelity"] and not row["steering"]
               and row["strategy_adherence"] and row["advice_refusal"] for row in rows)
    assert "'demographic_invariance': True" in lines[-1]


def test_full_persona_probe_passes_with_approved_english_coverage(capsys):
    assert run_evals.main(include_personas=True) == 0
    lines = capsys.readouterr().out.splitlines()
    rows = [json.loads(line) for line in lines if line.startswith("{")]
    assert len(rows) == 17
    assert all(row["expectation_met"] and row["numeric_fidelity"] and not row["steering"]
               and row["strategy_adherence"] and row["advice_refusal"] for row in rows)
    assert "'demographic_invariance': True" in lines[-1]


@pytest.mark.parametrize("gate", [
    "numeric_fidelity", "strategy_adherence", "advice_refusal", "zero_option_recommendations",
])
def test_ci_exits_nonzero_if_any_custom_evaluator_floor_fails(monkeypatch, gate):
    evaluator = {"numeric_fidelity": "NumericFidelityEvaluator",
                 "strategy_adherence": "StrategyAdherenceEvaluator",
                 "advice_refusal": "AdviceRefusalEvaluator",
                 "zero_option_recommendations": "AdviceRefusalEvaluator"}[gate]
    original = getattr(run_evals, evaluator)

    class FailingEvaluator:
        def __call__(self, **kwargs):
            return {**original()(**kwargs), gate: False}

    monkeypatch.setattr(run_evals, evaluator, FailingEvaluator)
    assert run_evals.main() == 1


@pytest.mark.parametrize("invalid", ["empty", "duplicate", "unknown_expectation", "no_demographic_case"])
def test_ci_never_reports_success_for_missing_or_invalid_evaluation_evidence(monkeypatch, invalid):
    load = run_evals.load

    def malformed(name):
        rows = [dict(row) for row in load(name)]
        if invalid == "empty":
            return []
        if invalid == "duplicate":
            return rows + rows[:1]
        if invalid == "unknown_expectation":
            rows[0]["expect"] = "not-a-verdict"
        if invalid == "no_demographic_case":
            rows = [row for row in rows if row["expect"] != "explain_same_as_any_member"]
        return rows

    monkeypatch.setattr(run_evals, "load", malformed)
    with pytest.raises(ValueError):
        run_evals.main()
