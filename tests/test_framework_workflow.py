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


@pytest.mark.parametrize("utterance,new_question", [
    ("So the capital is the fund's official calculation of what I've earned, and it replaces the yearly adjustment? "
     "That sounds clear, but can you confirm if this matches my statements?", True),
    ("Hello again. I understood the explanation. I am still worried about later years. Could we focus on that?", True),
    ("Thanks, but what happens in later years?", True),
    ("So it stays the same on the transition date and can go up or down each year after that.", False),
    ("So it stays the same on the transition date, right?", False),
    ("Thank you, that makes sense.", False),
])
def test_b9_recap_with_a_new_question_is_not_a_confirmation(utterance, new_question):
    from governedgraph.orchestrator.workflow import asks_something_new

    assert asks_something_new(utterance) is new_question


def test_b9_restating_reading_with_a_new_question_reaches_the_explanation_executor(counsellor, monkeypatch):
    from governedgraph.agents import decision_agents
    from governedgraph.common.contracts import IntentDecision

    counsellor.handle("Explain my transition letter")
    recap = IntentDecision("comprehension", "unclassified", "restating_previous_explanation", "en")
    monkeypatch.setattr(decision_agents, "run_decision", lambda role, tools, utterance, context: recap)
    result = counsellor.handle("I understood that. Could we focus on what happens in later years?")
    assert "explain-with-approved-tools" in result.workflow_steps
    assert "acknowledge-explanation" not in result.workflow_steps
    assert {"stage": "intent", "result": "new_question_in_reply", "issues": []} in result.turn_trace
