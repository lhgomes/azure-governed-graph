"""ILLUSTRATIVE - synthetic data: selected-style tests never alter member selection or learning."""
import asyncio
from contextlib import nullcontext
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from governedgraph.admin.testing import TestChannel as SelectedStyleChannel
from governedgraph.admin.testing import TestSession as SelectedStyleSession
from governedgraph.agents.counsellor import Counsellor, DependencyFailure, DISCLOSURE, START_GREETINGS
from governedgraph.common.contracts import ContractError, ExplainabilityRecord, ResponseReference
from governedgraph.common.member_auth import MemberIdentity
from governedgraph.learning.feedback import project_feedback
from governedgraph.orchestrator.service import InvalidAnswerReference, MemberScopeViolation, OrchestratorService
from governedgraph.orchestrator.state_machine import Journey
from governedgraph.orchestrator.store import JourneyRecord
from governedgraph.strategy_service.catalogue import Catalogue
from strategy_doubles import install_no_export_tracing


@pytest.fixture(autouse=True)
def approved_catalogue(monkeypatch):
    from governedgraph.admin import testing

    monkeypatch.setattr(testing.HttpStrategyClient, "get_catalogue", lambda *_: (Catalogue.load(), {}))


@pytest.mark.parametrize("extra", [{"age": 60}, {"script": {}}, {"benchmark_model": "other"}])
def test_b7_test_session_rejects_unapproved_fields(extra):
    with pytest.raises(ContractError):
        SelectedStyleSession.from_dict({"strategy_id": "S1", "mode": "chat", **extra})


@pytest.mark.parametrize("payload", [
    {"strategy_id": "not-approved", "mode": "chat"},
    {"strategy_id": "S1", "mode": "benchmark"},
    {"strategy_id": "S1", "mode": "simulator"},
])
def test_b3_test_session_requires_approved_style_and_fixed_modes(payload):
    with pytest.raises(ContractError):
        SelectedStyleSession.from_dict(payload)


@pytest.mark.parametrize("contact_type", ["first_contact", "second_contact"])
def test_test_channel_pins_style_and_keeps_both_tokens_in_request_headers(contact_type):
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"journey_id": "J-test", "text": DISCLOSURE, "state": "authenticated"})

    async def run():
        async with httpx.AsyncClient(base_url="https://runtime.example/", follow_redirects=False,
                                     transport=httpx.MockTransport(handle)) as client:
            channel = SelectedStyleChannel(client, lambda: "synthetic-member-bearer", "S3", "synthetic-admin-bearer")
            return await channel.start(channel="chat", contact_type=contact_type)

    reply = asyncio.run(run())
    assert reply.text == DISCLOSURE
    assert seen[0].url.path == "/admin-test/journeys"
    assert seen[0].headers["Authorization"] == "Bearer synthetic-member-bearer"
    assert seen[0].headers["X-Admin-Authorization"] == "Bearer synthetic-admin-bearer"
    assert json.loads(seen[0].read()) == {
        "strategy_id": "S3", "channel": "chat", "accessibility_mode": "standard",
        **({"contact_type": contact_type} if contact_type == "second_contact" else {}),
    }


@pytest.mark.parametrize("contact_type", ["first_contact", "second_contact"])
def test_b4_b7_contact_type_persists_as_stage_without_restoring_conversation(
        contact_type, isolated_conversations, monkeypatch):
    install_no_export_tracing(monkeypatch)
    transaction = SimpleNamespace(record=None, save=Mock())
    store = Mock()
    store.transaction.return_value = nullcontext(transaction)
    runtime = OrchestratorService(store, Mock(), audit_store=Mock(), continuity=isolated_conversations)
    result = runtime.start(MemberIdentity("MEMBER-SYNTHETIC", True), "chat", "standard",
                           contact_type=contact_type)
    stored = transaction.save.call_args.args[0].journey
    restored = Journey.from_dict(stored.to_dict())
    assert restored.stage == restored.request_key()["journey_state"] == contact_type
    assert restored.last_explanation is None
    assert len(restored.events) == 1
    assert restored.events[0].event == "disclosed_and_authenticated"
    assert result["text"] == (DISCLOSURE if contact_type == "first_contact" else START_GREETINGS["second_contact"])
    assert restored.state.value == "identified"


@pytest.mark.parametrize("contact_type", ["third_contact", None, {}, ["second_contact"]])
def test_b7_contact_type_is_closed_before_any_journey_is_created(contact_type):
    runtime = object.__new__(OrchestratorService)
    runtime.store = Mock()
    with pytest.raises(ContractError, match="contact type"):
        runtime.start(MemberIdentity("MEMBER-SYNTHETIC", True), "chat", "standard", contact_type=contact_type)
    runtime.store.transaction.assert_not_called()


@pytest.fixture
def selection(monkeypatch):
    counsellor = object.__new__(Counsellor)
    ranked = SimpleNamespace(source="signed_snapshot", catalogue_version=Catalogue.load().version, ranked=[
        SimpleNamespace(strategy_id="S3"), SimpleNamespace(strategy_id="S1")])
    tools = SimpleNamespace(trace=SimpleNamespace(ranked=ranked, escalations=[], values=None,
        decision_evidence=[], measure=lambda _: nullcontext()))
    for key, value in {
        "journey": Journey("J-selected", "MEMBER-SYNTHETIC", channel="chat", test_strategy_id="S1"),
        "catalogue": Catalogue.load(), "tools": tools, "_prefetched": True, "step_up_verified": True,
    }.items():
        monkeypatch.setattr(counsellor, key, value, raising=False)
    compose = Mock(side_effect=DependencyFailure("stop after selection"))
    monkeypatch.setattr(counsellor, "_compose", compose)
    return counsellor, compose


def test_b3_test_selects_requested_served_style_without_reordering_the_signed_response(selection):
    counsellor, compose = selection
    with pytest.raises(DependencyFailure):
        counsellor._explain("Explain the approved information.", set())
    assert compose.call_args.args[0].strategy_id == "S1"
    served = counsellor.tools.trace.ranked
    assert served is not None
    assert [item.strategy_id for item in served.ranked] == ["S3", "S1"]
    counsellor.journey.test_strategy_id = ""
    with pytest.raises(DependencyFailure):
        counsellor._explain("Explain the approved information.", set())
    assert compose.call_args.args[0].strategy_id == "S3"


def test_b6_test_cannot_select_an_unserved_or_excluded_style(selection, monkeypatch):
    counsellor, compose = selection
    escalate = Mock(return_value=SimpleNamespace(escalated=True))
    monkeypatch.setattr(counsellor, "_escalate", escalate)
    counsellor.journey.test_strategy_id = "S2"
    assert counsellor._explain("Explain the approved information.", set()).escalated
    counsellor.journey.test_strategy_id = "S1"
    assert counsellor._explain("Explain the approved information.", {"S1"}).escalated
    compose.assert_not_called()
    assert escalate.call_count == 2


def test_b8_member_turn_cannot_resume_a_pinned_test_without_admin_authorization():
    runtime = object.__new__(OrchestratorService)
    journey = Journey("J-selected", "MEMBER-SYNTHETIC", test_strategy_id="S1")
    runtime.store = Mock()
    runtime.store.transaction.return_value = nullcontext(SimpleNamespace(record=SimpleNamespace(journey=journey)))
    with pytest.raises(MemberScopeViolation):
        runtime.turn(journey.journey_id, "question", MemberIdentity(journey.member_ref, True), "synthetic-bearer", "Explain.")
    with pytest.raises(InvalidAnswerReference):
        runtime._linked_answer(JourneyRecord(journey), ResponseReference("J-member", "b" * 32),
                               MemberIdentity(journey.member_ref, True), before=datetime.now(timezone.utc))


def test_b4_b10_test_answers_and_journeys_do_not_change_any_learning_projection():
    journey = Journey("J-selected", "MEMBER-SYNTHETIC", test_strategy_id="S1")
    record = ExplainabilityRecord(
        journey.journey_id, journey.member_ref, "S1", "1.0", "signed-test", "control",
        [], [], [], "pinned-model", "counsellor", "1", True, True, journey.request_key(), True,
        response_kind="explanation", response_id="c" * 32,
    )
    now = datetime.now(timezone.utc)
    assert project_feedback([journey], [record], now=now) == project_feedback([], [], now=now)
    assert Journey.from_dict(journey.to_dict()).test_strategy_id == "S1"
    member = Journey("J-member", "MEMBER-SYNTHETIC")
    assert "test_strategy_id" not in member.to_dict()
