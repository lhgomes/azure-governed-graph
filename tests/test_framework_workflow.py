"""ILLUSTRATIVE - synthetic data: real Agent Framework policy routing."""
import asyncio
from contextvars import ContextVar

import pytest

from governedgraph.agents.counsellor import new_session
from governedgraph.common.config import settings
from governedgraph.learning.run_cycle import run_cycle
from governedgraph.strategy_service.service import StrategyService


def test_art50_b8_natural_opening_is_exactly_user_approved_and_versioned():
    from governedgraph.agents.counsellor import DISCLOSURE, TEMPLATES
    from governedgraph.guardrails.numeric_fidelity import check

    assert TEMPLATES["template_set_version"] == "1.4"
    assert TEMPLATES["disclosure"]["approval_ref"] == "IMP-102-user-approval-2026-11-04"
    assert DISCLOSURE == (
        "Hello, welcome! I'm your AI pension guide. We can take your pension questions one step at a time. "
        "I can help explain pension information and your official figures, but I can't give personal advice. "
        "You can ask for a person at any time. Where would you like to start?"
    )
    assert check(DISCLOSURE, None, []).passed


@pytest.fixture
def counsellor(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.__class__, "run_dir", property(lambda self: tmp_path))
    strategies = StrategyService(state_dir=tmp_path / "strategy", control_share=0)
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

    def checked(*args):
        observed.append(context.get())
        return screen(*args)

    monkeypatch.setattr(counsellor.screen, "screen", checked)

    async def turn():
        return counsellor.handle("Explain my transition letter")

    result = asyncio.run(turn())
    assert not result.escalated
    assert observed == ["synthetic-trace"]
