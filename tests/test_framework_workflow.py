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

    assert TEMPLATES["template_set_version"] == "1.7"
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


def _routed(monkeypatch, decision):
    from governedgraph.agents import decision_agents

    roles = []

    def decide(role, tools, utterance, context):
        roles.append(role)
        return decision

    monkeypatch.setattr(decision_agents, "run_decision", decide)
    return roles


def test_b9_a_recap_that_the_router_reads_as_a_question_reaches_the_explanation_executor(counsellor, monkeypatch):
    from governedgraph.common.contracts import IntentDecision

    counsellor.handle("Explain my transition letter")
    roles = _routed(monkeypatch, IntentDecision(
        "explanation", "transition_impact_question", "payment_continuity", "en"))
    result = counsellor.handle("I understood that. Could we focus on what happens in later years?")
    assert roles == ["intent"]
    assert "explain-with-approved-tools" in result.workflow_steps
    assert "acknowledge-explanation" not in result.workflow_steps
    assert counsellor.journey.intent == "transition_impact_question"


def test_b3_cold_second_contact_recap_question_is_ranked_under_a_signed_explanation_key(counsellor, monkeypatch):
    # Observed in gg-dev: a first utterance misread as a recap left the intent "unclassified", the ranking
    # request fell to an unsigned fallback and the member was handed over with "a service is unavailable".
    from governedgraph.common.contracts import IntentDecision

    counsellor.journey.stage = "second_contact"
    roles = _routed(monkeypatch, IntentDecision(
        "explanation", "transition_impact_question", "payment_continuity", "en"))
    result = counsellor.handle(
        "Hello again. I understood the explanation. I am still worried about later years. Could we focus on that?")
    assert roles == ["intent"]
    assert not result.escalated and result.strategy_id != "S6"
    assert counsellor.journey.request_key()["intent"] == "transition_impact_question"
    assert counsellor.tools.trace.ranked.signature_valid


def _unclear_request(counsellor, monkeypatch, drafted):
    from types import SimpleNamespace

    from governedgraph.agents.foundry_runtime import FoundryComposer
    from governedgraph.common.contracts import IntentDecision

    counsellor.decision_agents["intent"] = SimpleNamespace(run=lambda *_: IntentDecision(
        "clarification", "unclassified", "more_context_needed", "en"))
    asked = []

    def clarify(tools, utterance):
        asked.append(utterance)
        if isinstance(drafted, Exception):
            raise drafted
        return drafted

    monkeypatch.setattr(FoundryComposer, "shared", classmethod(
        lambda cls, instructions: SimpleNamespace(clarify=clarify)))
    return asked


def test_b2_b9_an_unclear_request_gets_a_writer_drafted_clarifying_question_after_the_gates(
        counsellor, monkeypatch):
    question = "Which part of the transition would you like me to explain?"
    asked = _unclear_request(counsellor, monkeypatch, question)
    result = counsellor.handle("What about that?")
    assert result.text == question and asked == ["What about that?"]
    assert not result.escalated and "clarify-question" in result.workflow_steps
    assert counsellor.tools.trace.values is None
    assert counsellor.tools.store.read_all()[-1]["response_kind"] == "acknowledgment"


@pytest.mark.parametrize("drafted", [
    "Your pension is 1,412.50 a month. What do you mean?",
    "What do you mean by 2028?",
    "You should choose option A. Which do you mean?",
    "Which part do you mean? Or which topic?",
    "Please tell me more.",
    "What is {{value:current_monthly_pension}}?",
    "Which part do you mean" + " really" * 60 + "?",
    "",
    TimeoutError("private writer text"),
])
def test_b2_b3_b9_a_clarifying_question_that_fails_a_gate_or_the_writer_falls_back_to_approved_wording(
        counsellor, monkeypatch, caplog, drafted):
    from governedgraph.common.domain import message

    _unclear_request(counsellor, monkeypatch, drafted)
    result = counsellor.handle("What about that?")
    assert result.text == message("clarify_question") and not result.escalated
    assert "private writer text" not in caplog.text and counsellor.tools.trace.values is None


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


def test_b2_gna_restatement_verdicts_are_approved_short_figure_free_and_never_steer():
    from governedgraph.agents.counsellor import TEMPLATES
    from governedgraph.guardrails.advice_intent import response_steers_choice
    from governedgraph.guardrails.numeric_fidelity import check

    for verdict in ("matches", "partly", "differs"):
        entry = TEMPLATES["acknowledgements"][f"restatement_{verdict}"]
        assert entry["approval_ref"] == "IMP-RESTATEMENT-VERDICT-2026-11-25-user-approval"
        for language in ("en", "nl"):
            text = entry[language]
            assert len(text) <= 40 and "?" not in text and "{{" not in text
            assert check(text, None, []).passed and not response_steers_choice(text)
