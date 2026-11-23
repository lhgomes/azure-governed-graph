"""ILLUSTRATIVE - synthetic data. Semantic decisions are agents; invariants remain host-owned."""
import json
import logging
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from governedgraph.agents import decision_agents
from governedgraph.agents.decision_agents import DecisionContext, DecisionUnavailable
from governedgraph.common.config import settings
from governedgraph.common.contracts import (
    INTENT_DECISIONS, AnswerReview, ContractError, DecisionEvidence, ExplanationContext,
    HISTORICAL_REASONS, IntentDecision, UnderstandingDecision,
)
from counsellor_doubles import install, new_session
from strategy_doubles import run_cycle
from strategy_doubles import local_strategy_service

EXPLAIN = {"route": "explanation", "intent": "transition_impact_question",
           "reason": "official_transition_information", "response_language": "en"}
CONTEXT = DecisionContext("identified", "unclassified", None)


@pytest.fixture(autouse=True)
def no_excluded_concepts(monkeypatch):
    monkeypatch.setattr(decision_agents, "_excluded_concepts", lambda *_: [])


def tools():
    return SimpleNamespace(journey=SimpleNamespace(journey_id="J-synthetic"), trace_parent="",
                           trace=SimpleNamespace(decision_evidence=[]))


def review_tools(*, history=(), values=(), sources=()):
    turn = tools()
    turn.journey.response_language = "en"
    turn.trace.note = lambda *_: None
    turn.trace.model_rounds = 0
    turn.trace.composition_source = "foundry"
    turn.trace.values = SimpleNamespace(values=list(values))
    turn.trace.content_versions = [item[0] for item in sources]
    turn.trace.content_texts = [item[1] for item in sources]
    turn.conversation_context = lambda: list(history)
    return turn


def reading(verdict, *, checked="concept_rights_transfer"):
    def choose(_tools, _state, questions, optional=frozenset()):
        return {key: verdict if key == "verdict" else "checked" if key == checked else "absent" for key in questions}
    return choose


@pytest.mark.parametrize("route,topic,expected", [
    ("asks_information", "transition_overview", ("explanation", "transition_overview")),
    ("asks_choice", "transition_overview", ("advice", "personal_choice_or_comparison")),
    ("asks_person", "transition_overview", ("human", "explicit_human_request")),
    ("asks_person", "fail", ("human", "explicit_human_request")),
    ("only_acknowledges", "payment_continuity", ("comprehension", "restating_previous_explanation")),
    ("abstain", "transition_overview", ("clarification", "more_context_needed")),
    ("asks_information", "abstain", ("clarification", "more_context_needed")),
    ("fail", "transition_overview", None),
    ("asks_information", "fail", None),
])
def test_decision_model_is_sole_intent_router(monkeypatch, route, topic, expected):
    from governedgraph.agents import decision_model

    turn = tools()
    turn.conversation_context = lambda: []
    turn.trace.note = lambda *_: None
    requests = []

    def choose(_tools, state, questions):
        requests.append((state, questions))
        reading = route if "route" in questions else topic
        if reading == "fail":
            raise decision_model.DecisionModelUnavailable("synthetic transport failure")
        if reading == "abstain":
            raise decision_model.DecisionModelAbstained("below minimum confidence")
        return {"route": reading} if "route" in questions else {"topic": reading}

    monkeypatch.setattr(decision_model, "choose", choose)
    if expected:
        decision = decision_agents.run_decision("intent", turn, "Synthetic request.", CONTEXT)
        # The language is not read from the message: the session fixed it at the start.
        assert (decision.route, decision.reason, decision.response_language) == (
            expected[0], expected[1], CONTEXT.response_language)
        assert turn.trace.decision_evidence == [
            DecisionEvidence("intent", "microsoft-decision-1.1", "1", asdict(decision)),
        ]
    else:
        # A real failure is an outage; an unclear reading is a clarification (the cases above).
        with pytest.raises(DecisionUnavailable):
            decision_agents.run_decision("intent", turn, "Synthetic request.", CONTEXT)
        assert not turn.trace.decision_evidence
    # Each question is its own request, and the language is never asked.
    states = {next(iter(questions)): (state, questions) for state, questions in requests}
    assert sorted(states) == ["route", "topic"]
    assert states["route"][0] == states["topic"][0] == "Customer message: Synthetic request."
    # Controls and topics are separate questions, so neither dilutes the other.
    assert list(states["route"][1]["route"]["criteria"]) == [
        "asks_person", "asks_choice", "asks_information", "only_acknowledges"]
    assert set(states["topic"][1]["topic"]["criteria"]) == set(decision_agents._intent_criteria())
    assert "choose the first of: asks_person, asks_choice, asks_information, only_acknowledges" in (
        states["route"][1]["route"]["instructions"])


@pytest.mark.parametrize("verdict,expected", [("pass_draft", "pass"), ("advice", "fail"), ("unsupported", "revise"),
                                              ("abstained", "unassessable"), (None, None)])
def test_decision_model_is_sole_draft_reviewer(monkeypatch, verdict, expected):
    from governedgraph.agents import counsellor, decision_model

    turn = review_tools()
    session = SimpleNamespace(tools=turn, decision_agents={}, journey=SimpleNamespace(journey_id="J-review"),
                              _passage_fallback=lambda *_: "")

    def choose(_tools, _state, questions, optional=frozenset()):
        if verdict is None:
            raise decision_model.DecisionModelUnavailable("synthetic transport failure")
        if verdict == "abstained" and "verdict" in questions:
            raise decision_model.DecisionModelAbstained("below minimum confidence")
        return reading(verdict)(_tools, _state, questions, optional)

    monkeypatch.setattr(decision_model, "choose", choose)
    draft = json.dumps({"text": "The arrangement changes.", "content_ids": []})
    if expected is None:
        with pytest.raises(counsellor.DependencyFailure, match="answer_review_agent"):
            counsellor.Counsellor._review_answer(session, "What changes?", None, draft)
        assert not turn.trace.decision_evidence
        return
    actual, review = counsellor.Counsellor._review_answer(
        session, "What changes?", SimpleNamespace(teach_back=True), draft, allow_repair=False,
    )
    assert review.verdict == expected
    assert review.teach_back_concepts == (["rights_transfer"] if expected == "pass" else [])
    assert actual == (draft if expected == "pass" else "")
    assert turn.trace.decision_evidence == [
        DecisionEvidence("answer-review", "microsoft-decision-1.1", "1", asdict(review)),
    ]


def test_review_question_gets_plain_text_with_only_what_a_reviewer_needs(monkeypatch):
    from governedgraph.agents import decision_model
    from governedgraph.common.contracts import AnswerDraft

    history = [{"role": "user" if index % 2 == 0 else "assistant", "text": f"Earlier message {index}."}
               for index in range(8)]
    turn = review_tools(
        history=history,
        values=[SimpleNamespace(value_id="monthly_pension_after_transition",
                                label="Gross monthly pension directly after transition"),
                SimpleNamespace(value_id="current_monthly_pension", label="Current monthly pension")],
        sources=[("C-002-v1.2", "The payment directly after the transition is your official amount."),
                 ("C-009-v1.0", "A passage the draft did not cite.")])
    requests = []

    def choose(_tools, state, questions, optional=frozenset()):
        requests.append((state, questions))
        return reading("pass_draft")(_tools, state, questions, optional)

    monkeypatch.setattr(decision_model, "choose", choose)
    draft = AnswerDraft("Your amount is {{value:monthly_pension_after_transition}}.", ["C-002-v1.2"])
    decision_agents.review_with_decision_model(
        turn, "Will 1.500 euro change?", SimpleNamespace(teach_back=True), draft)
    # Two requests: the verdict and the concept checks. The language is fixed by the session, never judged.
    assert len(requests) == 2 and not any("language" in questions for _, questions in requests)
    verdict_state, verdict_questions = next(item for item in requests if "verdict" in item[1])
    concept_state, _ = next(item for item in requests if any(key.startswith("concept_") for key in item[1]))

    with pytest.raises(ValueError):
        json.loads(verdict_state)
    assert verdict_state.startswith("MEMBER QUESTION:\nWill [member-supplied number] euro change?")
    assert "Earlier message 6." in verdict_state and "Earlier message 7." in verdict_state
    assert "Earlier message 5." not in verdict_state
    assert ("{{value:monthly_pension_after_transition}} = Gross monthly pension directly after transition"
            in verdict_state)
    assert "current_monthly_pension" not in verdict_state
    assert "[C-002-v1.2] The payment directly after" in verdict_state and "C-009" not in verdict_state
    question = verdict_questions["verdict"]
    for section in ("MEMBER QUESTION", "PREVIOUS EXCHANGE", "DRAFT REPLY TO REVIEW", "OFFICIAL AMOUNTS",
                    "APPROVED SOURCES"):
        assert section in question["instructions"]
    assert list(question["criteria"]) == ["pass_draft", "advice", "unsupported", "off_target"]
    assert "including that something stays the same or will change" in question["criteria"]["unsupported"]
    assert concept_state == f"DRAFT REPLY:\n{draft.text}"


def test_concepts_are_not_asked_when_no_teach_back_or_exclusion_needs_them(monkeypatch):
    from governedgraph.agents import decision_model
    from governedgraph.common.contracts import AnswerDraft

    requests = []

    def choose(_tools, state, questions, optional=frozenset()):
        requests.append(set(questions))
        return reading("pass_draft")(_tools, state, questions, optional)

    monkeypatch.setattr(decision_model, "choose", choose)
    monkeypatch.setattr(decision_agents, "_excluded_concepts", lambda *_: [])
    turn = review_tools()
    review = decision_agents.review_with_decision_model(
        turn, "Will it change?", SimpleNamespace(teach_back=False), AnswerDraft("It stays the same.", []))
    assert requests == [{"verdict"}] and turn.trace.model_rounds == 1
    assert (review.verdict, review.concepts, review.teach_back_concepts) == ("pass", [], [])


def test_b3_a_claim_about_the_members_own_letter_blocks_through_the_unsupported_label(monkeypatch):
    from governedgraph.agents import decision_model
    from governedgraph.common.contracts import AnswerDraft

    monkeypatch.setattr(decision_model, "choose", reading("unsupported"))
    draft = AnswerDraft("Your amount is {{value:monthly_pension_after_transition}}. It is shown in your personal "
                        "transition letter. Adjustments can go up or down.", [])
    review = decision_agents.review_with_decision_model(
        review_tools(), "Will it change?", SimpleNamespace(teach_back=False), draft)
    assert (review.verdict, review.issues) == ("revise", ["grounding"])


@pytest.mark.parametrize("unavailable,expected", [(False, "unassessable"), (True, "unavailable")])
def test_b9_an_unclear_review_is_not_a_service_failure_but_a_real_failure_still_is(
        monkeypatch, caplog, unavailable, expected):
    from governedgraph.agents import decision_model
    from governedgraph.common.contracts import AnswerDraft

    def choose(_tools, _state, questions, optional=frozenset()):
        if "verdict" in questions:
            if unavailable:
                raise decision_model.DecisionModelUnavailable("synthetic transport failure")
            raise decision_model.DecisionModelAbstained("below minimum confidence")
        return reading("pass_draft")(_tools, _state, questions, optional)

    monkeypatch.setattr(decision_model, "choose", choose)
    caplog.set_level(logging.WARNING, logger="governedgraph.decision_agents")
    draft = AnswerDraft("The arrangement changes.", [])
    if expected == "unavailable":
        with pytest.raises(DecisionUnavailable):
            decision_agents.review_with_decision_model(
                review_tools(), "What changes?", SimpleNamespace(teach_back=False), draft)
        return
    review = decision_agents.review_with_decision_model(
        review_tools(), "What changes?", SimpleNamespace(teach_back=False), draft)
    assert (review.verdict, review.issues) == ("unassessable", ["grounding"])
    assert "answer_review_abstained" in caplog.text


@pytest.mark.parametrize("verdict,delivered,issue", [("off_target", True, "relevance"),
                                                     ("unsupported", False, "grounding")])
def test_b3_quality_only_review_keeps_gated_draft_while_safety_defects_do_not(
        monkeypatch, verdict, delivered, issue):
    from governedgraph.agents import counsellor, decision_model

    turn = review_tools()
    session = SimpleNamespace(tools=turn, decision_agents={}, journey=SimpleNamespace(journey_id="J-style"),
                              _passage_fallback=lambda *_: "")
    monkeypatch.setattr(decision_agents, "_excluded_concepts", lambda *_: [])
    monkeypatch.setattr(decision_model, "choose", reading(verdict))
    draft = json.dumps({"text": "The arrangement changes.", "content_ids": []})
    actual, review = counsellor.Counsellor._review_answer(
        session, "What changes?", SimpleNamespace(teach_back=True), draft, allow_repair=False)
    assert (review.verdict, review.issues) == ("revise", [issue])
    assert actual == (draft if delivered else "")
    assert turn.trace.composition_source == ("foundry" if delivered else "template_fallback")


def test_b7_decision_context_rejects_demographic_fields():
    with pytest.raises(ContractError):
        DecisionContext.from_dict({**asdict(CONTEXT), "age_band": "75+"})


def test_b1_b2_b7_intent_state_carries_only_situation_last_exchange_and_message(monkeypatch):
    from governedgraph.agents import decision_model

    states = {}
    monkeypatch.setattr(decision_model, "choose", lambda _t, state, questions: (
        states.__setitem__(next(iter(questions)), (state, questions)),
        {"route": "asks_information"} if "route" in questions else {"topic": "payment_continuity"})[1])
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
    expected = (
        "Context: the assistant has just asked the customer to say in their own words what they understood.\n"
        "Previous exchange (context only):\nCustomer: Earlier question\nAssistant: Earlier answer\n"
        "Customer message: And afterwards?")
    assert states["route"][0] == states["topic"][0] == expected
    assert "MBR-" not in expected and "T-S5" not in expected
    # The situation changes how a restatement is read: here it answers the teach-back request.
    assert "answers the check question just asked and is only_acknowledges" in states["route"][1]["route"]["instructions"]
    assert "response_language" not in states


def test_b9_a_question_after_an_accepted_answer_is_never_read_as_an_acknowledgement(monkeypatch):
    from governedgraph.agents import decision_model

    seen = {}
    monkeypatch.setattr(decision_model, "choose", lambda _t, state, questions: (
        seen.__setitem__(next(iter(questions)), questions),
        {"route": "asks_information"} if "route" in questions else {"topic": "payment_continuity"})[1])
    turn = tools()
    turn.trace.note = lambda *_: None
    turn.conversation_context = lambda: []
    decision_agents.run_decision(
        "intent", turn, "So it stays the same, right?", DecisionContext("resolved", "unclassified", None))
    assert "Any other question, including \"is that right?\", is asks_information" in (
        seen["route"]["route"]["instructions"])


@pytest.mark.parametrize("state", ["resolved", "comprehension_check", "explaining"])
def test_a_restatement_answering_the_previous_check_question_is_an_acknowledgement_in_every_state(monkeypatch, state):
    # Observed in gg-dev: the journey was "resolved" although the previous answer ended by asking the member to
    # restate it, so the restatement was read as a new question and the same explanation was given twice.
    from governedgraph.agents import decision_model

    seen = {}
    monkeypatch.setattr(decision_model, "choose", lambda _t, state, questions: (
        seen.__setitem__(next(iter(questions)), questions),
        {"route": "only_acknowledges"} if "route" in questions else {"topic": "payment_continuity"})[1])
    turn = tools()
    turn.trace.note = lambda *_: None
    turn.conversation_context = lambda: [{"role": "assistant", "text": "... In your own words, what changes?"}]
    decision_agents.run_decision("intent", turn, "So it can go up or down. Is that right?",
                                 DecisionContext(state, "unclassified", None))
    assert decision_agents.ANSWERED_CHECK in seen["route"]["route"]["instructions"]


def test_b9_a_restatement_offered_for_confirmation_is_a_request_for_information_even_without_a_question_mark():
    # Observed in gg-dev: the transcribed "So, right after the transition I will get the same amount, but later it
    # might go up or down." was closed with "We can leave it here for now", because the label said a restatement
    # is an acknowledgement and speech carries no question mark.
    from governedgraph.common.domain import vocabulary

    # Observed in gg-dev (second contact): a restatement closed by "Is that right?" answering a pending teach-back
    # check was routed as a new question and the same explanation was given twice, because the label text said a
    # restatement is a question while the context note said the opposite. Only the context note decides it now.
    criteria = vocabulary()["decision_criteria"]["route"]
    assert not any("is that right" in text.lower() for text in criteria.values())
    assert all("context note" in criteria[label] for label in ("asks_information", "only_acknowledges"))
    note = decision_agents.ROUTE_NOTES["resolved"]
    assert "with or without a question mark" in note and "asks_information" in note


def test_b3_b7_question_focus_preserves_old_audits_without_generic_new_native_outputs():
    from governedgraph.agents.foundry_runtime import ANSWER_GOALS
    from governedgraph.common.contracts import EXPLANATION_CONCEPTS

    assert IntentDecision.from_dict(EXPLAIN).reason == "official_transition_information"
    assert IntentDecision.from_dict({**EXPLAIN, "reason": "combined_transition_question"})
    reasons = set(decision_agents._intent_criteria())
    assert not HISTORICAL_REASONS & reasons
    for reason, (_, excluded) in ANSWER_GOALS.items():
        decision = IntentDecision.from_dict({**EXPLAIN, "reason": reason})
        assert decision.intent == EXPLAIN["intent"] and decision.reason in reasons
        assert set(excluded) <= EXPLANATION_CONCEPTS
        with pytest.raises(ContractError):
            IntentDecision.from_dict({**asdict(decision), "age_band": "synthetic"})


def test_stored_reviews_with_retired_issue_labels_still_decode_as_the_nearest_current_label():
    old = {"verdict": "revise", "issues": ["uncertainty", "document_context", "relevance"],
           "concepts": [], "teach_back_concepts": []}
    assert AnswerReview.from_dict(old).issues == ["grounding", "relevance"]
    with pytest.raises(ContractError):
        AnswerReview.from_dict({**old, "issues": ["invented"]})
    with pytest.raises(ContractError):
        AnswerReview.from_dict({**old, "verdict": "pass"})


@pytest.mark.parametrize("route,intent,reason", sorted(
    row for row in INTENT_DECISIONS if row[2] not in HISTORICAL_REASONS))
def test_b3_b7_classification_derives_only_the_approved_matching_route(monkeypatch, route, intent, reason):
    from governedgraph.agents import decision_model

    labels = {"explanation": "asks_information", "human": "asks_person", "advice": "asks_choice",
              "comprehension": "only_acknowledges"}

    def choose(_t, _s, questions):
        if "route" in questions:
            if route == "clarification":
                raise decision_model.DecisionModelAbstained("below minimum confidence")
            return {"route": labels[route]}
        return {"topic": reason if route == "explanation" else "transition_overview"}

    monkeypatch.setattr(decision_model, "choose", choose)
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
    old.pop("decline_reason")  # legacy records predate the field and were stored without it
    old["agent_version"] = "1"  # legacy records carry the version they were pinned to
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


def test_b9_personal_choice_and_factual_objection_criteria_stay_distinct():
    route, topics = decision_agents._pack_criteria()["route"], decision_agents._intent_criteria()
    assert "whether the customer should take an action" in route["asks_choice"]
    assert "not whether the customer should" in topics["transition_information_gap"]
    assert "is not a request to decide" in route["asks_choice"]


def test_b3_audit_accepts_only_one_bounded_repair_and_preserves_unique_routing_evidence(counsellor):
    from governedgraph.common.contracts import ExplainabilityRecord

    counsellor.handle("Will the transition change what I receive?")
    record = counsellor.tools.store.read_all()[-1]
    def evidence(verdict):
        return asdict(DecisionEvidence("answer-review", "microsoft-decision-1.1", "1", {
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
