"""ILLUSTRATIVE - synthetic data. Unavailable decisions remain observable and fail closed."""
import json
import logging
from types import SimpleNamespace

import httpx
import pytest

from governedgraph.agents import decision_agents, decision_model, rate_limit
from governedgraph.agents.tools import Trace
from governedgraph.common.contracts import AnswerDraft, ExplanationContext
from governedgraph.common.operational_logging import OperationalFormatter
from governedgraph.voice.voice_live_bridge import VoiceReply


@pytest.mark.parametrize("scenario,failure,usage_event", [
    ("accepted", None, "model_usage"),
    ("low_confidence", "low_confidence", "model_usage"),
    ("missing_choice", "answer_choice", "model_usage"),
    ("http_failure", "http_status", "model_usage_missing"),
    ("rate_limited", None, "model_usage"),
    ("retry_exhausted", "http_status", "model_usage_missing"),
    ("retry_budget", "http_status", "model_usage_missing"),
])
def test_decision_failure_diagnostics_preserve_usage_without_payload(monkeypatch, scenario, failure, usage_event):
    body = {"model": "microsoft-decision-1.1", "usage": {"input_tokens": 12, "output_tokens": 1},
            "answers": {"verdict": {"type": "choice", "choice": "pass_draft",
                                     "probabilities": {"pass_draft": 0.51}}}}
    if scenario == "low_confidence":
        body["answers"]["verdict"]["probabilities"]["pass_draft"] = 0.3
    elif scenario == "missing_choice":
        del body["answers"]["verdict"]["choice"]
    response = httpx.Response(429 if scenario in {"http_failure", "retry_exhausted", "retry_budget"} else 200,
                              headers={"Retry-After": "60" if scenario == "http_failure" else "0"}, json=body,
                              request=httpx.Request("POST", "https://synthetic.example/"))
    monkeypatch.setattr(decision_model, "settings", SimpleNamespace(
        project_endpoint="https://synthetic.services.ai.azure.com/api/projects/synthetic",
        decision_deployment="decision-v1"))
    monkeypatch.setattr(decision_model.connections, "credential", lambda: SimpleNamespace(
        get_token=lambda *_: SimpleNamespace(token="synthetic-private-token")))
    calls, waits = [], []
    def post(*_args, **_kwargs):
        calls.append(True)
        if scenario == "rate_limited" and len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, request=response.request)
        return response
    monkeypatch.setattr(decision_model.connections, "http_client", lambda: SimpleNamespace(post=post))
    monkeypatch.setattr(decision_model, "create_with_retry", lambda create, tools:
                        rate_limit.create_with_retry(create, tools, sleep=waits.append))
    events, errors = [], []
    monkeypatch.setattr(decision_model, "emit", lambda name, **fields: events.append((name, fields)))
    monkeypatch.setattr(decision_model.log, "error", lambda name, *, extra: errors.append({"msg": name, **extra}))
    turn = SimpleNamespace(journey=SimpleNamespace(journey_id="J-synthetic"), trace_parent="", trace=Trace())
    if scenario == "retry_budget":
        turn.trace.rate_limit_wait = rate_limit.TURN_WAIT_BUDGET_SECONDS
    questions = {"verdict": {"type": "choice", "criteria": {"pass_draft": "Approved."}}}
    if failure:
        with pytest.raises(decision_model.DecisionModelUnavailable) as raised:
            decision_model.choose(turn, {"draft": "synthetic-private-text"}, questions)
        assert isinstance(raised.value, decision_model.DecisionModelAbstained) is (failure == "low_confidence")
        record = json.loads(OperationalFormatter().format(logging.makeLogRecord(errors[0])))
        assert record["failure_domain"] == failure
        assert record["http_status"] == response.status_code
        if scenario not in {"http_failure", "retry_exhausted", "retry_budget"}:
            assert record["decision_question"] == "verdict"
        assert "synthetic-private" not in json.dumps(record)
        if failure == "low_confidence":
            # The closed label and its score are content-free and show how close the decision was to the gate.
            assert (record["top_choice"], record["top_probability"]) == ("pass_draft", 0.3)
            assert "runner_up" not in record
        else:
            assert "pass_draft" not in json.dumps(record)
    else:
        assert decision_model.choose(turn, {}, questions) == {"verdict": "pass_draft"}
        assert not errors
    assert [name for name, _ in events] == [usage_event]
    assert len(calls) == (2 if scenario in {"rate_limited", "retry_exhausted"} else 1)
    assert waits == ([1.5] if scenario in {"rate_limited", "retry_exhausted"} else [])


@pytest.mark.parametrize("role", ["intent", "answer-review"])
def test_unavailable_decision_evidence_does_not_block_protective_handover(monkeypatch, role):
    def unavailable(*_):
        raise decision_model.DecisionModelUnavailable("synthetic transport failure")

    monkeypatch.setattr(decision_model, "choose", unavailable)
    turn = SimpleNamespace(trace=Trace(), conversation_context=lambda: [],
                           journey=SimpleNamespace(journey_id="J-unavailable", response_language="en"))
    with pytest.raises(decision_agents.DecisionUnavailable):
        if role == "intent":
            decision_agents.run_decision(
                role, turn, "Synthetic question.",
                decision_agents.DecisionContext("identified", "unclassified", None))
        else:
            monkeypatch.setattr(decision_agents, "_excluded_concepts", lambda *_: [])
            decision_agents.review_with_decision_model(
                turn, "Synthetic question.", SimpleNamespace(teach_back=False), AnswerDraft("Synthetic explanation.", []))
    assert VoiceReply(text="A person can help.", state="escalation_requested", turn_trace=turn.trace.turn_trace)


@pytest.mark.parametrize("scenario,expected", [
    ("understood", ("understood", "meaning_correct")),
    ("needs_explanation", ("needs_explanation", "meaning_not_yet_understood")),
    ("uncertain", ("uncertain", "insufficient_evidence")),
    ("abstained", ("uncertain", "insufficient_evidence")),
    ("unavailable", None),
])
def test_b5_understanding_runs_on_the_decision_model_and_abstention_is_uncertain_not_handover(
        monkeypatch, scenario, expected):
    def choose(_tools, state, questions):
        assert set(questions) == {"outcome"}
        assert set(questions["outcome"]["criteria"]) == {"understood", "needs_explanation", "uncertain"}
        # Plain text: only what was taught (described by the pack) and the reply; no history, no JSON.
        assert state.endswith("Customer reply: Synthetic reply.") and "- Built-up pension rights" in state
        if scenario == "abstained":
            raise decision_model.DecisionModelAbstained("below minimum confidence")
        if scenario == "unavailable":
            raise decision_model.DecisionModelUnavailable("synthetic transport failure")
        return {"outcome": scenario}

    monkeypatch.setattr(decision_model, "choose", choose)
    turn = SimpleNamespace(trace=Trace(), conversation_context=lambda: [])
    taught = ExplanationContext(["rights_transfer"], ["rights_transfer"])
    context = decision_agents.DecisionContext("comprehension_check", "unclassified", None, "en", taught)
    if expected is None:
        with pytest.raises(decision_agents.DecisionUnavailable):
            decision_agents.run_decision("understanding", turn, "Synthetic reply.", context)
        assert turn.trace.turn_trace == [{"stage": "outcome", "result": "understanding_unavailable", "issues": []}]
        assert VoiceReply(text="A person can help.", state="escalation_requested", turn_trace=turn.trace.turn_trace)
        return
    decision = decision_agents.run_decision("understanding", turn, "Synthetic reply.", context)
    assert (decision.outcome, decision.reason) == expected
    assert [(item.role, item.agent_name) for item in turn.trace.decision_evidence] == [
        ("understanding", "microsoft-decision-1.1")]


def test_optional_unclear_answer_is_none_while_a_required_one_still_fails_closed(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="governedgraph.agents.decision_model")
    body = {"model": "microsoft-decision-1.1", "usage": {"input_tokens": 12, "output_tokens": 1},
            "answers": {
                "required": {"type": "choice", "choice": "a", "probabilities": {"a": 0.9, "b": 0.1}},
                "optional": {"type": "choice", "choice": "taught",
                             "probabilities": {"taught": 0.497, "absent": 0.336, "checked": 0.167}},
            }}
    response = httpx.Response(200, json=body, request=httpx.Request("POST", "https://synthetic.example/"))
    monkeypatch.setattr(decision_model, "settings", SimpleNamespace(
        project_endpoint="https://synthetic.services.ai.azure.com/api/projects/synthetic",
        decision_deployment="decision-v1"))
    monkeypatch.setattr(decision_model.connections, "credential", lambda: SimpleNamespace(
        get_token=lambda *_: SimpleNamespace(token="synthetic-private-token")))
    monkeypatch.setattr(decision_model.connections, "http_client",
                        lambda: SimpleNamespace(post=lambda *_a, **_k: response))
    monkeypatch.setattr(decision_model, "emit", lambda *_a, **_k: None)
    turn = SimpleNamespace(journey=SimpleNamespace(journey_id="J-synthetic"), trace_parent="", trace=Trace())
    questions = {"required": {"type": "choice", "criteria": {"a": "A", "b": "B"}},
                 "optional": {"type": "choice", "criteria": {"taught": "T", "absent": "N", "checked": "C"}}}
    assert decision_model.choose(turn, "state", questions, frozenset({"optional"})) == {
        "required": "a", "optional": None}
    record = next(r for r in caplog.records if r.getMessage() == "decision_choice_unclear")
    assert (record.decision_question, record.top_choice, record.runner_up) == ("optional", "taught", "absent")
    with pytest.raises(decision_model.DecisionModelAbstained):
        decision_model.choose(turn, "state", questions)


def test_b5_unclear_concept_reading_never_vetoes_an_approved_answer(monkeypatch):
    seen = {}

    def choose(_tools, _state, questions, optional=frozenset()):
        if "verdict" in questions:
            return {"verdict": "pass_draft"}
        if "language" in questions:
            return {"language": "expected_language"}
        seen["optional"] = optional
        return {key: ("checked" if key == "concept_rights_transfer"
                      else None if key == "concept_official_after_transition_amount" else "absent")
                for key in questions}

    monkeypatch.setattr(decision_model, "choose", choose)
    turn = SimpleNamespace(trace=Trace(), journey=SimpleNamespace(journey_id="J-review", response_language="en"),
                           conversation_context=lambda: [])
    review = decision_agents.review_with_decision_model(
        turn, "Synthetic question.", SimpleNamespace(teach_back=True), AnswerDraft("Synthetic explanation?", []))
    # The concept the judge could not read is simply not recorded as taught; the verdict stands.
    assert (review.verdict, review.concepts, review.teach_back_concepts) == (
        "pass", ["rights_transfer"], ["rights_transfer"])
    assert seen["optional"] and all(key.startswith("concept_") for key in seen["optional"])


@pytest.fixture(autouse=True)
def no_excluded_concepts(monkeypatch):
    monkeypatch.setattr(decision_agents, "_excluded_concepts", lambda *_: [])


@pytest.mark.parametrize("verdict", ["off_target", "pass_draft"])
def test_relevance_revision_is_told_which_excluded_concept_was_taught(monkeypatch, verdict):
    def choose(_tools, _state, questions, optional=frozenset()):
        if "verdict" in questions:
            return {"verdict": verdict}
        if "language" in questions:
            return {"language": "expected_language"}
        return {key: ("taught" if key in {"concept_official_source", "concept_future_adjustments"} else "absent")
                for key in questions}

    monkeypatch.setattr(decision_model, "choose", choose)
    monkeypatch.setattr(decision_agents, "_excluded_concepts", lambda *_: ["rights_transfer", "official_source"])
    turn = SimpleNamespace(trace=Trace(), journey=SimpleNamespace(journey_id="J-review", response_language="en"),
                           conversation_context=lambda: [])
    review = decision_agents.review_with_decision_model(
        turn, "Synthetic question.", SimpleNamespace(teach_back=False), AnswerDraft("Synthetic explanation.", []))
    assert (review.verdict, review.issues) == ("revise", ["relevance"])
    assert [(item["issue"], item["excluded_concept"]) for item in turn.trace.review_feedback] == [
        ("relevance", "official_source")]


def test_b5_nothing_taught_means_uncertain_without_a_model_call(monkeypatch):
    monkeypatch.setattr(decision_model, "choose", lambda *_: pytest.fail("no model call is needed"))
    turn = SimpleNamespace(trace=Trace(), conversation_context=lambda: [])
    context = decision_agents.DecisionContext("comprehension_check", "unclassified", None)
    decision = decision_agents.run_decision("understanding", turn, "Yes, I understand.", context)
    assert (decision.outcome, decision.reason) == ("uncertain", "insufficient_evidence")


@pytest.mark.parametrize("change", [("drop", "advice"), ("drop", "off_target"), ("add", "cannot_tell")])
def test_decision_criteria_must_describe_exactly_the_closed_review_labels(tmp_path, change):
    from governedgraph.common import domain
    from governedgraph.common.config import DATA_DIR

    action, label = change
    vocabulary = json.loads((DATA_DIR / "catalogue" / "vocabulary_v1.json").read_text())
    review = vocabulary["decision_criteria"]["review"]
    if action == "drop":
        del review[label]
    else:
        review[label] = "An extra label the host does not know."
    (tmp_path / "catalogue").mkdir()
    (tmp_path / "catalogue" / "vocabulary_v1.json").write_text(json.dumps(vocabulary))
    with pytest.raises(domain.DomainPackError, match="review"):
        domain.vocabulary(tmp_path)


def test_decision_criteria_must_describe_exactly_the_closed_reason_set(monkeypatch):
    from governedgraph.common import domain

    vocabulary = domain.vocabulary()
    broken = {**vocabulary, "decision_criteria": {
        **vocabulary["decision_criteria"],
        "intent": {k: v for k, v in vocabulary["decision_criteria"]["intent"].items() if k != "payment_continuity"},
    }}
    monkeypatch.setattr(domain, "vocabulary", lambda *_: broken)
    decision_agents._pack_criteria.cache_clear()
    decision_agents._intent_criteria.cache_clear()
    try:
        with pytest.raises(domain.DomainPackError, match="decision_criteria"):
            decision_agents._intent_criteria()
    finally:
        decision_agents._pack_criteria.cache_clear()
        decision_agents._intent_criteria.cache_clear()


def test_a_routing_reading_at_or_below_the_gate_abstains_whatever_its_margin(monkeypatch):
    # A clear but sub-gate lead used to be accepted by a margin rule that only routing had; there is one gate now.
    body = {"model": "microsoft-decision-1.1", "usage": {"input_tokens": 12, "output_tokens": 1},
            "answers": {"route": {"type": "choice", "choice": "asks_information", "probabilities": {
                "asks_information": 0.25, "only_acknowledges": 0.2, "asks_choice": 0.02, "asks_person": 0.0004}}}}
    response = httpx.Response(200, json=body, request=httpx.Request("POST", "https://synthetic.example/"))
    monkeypatch.setattr(decision_model, "settings", SimpleNamespace(
        project_endpoint="https://synthetic.services.ai.azure.com/api/projects/synthetic",
        decision_deployment="decision-v1"))
    monkeypatch.setattr(decision_model.connections, "credential", lambda: SimpleNamespace(
        get_token=lambda *_: SimpleNamespace(token="synthetic-private-token")))
    monkeypatch.setattr(decision_model.connections, "http_client",
                        lambda: SimpleNamespace(post=lambda *_a, **_k: response))
    monkeypatch.setattr(decision_model, "emit", lambda *_a, **_k: None)
    turn = SimpleNamespace(journey=SimpleNamespace(journey_id="J-synthetic"), trace_parent="", trace=Trace())
    questions = {"route": {"type": "choice", "criteria": {
        "asks_information": "A", "only_acknowledges": "B", "asks_choice": "C", "asks_person": "D"}}}
    with pytest.raises(decision_model.DecisionModelAbstained):
        decision_model.choose(turn, "state", questions)