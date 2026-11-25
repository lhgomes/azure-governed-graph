"""ILLUSTRATIVE - synthetic data: a restatement turn through the real orchestrator, journey store and reply contracts."""
import json
from types import SimpleNamespace

import pytest

from governedgraph.agents import decision_agents
from governedgraph.agents.counsellor import Counsellor
from governedgraph.common.config import settings
from governedgraph.common.member_auth import MemberIdentity
from governedgraph.orchestrator.messaging import FileJourneyPublisher
from governedgraph.orchestrator.service import OrchestratorService
from governedgraph.orchestrator.store import FileJourneyStore
from governedgraph.voice.voice_live_bridge import VoiceReply
from counsellor_doubles import install
from strategy_doubles import local_strategy_service, run_cycle

IDENTITY = MemberIdentity("MBR-7Q2K", True)
RESTATEMENT = "So right after the transition my payment is my official amount, and later it can go up or down?"


@pytest.fixture
def resolved_journey(tmp_path, monkeypatch, isolated_conversations, request):
    monkeypatch.setattr(settings.__class__, "run_dir", property(lambda self: tmp_path))
    monkeypatch.setenv("AZURE_ENV_NAME", "gg-dev")
    monkeypatch.setenv("GOVERNEDGRAPH_ENABLE_OUTCOME_CHECK", "true")
    install(monkeypatch, tmp_path)
    strategies = local_strategy_service(tmp_path / "strategy", control_share=0)
    run_cycle(strategies)
    runtime = OrchestratorService(
        FileJourneyStore(tmp_path / "journeys"), FileJourneyPublisher(tmp_path / "queues"),
        lambda journey, auth_ref, **kwargs: Counsellor(journey, auth_ref, strategy_service=strategies, **kwargs),
        continuity=isolated_conversations)
    channel = request.param
    journey_id = runtime.start(IDENTITY, channel, "standard")["journey_id"]
    runtime.turn(journey_id, "explain", IDENTITY, "", "Explain my transition letter")
    resolved = runtime.turn(journey_id, "teachback", IDENTITY, "", "It stays the same and can go up or down each year.")
    assert resolved["state"] == "resolved"

    def restate(verdict):
        monkeypatch.setattr(decision_agents, "read_restatement", lambda *_: verdict)
        return runtime.turn(journey_id, "restate", IDENTITY, "", RESTATEMENT)

    def audits():
        return [json.loads(line) for line in (tmp_path / "explainability" / "records.jsonl").read_text().splitlines()]

    return SimpleNamespace(restate=restate, audits=audits, journey_id=journey_id, runtime=runtime, root=tmp_path)


@pytest.mark.parametrize("resolved_journey", ["chat", "voice"], indirect=True)
def test_b5_a_confirmed_restatement_ends_resolved_without_an_outcome_check_or_a_strategy_credit(resolved_journey):
    reply = resolved_journey.restate("matches")
    assert reply["text"] == "Yes, that's right." and reply["state"] == "resolved" and not reply["escalated"]
    assert "outcome_offer" not in reply and "response_id" not in reply  # nothing for the learning loop to credit
    assert {"stage": "restatement", "result": "matches", "issues": []} in reply["turn_trace"]
    assert resolved_journey.audits()[-1]["response_kind"] == "acknowledgment"
    VoiceReply.from_dict(reply)  # exactly what the voice bridge parses in production
    with FileJourneyStore(resolved_journey.root / "journeys").transaction(resolved_journey.journey_id) as transaction:
        events = transaction.record.journey.events
    assert events[-1].response_id and not events[-1].outcome_offered
    assert json.loads(transaction.record.encode())  # the stored journey still decodes


@pytest.mark.parametrize("resolved_journey", ["chat", "voice"], indirect=True)
@pytest.mark.parametrize("verdict,lead_in", [("partly", "That is partly right."), ("differs", "Not quite.")])
def test_b2_a_partly_or_differing_restatement_is_a_reviewed_explanation_with_the_approved_opening(
        resolved_journey, verdict, lead_in):
    reply = resolved_journey.restate(verdict)
    assert reply["text"].startswith(lead_in + " ") and not reply["escalated"]
    assert reply["numeric_fidelity_passed"] and reply["no_option_recommendation"]
    assert resolved_journey.audits()[-1]["response_kind"] == "explanation"
    assert {"stage": "restatement", "result": verdict, "issues": []} in reply["turn_trace"]
    VoiceReply.from_dict(reply)


@pytest.mark.parametrize("resolved_journey", ["chat"], indirect=True)
def test_a_restatement_that_cannot_be_judged_is_answered_like_any_question(resolved_journey):
    reply = resolved_journey.restate("cannot_tell")
    assert not reply["text"].startswith(("Yes, that's right.", "That is partly right.", "Not quite."))
    assert not any(item["stage"] == "restatement" and item["result"] != "cannot_tell" for item in reply["turn_trace"])
