"""ILLUSTRATIVE - synthetic data. Semantic decisions are agents; invariants remain host-owned."""
import json
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from governedgraph.agents import decision_agents
from governedgraph.agents.decision_agents import (
    DecisionContext, DecisionUnavailable, FoundryDecisionAgent, agent_definition,
)
from governedgraph.common.config import settings
from governedgraph.common.contracts import (
    INTENT_DECISIONS, AnswerReview, ContractError, DecisionEvidence, ExplanationContext,
    IntentDecision, UnderstandingDecision,
)
from counsellor_doubles import install, new_session
from strategy_doubles import run_cycle
from strategy_doubles import local_strategy_service

EXPLAIN = {"route": "explanation", "intent": "transition_impact_question",
           "reason": "official_transition_information", "response_language": "en"}
CONTEXT = DecisionContext("identified", "unclassified", None)


def tools():
    return SimpleNamespace(journey=SimpleNamespace(journey_id="J-synthetic"), trace_parent="",
                           trace=SimpleNamespace(decision_evidence=[]))


@pytest.mark.parametrize("reason,language,route", [
    ("transition_overview", "en", "explanation"),
    ("personal_choice_or_comparison", "nl", "advice"),
    ("explicit_human_request", "en", "human"),
    ("transition_overview", None, "explanation"),
    (None, "en", None),
])
def test_decision_model_is_sole_intent_router(monkeypatch, reason, language, route):
    from governedgraph.agents import decision_model

    turn = tools()
    turn.conversation_context = lambda: []
    turn.trace.note = lambda *_: None
    requests = []

    def choose(_tools, state, questions):
        requests.append((state, set(questions)))
        if "reason" in questions:
            if reason is None:
                raise decision_model.DecisionModelUnavailable("synthetic transport failure")
            return {"reason": reason}
        if language is None:
            raise decision_model.DecisionModelAbstained("below minimum confidence")
        return {"response_language": language}

    monkeypatch.setattr(decision_model, "choose", choose)
    if route:
        decision = decision_agents.run_decision("intent", turn, "Synthetic request.", CONTEXT)
        # An unclear language reading keeps the established language and never blocks routing.
        expected_language = language or CONTEXT.response_language
        assert (decision.route, decision.reason, decision.response_language) == (route, reason, expected_language)
        assert turn.trace.decision_evidence == [
            DecisionEvidence("intent", "microsoft-decision-1.1", "1", asdict(decision)),
        ]
    else:
        with pytest.raises(DecisionUnavailable):
            decision_agents.run_decision("intent", turn, "Synthetic request.", CONTEXT)
        assert not turn.trace.decision_evidence
    # Each question is its own request; the language reading sees only the established language and the message.
    states = {next(iter(names)): state for state, names in requests}
    assert sorted(states) == ["reason", "response_language"]
    assert states["reason"] == "Customer message: Synthetic request."
    assert states["response_language"] == (
        f"Established language: {CONTEXT.response_language}\nCustomer message: Synthetic request.")


@pytest.mark.parametrize("verdict,expected", [("pass_draft", "pass"), ("advice", "fail"),
                                              ("cannot_tell", "unassessable"), (None, None)])
def test_decision_model_is_sole_draft_reviewer(monkeypatch, verdict, expected):
    from governedgraph.agents import counsellor, decision_model

    turn = tools()
    turn.trace.note = lambda *_: None
    turn.trace.model_rounds = 0
    turn.trace.composition_source = "foundry"
    turn.conversation_context = lambda: []
    session = SimpleNamespace(tools=turn, decision_agents={}, journey=SimpleNamespace(journey_id="J-review"))
    monkeypatch.setattr(FoundryDecisionAgent, "shared", lambda _: pytest.fail("legacy reviewer called"))
    monkeypatch.setattr(decision_agents, "review_payload", lambda *_: {"task": {"excluded_concepts": []}})
    def choose(_tools, _state, questions, optional=frozenset()):
        if verdict is None:
            raise decision_model.DecisionModelUnavailable("synthetic transport failure")
        return {
            key: verdict if key == "verdict" else "checked" if key == "concept_rights_transfer" else "absent"
            for key in questions
        }

    monkeypatch.setattr(decision_model, "choose", choose)
    draft = json.dumps({"text": "The arrangement changes.", "content_ids": []})
    if expected is None:
        with pytest.raises(counsellor.DependencyFailure, match="answer_review_agent"):
            counsellor.Counsellor._review_answer(session, "What changes?", None, draft)
        assert not turn.trace.decision_evidence
        return
    actual, review = counsellor.Counsellor._review_answer(
        session, "What changes?", SimpleNamespace(teach_back=True), draft,
    )
    assert review.verdict == expected
    assert review.teach_back_concepts == (["rights_transfer"] if expected == "pass" else [])
    assert actual == (draft if expected == "pass" else "")
    assert turn.trace.decision_evidence == [
        DecisionEvidence("answer-review", "microsoft-decision-1.1", "1", asdict(review)),
    ]


def test_b3_document_context_rejection_quotes_the_letter_clauses_for_the_one_bounded_repair(monkeypatch):
    from governedgraph.agents import decision_model
    from governedgraph.common.contracts import AnswerDraft

    turn = tools()
    turn.trace.note = lambda *_: None
    turn.trace.model_rounds = 0
    turn.conversation_context = lambda: []
    monkeypatch.setattr(decision_agents, "review_payload", lambda *_: {"task": {"excluded_concepts": []}})
    monkeypatch.setattr(decision_model, "choose", lambda _t, _s, questions, optional=frozenset(): {
        key: "document_context" if key == "verdict" else "absent" for key in questions})
    draft = AnswerDraft("Your amount is {{value:monthly_pension_after_transition}}. It is shown in your personal "
                        "transition letter. Adjustments can go up or down.", [])
    review = decision_agents.review_with_decision_model(turn, "Will it change?", SimpleNamespace(teach_back=False), draft)
    assert (review.verdict, review.issues) == ("revise", ["document_context"])
    assert turn.trace.review_feedback == [
        {"issue": "document_context", "quote": "It is shown in your personal transition letter."}]


@pytest.mark.parametrize("verdict,delivered", [("strategy", True), ("relevance", True), ("grounding", False)])
def test_b3_quality_only_review_keeps_gated_draft_while_safety_defects_do_not(monkeypatch, verdict, delivered):
    from governedgraph.agents import counsellor, decision_model

    turn = tools()
    turn.trace.note = lambda *_: None
    turn.trace.model_rounds = 0
    turn.trace.composition_source = "foundry"
    turn.conversation_context = lambda: []
    session = SimpleNamespace(tools=turn, decision_agents={}, journey=SimpleNamespace(journey_id="J-style"))
    monkeypatch.setattr(decision_agents, "review_payload", lambda *_: {"task": {"excluded_concepts": []}})
    monkeypatch.setattr(decision_model, "choose", lambda _t, _s, questions, optional=frozenset(): {
        key: verdict if key == "verdict" else "absent" for key in questions})
    draft = json.dumps({"text": "The arrangement changes.", "content_ids": []})
    actual, review = counsellor.Counsellor._review_answer(
        session, "What changes?", SimpleNamespace(teach_back=True), draft, allow_repair=False)
    assert (review.verdict, review.issues) == ("revise", [verdict])
    assert actual == (draft if delivered else "")
    assert turn.trace.composition_source == ("foundry" if delivered else "template_fallback")


def test_b7_decision_context_rejects_demographic_fields():
    with pytest.raises(ContractError):
        DecisionContext.from_dict({**asdict(CONTEXT), "age_band": "75+"})


def test_b1_b2_b7_intent_state_carries_only_situation_last_exchange_and_message(monkeypatch):
    from governedgraph.agents import decision_model

    states = []
    monkeypatch.setattr(decision_model, "choose", lambda _t, state, questions: (
        states.append(state), {"reason": "payment_continuity"} if "reason" in questions
        else {"response_language": "en"})[1])
    turn = tools()
    turn.trace.note = lambda *_: None
    turn.conversation_context = lambda: [{"role": "user", "text": "Oldest question"},
                                          {"role": "assistant", "text": "Oldest answer"},
                                          {"role": "user", "text": "Earlier question"},
                                          {"role": "assistant", "text": "Earlier answer"}]
    context = DecisionContext("comprehension_check", "transition_impact_question", "T-S5", "en",
                              ExplanationContext(["official_after_transition_amount"],
                                                 ["official_after_transition_amount"]))
    decision_agents.run_decision("intent", turn, "And afterwards?", context)
    assert states[0] == (
        "Context: the assistant has just asked the customer to say in their own words what they understood.\n"
        "Previous exchange (context only):\nCustomer: Earlier question\nAssistant: Earlier answer\n"
        "Customer message: And afterwards?")
    assert "MBR-" not in states[0] and "T-S5" not in states[0]
    # The language reading never sees the history.
    assert states[1] == "Established language: en\nCustomer message: And afterwards?"


def test_b3_b7_question_focus_preserves_old_audits_without_generic_new_native_outputs():
    from governedgraph.agents.foundry_runtime import ANSWER_GOALS
    from governedgraph.common.contracts import EXPLANATION_CONCEPTS

    assert IntentDecision.from_dict(EXPLAIN).reason == "official_transition_information"
    reasons = set(decision_agents._intent_criteria())
    assert "official_transition_information" not in reasons
    for reason, (_, excluded) in ANSWER_GOALS.items():
        decision = IntentDecision.from_dict({**EXPLAIN, "reason": reason})
        assert decision.intent == EXPLAIN["intent"] and decision.reason in reasons
        assert set(excluded) <= EXPLANATION_CONCEPTS
        with pytest.raises(ContractError):
            IntentDecision.from_dict({**asdict(decision), "age_band": "synthetic"})


@pytest.mark.parametrize("route,intent,reason", sorted(
    row for row in INTENT_DECISIONS if row[2] != "official_transition_information"))
def test_b3_b7_classification_derives_only_the_approved_matching_route(monkeypatch, route, intent, reason):
    from governedgraph.agents import decision_model

    monkeypatch.setattr(decision_model, "choose", lambda _t, _s, questions: (
        {"reason": reason} if "reason" in questions else {"response_language": "en"}))
    turn = tools()
    turn.conversation_context = lambda: []
    turn.trace.note = lambda *_: None
    decision = decision_agents.run_decision("intent", turn, "Synthetic question.", CONTEXT)
    assert asdict(decision) == {
        "route": route, "intent": intent, "reason": reason, "response_language": "en",
    }


@pytest.fixture
def counsellor(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.__class__, "run_dir", property(lambda _: tmp_path))
    install(monkeypatch, tmp_path)
    service = local_strategy_service(tmp_path / "strategy", control_share=0)
    run_cycle(service)
    c = new_session("MBR-7Q2K", strategy_service=service, journey_id="J-semantic")
    c.start()
    return c


def test_b5_understanding_agent_meaning_verdict_resolves_without_expected_phrases_and_is_linked(counsellor):
    first = counsellor.handle("Will the transition change what I receive?")
    contexts = []

    def decide(bound_tools, utterance, context):
        contexts.append(context)
        return IntentDecision("comprehension", "unclassified", "restating_previous_explanation")

    counsellor.decision_agents["intent"] = SimpleNamespace(run=decide)

    def assess(bound_tools, utterance, context):
        decision = UnderstandingDecision("understood", "meaning_correct")
        bound_tools.trace.decision_evidence.append(
            DecisionEvidence("understanding", "microsoft-decision-1.1", "1", asdict(decision)),
        )
        return decision
    counsellor.decision_agents["understanding"] = SimpleNamespace(run=assess)
    result = counsellor.handle("Conversion preserves the starting position; later results can alter it.")
    assert not result.escalated and result.state == "resolved"
    assert "understanding-agent" in result.workflow_steps and "explain-with-approved-tools" not in result.workflow_steps
    assert contexts[0].previous_template_id == "T-S5"
    record = counsellor.tools.store.read_all()[-1]
    assert record["decision_evidence"][-1]["decision"]["outcome"] == "understood"
    event = counsellor.journey.events[-1]
    assert event.response_id == record["response_id"] and event.outcome == "comprehension_check"
    assert event.strategy_id == first.strategy_id


def test_b5_new_question_during_teachback_is_not_recorded_as_failed_understanding(counsellor):
    counsellor.handle("Will the transition change what I receive?")
    result = counsellor.handle("Please explain my transition letter.")
    assert not result.escalated and counsellor.journey.teach_back_failures == 0
    assert "understanding-agent" not in result.workflow_steps
    assert not any(event.event == "teach_back_failed" for event in counsellor.journey.events)


def test_b3_b9_missing_signed_ranking_takes_explicit_technical_branch_not_advice(counsellor, monkeypatch):
    ranked = counsellor.tools.get_ranked_strategies

    def unavailable_snapshot():
        response = replace(ranked(), source="expert_priors_fallback")
        counsellor.tools.trace.ranked = response
        return response

    monkeypatch.setattr(counsellor.tools, "get_ranked_strategies", unavailable_snapshot)
    result = counsellor.handle("Will the transition change what I receive?")
    assert result.escalated and result.strategy_id == "S6"
    assert "service-failure-handoff" in result.workflow_steps
    assert "personal-advice-handoff" not in result.workflow_steps
    assert "advice" not in result.text.lower()
    assert len(counsellor.tools.store.read_all()) == 1
    assert counsellor.tools.trace.escalations[-1].reason_code == "component_failure"


def test_b5_uncertain_understanding_clarifies_without_inventing_success_or_penalizing_member(counsellor):
    counsellor.handle("Will the transition change what I receive?")
    counsellor.decision_agents["intent"] = SimpleNamespace(run=lambda *_: IntentDecision(
        "comprehension", "unclassified", "restating_previous_explanation",
    ))
    counsellor.decision_agents["understanding"] = SimpleNamespace(run=lambda *_: UnderstandingDecision(
        "uncertain", "insufficient_evidence",
    ))
    result = counsellor.handle("Okay.")
    assert not result.escalated and result.state == "comprehension_check"
    assert "clarify-question" in result.workflow_steps
    assert counsellor.journey.teach_back_failures == 0
    assert not any(event.outcome == "comprehension_check" for event in counsellor.journey.events)


def test_b9_ambiguous_request_clarifies_without_advice_claim_or_handover(counsellor):
    result = counsellor.handle("What about that?")
    assert not result.escalated and "clarify-question" in result.workflow_steps
    assert "can't advise" not in result.text and counsellor.tools.trace.values is None


def test_b9_each_technical_failure_has_one_checked_response_not_two_mutated_edge_outputs(counsellor):
    def unavailable(*_):
        raise DecisionUnavailable("synthetic outage")
    counsellor.decision_agents["intent"] = SimpleNamespace(run=unavailable)
    result = counsellor.handle("Will this change impact my monthly payments?")
    assert result.escalated
    assert result.workflow_steps == ["screen-input", "intent-agent", "service-failure-handoff",
                                     "commit-checked-evidence"]
    assert len(counsellor.tools.store.read_all()) == 1
    assert counsellor.tools.trace.escalations[-1].reason_code == "component_failure"


def _gateway_403(headers=None, message="denied"):
    import httpx
    from openai import PermissionDeniedError

    request = httpx.Request("POST", "https://apim.invalid/foundry/openai/v1/responses")
    response = httpx.Response(403, request=request, headers=headers or {}, json={"error": {"message": message}})
    return PermissionDeniedError(message, response=response, body={"message": message})


def _intent_agent_failing_with(error):
    def unavailable(*_):
        raise DecisionUnavailable("intent agent unavailable") from error
    return SimpleNamespace(run=unavailable)


def test_b9_exhausted_conversation_token_quota_escalates_as_conversation_limit_not_outage(counsellor):
    quota = _gateway_403({"X-GG-Limit": "conversation-token-quota"})
    counsellor.decision_agents["intent"] = _intent_agent_failing_with(quota)
    result = counsellor.handle("Yes, I want this confirmation.")
    assert result.escalated and result.strategy_id == "S6"
    assert result.workflow_steps == ["screen-input", "intent-agent", "service-failure-handoff",
                                     "commit-checked-evidence"]
    assert "limit" in result.text and "unavailable" not in result.text
    assert counsellor.tools.trace.escalations[-1].reason_code == "conversation_limit"
    assert len(counsellor.tools.store.read_all()) == 1


def test_b9_other_gateway_denials_stay_component_failures(counsellor):
    counsellor.decision_agents["intent"] = _intent_agent_failing_with(
        _gateway_403(message="caller is not allowed"))
    result = counsellor.handle("Yes, I want this confirmation.")
    assert result.escalated and "unavailable" in result.text
    assert counsellor.tools.trace.escalations[-1].reason_code == "component_failure"


@pytest.mark.parametrize("version", ["1.1", "1.2"])
def test_b4_legacy_audit_outbox_retries_remain_byte_exact_without_rewriting_history(counsellor, version):
    from azure.core.exceptions import ResourceExistsError
    from governedgraph.common.contracts import ExplainabilityRecord
    from governedgraph.explainability.store import BlobExplainabilityStore, encoded

    counsellor.handle("Will the transition change what I receive?")
    old = counsellor.tools.store.read_all()[-1]
    old["contract_version"] = version
    if version == "1.1":
        old.pop("decision_evidence")
    else:
        for item in old["decision_evidence"]:
            item["decision"].pop("response_language", None)
    raw = json.dumps(old, sort_keys=True).encode()
    parsed = ExplainabilityRecord.from_dict(old)
    assert encoded(parsed).encode() == raw
    uploaded = []
    def exists(payload, **kwargs):
        uploaded.append(payload)
        raise ResourceExistsError("synthetic existing immutable audit")
    blob = SimpleNamespace(upload_blob=exists, download_blob=lambda: SimpleNamespace(readall=lambda: raw))
    client = SimpleNamespace(get_container_client=lambda _: SimpleNamespace(get_blob_client=lambda _: blob))
    BlobExplainabilityStore("https://synthetic.invalid", "records", client=client).append(parsed)
    assert uploaded == [raw]


@pytest.mark.parametrize("payload", [
    {"concepts": ["age"], "teach_back_concepts": []},
    {"concepts": [], "teach_back_concepts": ["future_adjustments"]},
    {"concepts": ["rights_transfer", "rights_transfer"], "teach_back_concepts": []},
    {"concepts": [], "teach_back_concepts": [], "gender": "synthetic"},
    {"concepts": [], "teach_back_concepts": [], "text": "a raw conversation or figure"},
])
def test_b4_b7_closed_explanation_memory_rejects_demographics_prose_and_untaught_targets(payload):
    with pytest.raises(ContractError):
        ExplanationContext.from_dict(payload)


@pytest.mark.parametrize("patch", [
    {"verdict": "pass", "issues": ["grounding"]},
    {"verdict": "revise", "issues": []},
    {"verdict": "fail", "concepts": ["rights_transfer"]},
    {"verdict": "unassessable", "issues": ["grounding"], "teach_back_concepts": ["rights_transfer"]},
    {"verdict": "pass", "text": "Free-form judge output is prohibited"},
    {"verdict": "pass", "issues": ["age"]},
    {"verdict": "pass", "concepts": ["official_source"], "teach_back_concepts": ["future_adjustments"]},
])
def test_b2_b7_judge_contract_is_closed_and_cannot_turn_a_nonpass_into_taught_memory(patch):
    with pytest.raises(ContractError):
        AnswerReview.from_dict({"verdict": "pass", "issues": [], "concepts": [], "teach_back_concepts": [], **patch})


def test_b3_judge_is_a_pinned_tool_free_separate_agent_using_existing_model():
    definition = agent_definition("answer-review").as_dict()
    schema = definition["text"]["format"]["schema"]
    assert definition["model"] == settings.model_deployment and definition["tools"] == []
    assert definition["temperature"] == 0.0 and schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "checks", "evidence_status", "feedback", "concepts", "teach_back_concepts"}
    assert "cited source bodies" in decision_agents.INSTRUCTIONS["answer-review"]
    assert set(decision_agents.INSTRUCTIONS) == {"answer-review"}


def test_b9_personal_choice_and_factual_objection_criteria_stay_distinct():
    criteria = decision_agents._intent_criteria()
    assert "whether the customer should take an action" in criteria["personal_choice_or_comparison"]
    assert "not whether the customer should" in criteria["transition_information_gap"]
    assert "is not a request to decide" in criteria["personal_choice_or_comparison"]


def test_b3_audit_accepts_only_one_bounded_repair_and_preserves_unique_routing_evidence(counsellor):
    from governedgraph.common.contracts import ExplainabilityRecord

    counsellor.handle("Will the transition change what I receive?")
    record = counsellor.tools.store.read_all()[-1]
    def evidence(verdict):
        return asdict(DecisionEvidence("answer-review", "gg-answer-review-agent", "1", {
            "verdict": verdict, "issues": [] if verdict == "pass" else ["relevance"],
            "concepts": [], "teach_back_concepts": [],
        }))
    original = list(record["decision_evidence"])
    record["decision_evidence"] = original + [evidence("revise"), evidence("pass")]
    assert len(ExplainabilityRecord.from_dict(record).decision_evidence) == len(original) + 2
    for rejected in (
        original + [evidence("pass"), evidence("pass")],
        original + [evidence("revise"), evidence("revise"), evidence("pass")],
        original + original,
    ):
        with pytest.raises(ContractError):
            ExplainabilityRecord.from_dict({**record, "decision_evidence": rejected})
