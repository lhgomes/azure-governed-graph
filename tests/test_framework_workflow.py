"""ILLUSTRATIVE - synthetic data: real Agent Framework policy routing."""
import asyncio
from contextvars import ContextVar

import pytest

from counsellor_doubles import install, new_session
from governedgraph.common.config import settings
from strategy_doubles import run_cycle
from strategy_doubles import local_strategy_service


def test_art50_b8_natural_opening_is_exactly_user_approved_and_versioned():
    from governedgraph.agents.counsellor import DISCLOSURE, TEMPLATES
    from governedgraph.guardrails.numeric_fidelity import check

    assert TEMPLATES["template_set_version"] == "1.5"
    assert TEMPLATES["disclosure"]["approval_ref"] == "IMP-102-user-approval-2026-11-14"
    assert DISCLOSURE == (
        "Hello, welcome! I'm your AI pension guide. We can take your pension questions one step at a time. "
        "You can ask for a person at any time. Where would you like to start?"
    )
    assert check(DISCLOSURE, None, []).passed


@pytest.fixture
def counsellor(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.__class__, "run_dir", property(lambda self: tmp_path))
    install(monkeypatch, tmp_path)
    strategies = local_strategy_service(tmp_path / "strategy", control_share=0)
    run_cycle(strategies)
    agent = new_session("MBR-7Q2K", strategy_service=strategies, journey_id="J-framework")
    agent.start()
    return agent


def test_b1_b2_b3_framework_explains_and_commits_exactly_once(counsellor):
    result = counsellor.handle("Explain my transition letter")
    assert result.workflow_steps == [
        "screen-input", "intent-agent", "explain-with-approved-tools", "commit-checked-evidence",
    ]
    assert result.numeric_fidelity_passed and result.no_option_recommendation
    records = counsellor.tools.store.read_journey(counsellor.journey.journey_id)
    assert len([record for record in records if record["response_kind"] == "explanation"]) == 1
    assert result.snapshot_id == counsellor.tools.trace.ranked.snapshot_id


@pytest.mark.parametrize("utterance,branch", [
    ("I want a human advisor", "requested-human-handoff"),
    ("Should I object to the transition or not?", "personal-advice-handoff"),
])
def test_b9_dq10_framework_routes_directly_to_checked_handover(counsellor, utterance, branch):
    result = counsellor.handle(utterance)
    assert result.escalated
    assert result.workflow_steps == ["screen-input", "intent-agent", branch, "commit-checked-evidence"]
    assert result.no_option_recommendation


def test_b5_failed_teachback_uses_the_explanation_executor(counsellor):
    first = counsellor.handle("Explain my transition letter")
    second = counsellor.handle("I still do not understand")
    assert second.strategy_id != first.strategy_id
    assert second.workflow_steps == [
        "screen-input", "intent-agent", "understanding-agent",
        "explain-with-approved-tools", "commit-checked-evidence",
    ]


def test_framework_preserves_context_under_an_existing_event_loop(counsellor, monkeypatch):
    context = ContextVar("test-workflow-context")
    context.set("synthetic-trace")
    screen = counsellor.screen.screen
    observed = []

    def checked(*args, **kwargs):
        observed.append(context.get())
        return screen(*args, **kwargs)

    monkeypatch.setattr(counsellor.screen, "screen", checked)

    async def turn():
        return counsellor.handle("Explain my transition letter")

    result = asyncio.run(turn())
    assert not result.escalated
    # Input screening and approved-document screening both run inside the preserved context.
    assert observed == ["synthetic-trace"] * 2


@pytest.mark.parametrize("utterance,awaiting_teach_back,new_question", [
    ("So the capital is the fund's official calculation of what I've earned, and it replaces the yearly adjustment? "
     "That sounds clear, but can you confirm if this matches my statements?", True, True),
    ("Hello again. I understood the explanation. I am still worried about later years. Could we focus on that?", True, True),
    ("Thanks, but what happens in later years?", True, True),
    ("So it stays the same on the transition date and can go up or down each year after that.", True, False),
    ("So it stays the same on the transition date, right?", True, False),
    ("So it stays the same on the transition date, right?", False, True),
    ("Thank you, that makes sense.", False, False),
])
def test_b9_recap_questions_are_answered_unless_they_are_pending_teach_backs(
        utterance, awaiting_teach_back, new_question):
    from governedgraph.orchestrator.workflow import asks_something_new

    assert asks_something_new(utterance, awaiting_teach_back=awaiting_teach_back) is new_question


def _recap_then_question(monkeypatch, follow_up_reason):
    from governedgraph.agents import decision_agents
    from governedgraph.common.contracts import INTENT_DECISIONS, IntentDecision

    recap = IntentDecision("comprehension", "unclassified", "restating_previous_explanation", "en")
    follow_up = IntentDecision(*next(row for row in INTENT_DECISIONS if row[2] == follow_up_reason), "en")
    roles = []

    def decide(role, tools, utterance, context):
        roles.append(role)
        return follow_up if role == "follow_up_intent" else recap

    monkeypatch.setattr(decision_agents, "run_decision", decide)
    return roles


def test_b9_restating_reading_with_a_new_question_reaches_the_explanation_executor(counsellor, monkeypatch):
    counsellor.handle("Explain my transition letter")
    roles = _recap_then_question(monkeypatch, "payment_continuity")
    result = counsellor.handle("I understood that. Could we focus on what happens in later years?")
    assert roles == ["intent", "follow_up_intent"]
    assert "explain-with-approved-tools" in result.workflow_steps
    assert "acknowledge-explanation" not in result.workflow_steps
    assert {"stage": "intent", "result": "new_question_in_reply", "issues": []} in result.turn_trace
    assert counsellor.journey.intent == "transition_impact_question"


def test_b3_cold_second_contact_recap_question_is_ranked_under_a_signed_explanation_key(counsellor, monkeypatch):
    # Observed in gg-dev: a first utterance misread as a recap left the intent "unclassified", the ranking
    # request fell to an unsigned fallback and the member was handed over with "a service is unavailable".
    counsellor.journey.stage = "second_contact"
    roles = _recap_then_question(monkeypatch, "payment_continuity")
    result = counsellor.handle(
        "Hello again. I understood the explanation. I am still worried about later years. Could we focus on that?")
    assert roles == ["intent", "follow_up_intent"]
    assert not result.escalated and result.strategy_id != "S6"
    assert counsellor.journey.request_key()["intent"] == "transition_impact_question"
    assert counsellor.tools.trace.ranked.signature_valid


def test_follow_up_reading_asks_one_question_without_the_recap_and_supersedes_the_earlier_evidence(monkeypatch):
    from types import SimpleNamespace

    from governedgraph.agents import decision_agents, decision_model
    from governedgraph.agents.tools import Trace
    from governedgraph.common.contracts import DecisionEvidence, IntentDecision

    asked = []

    def choose(tools, state, questions):
        asked.append(questions)
        return {"reason": "payment_continuity"}

    monkeypatch.setattr(decision_model, "choose", choose)
    trace = Trace()
    trace.decision_evidence.append(DecisionEvidence("intent", "microsoft-decision-1.1", "1", {
        "route": "comprehension", "intent": "unclassified", "reason": "restating_previous_explanation",
        "response_language": "en"}))
    tools = SimpleNamespace(trace=trace, conversation_context=lambda: [])
    context = decision_agents.DecisionContext("resolved", "unclassified", None, "en", None)
    decision = decision_agents.run_decision("follow_up_intent", tools, "Could we focus on later years?", context)
    assert decision == IntentDecision("explanation", "transition_impact_question", "payment_continuity", "en")
    assert len(asked) == 1 and set(asked[0]) == {"reason"}
    assert "restating_previous_explanation" not in asked[0]["reason"]["criteria"]
    assert [(item.role, item.decision["reason"]) for item in trace.decision_evidence] == [
        ("intent", "payment_continuity")]


@pytest.mark.parametrize("reason,branch", [
    ("explicit_human_request", "requested-human-handoff"),
    ("personal_choice_or_comparison", "personal-advice-handoff"),
])
def test_b9_follow_up_reading_keeps_human_and_advice_routes_reachable(counsellor, monkeypatch, reason, branch):
    _recap_then_question(monkeypatch, reason)
    result = counsellor.handle("I understood that. Could we speak to someone about what I should do?")
    assert result.escalated and branch in result.workflow_steps
    assert "explain-with-approved-tools" not in result.workflow_steps


def test_follow_up_reading_cannot_select_the_recap_it_corrects():
    from governedgraph.agents.decision_agents import _intent_criteria

    assert "restating_previous_explanation" in _intent_criteria()
    assert "restating_previous_explanation" not in _intent_criteria(follow_up=True)
    assert {"explicit_human_request", "personal_choice_or_comparison"} <= set(_intent_criteria(follow_up=True))


def test_art50_b8_start_variants_keep_the_original_approval_and_carry_their_own():
    from governedgraph.agents.counsellor import START_GREETINGS, TEMPLATES
    from governedgraph.guardrails.numeric_fidelity import check

    disclosure = TEMPLATES["disclosure"]
    assert disclosure["approval_ref"] == "IMP-102-user-approval-2026-11-14"
    for variant in ("returning", "second_contact"):
        assert disclosure[variant]["approval_ref"] == "IMP-CUSTOMER-STARTUP-CONTINUITY-2026-11-20"
    for text in START_GREETINGS.values():
        assert "AI pension guide" in text and "person at any time" in text
        assert check(text, None, []).passed


def test_b8_confirmed_closing_is_approved_figure_free_and_does_not_replace_the_thank_you_closing():
    from governedgraph.agents.counsellor import TEMPLATES
    from governedgraph.guardrails.advice_intent import response_steers_choice
    from governedgraph.guardrails.numeric_fidelity import check

    acknowledgements = TEMPLATES["acknowledgements"]
    confirmed, thanks = acknowledgements["comprehension_confirmed"], acknowledgements["conversation_complete"]
    assert confirmed["approval_ref"] == "HR-PENSION-WTP-CONFIRMED-CLOSING-2026-11-20"
    assert thanks["approval_ref"] == "HR-PENSION-WTP-CONFIRMATION-CLOSURE-2026-11-17"
    for language in ("en", "nl"):
        assert confirmed[language] != thanks[language] and "?" not in confirmed[language]
        assert check(confirmed[language], None, []).passed and not response_steers_choice(confirmed[language])
    assert "leave it here" not in confirmed["en"]
