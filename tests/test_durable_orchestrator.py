"""ILLUSTRATIVE - synthetic data: restart, chaos, outbox and consumer idempotency."""
from dataclasses import asdict
from contextlib import contextmanager
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from governedgraph.agents.counsellor import Counsellor
from governedgraph.common.config import settings
from governedgraph.common.contracts import ContractError, EscalationRequest
from governedgraph.common.member_auth import MemberIdentity
from governedgraph.contact_centre.service import HANDOVER_SUMMARY, Handover, SyntheticContactCentre
from governedgraph.guardrails.numeric_fidelity import check
from governedgraph.learning.run_cycle import run_cycle
from governedgraph.orchestrator.consumer import InboxRecord, JourneyConsumer
from governedgraph.orchestrator.messaging import FileJourneyPublisher
from governedgraph.orchestrator.service import DeliveryPending, OrchestratorService
from governedgraph.orchestrator.state_machine import Journey, State
from governedgraph.orchestrator.store import Delivery, FileJourneyStore, FileRecordStore, JourneyBusy, JourneyRecord
from governedgraph.orchestrator.worker import handle_message
from governedgraph.strategy_service.service import StrategyService

IDENTITY = MemberIdentity("MBR-7Q2K", True)


@pytest.fixture
def setup(tmp_path, monkeypatch, isolated_conversations):
    monkeypatch.setattr(settings.__class__, "run_dir", property(lambda self: tmp_path))
    strategies = StrategyService(state_dir=tmp_path / "strategy", control_share=0)
    run_cycle(strategies)

    def factory(journey, auth_ref, **kwargs):
        return Counsellor(journey, auth_ref, strategy_service=strategies, **kwargs)

    def runtime(factory_override=None, publisher=None):
        return OrchestratorService(
            FileJourneyStore(tmp_path / "journeys"),
            publisher or FileJourneyPublisher(tmp_path / "queues"),
            factory_override or factory,
            continuity=isolated_conversations,
        )

    centre = SyntheticContactCentre(FileRecordStore(tmp_path / "handovers", Handover.decode))
    consumer = JourneyConsumer(FileRecordStore(tmp_path / "inbox", InboxRecord.decode), centre)
    return SimpleNamespace(runtime=runtime, factory=factory, centre=centre, consumer=consumer, root=tmp_path)


def load_record(setup, journey_id):
    with FileJourneyStore(setup.root / "journeys").transaction(journey_id) as transaction:
        return transaction.record


def queued(setup, queue):
    return [json.loads(path.read_text()) for path in (setup.root / "queues" / queue).glob("*.json")]


def test_new_journey_disclosure_event_uses_its_own_trace_root(setup, monkeypatch):
    active = []
    @contextmanager
    def span(journey_id, name, **kwargs):
        assert name == "governedgraph.orchestrator.start" and kwargs["server"] is True
        active.append(journey_id)
        yield
        active.pop()

    def event(name, **fields):
        assert name == "journey_started" and active == [fields["journey_id"]]

    monkeypatch.setattr("governedgraph.orchestrator.service.operation", span)
    capture = Mock(side_effect=event)
    monkeypatch.setattr("governedgraph.orchestrator.service.emit", capture)
    journey_id = setup.runtime().start(IDENTITY, "chat", "standard")["journey_id"]
    assert capture.call_args.kwargs["journey_id"] == journey_id


@pytest.mark.parametrize("domain", ["values_api", "search", "model", "content_safety", "strategy_service"])
def test_b9_each_dependency_failure_durably_escalates_without_estimate(setup, domain, caplog):
    def factory(*args, **kwargs):
        counsellor = setup.factory(*args, **kwargs)
        failure = Mock(side_effect=TimeoutError("PRIVATE utterance and figures must not be logged"))
        if domain == "values_api":
            counsellor.tools.values_client.get = failure
        elif domain == "search":
            counsellor.tools.search.search = failure
        elif domain == "model":
            counsellor._compose = failure
        elif domain == "content_safety":
            counsellor.screen.screen = failure
        else:
            counsellor.tools.strategy_client.get_ranked = failure
        return counsellor

    runtime = setup.runtime(factory)
    journey_id = runtime.start(IDENTITY, "chat", "standard")["journey_id"]
    result = runtime.turn(journey_id, "turn-chaos", IDENTITY, "synthetic-token-not-for-storage",
                          "Will the transition change what I receive?")
    assert result["escalated"] and result["state"] == "escalation_requested"
    assert result["strategy_id"] == "S6" and check(result["text"], None, []).passed
    record = load_record(setup, journey_id)
    assert record.journey.state == State.ESCALATION_REQUESTED
    assert record.journey.escalation_status == "requested"
    escalation = queued(setup, "escalations")
    assert len(escalation) == 1
    assert escalation[0]["message_id"] == f"{journey_id}:escalation:1"
    assert escalation[0]["session_id"] == journey_id
    text = (setup.root / "journeys" / f"{journey_id}.json").read_text()
    assert "synthetic-token" not in text and "Will the transition" not in text
    assert "1,412" not in text and "PRIVATE" not in caplog.text


def test_b1_restart_restores_teachback_and_persists_only_references(setup):
    runtime = setup.runtime()
    journey_id = runtime.start(IDENTITY, "voice", "standard")["journey_id"]
    first = runtime.turn(journey_id, "explain", IDENTITY, "", "Explain my transition letter")
    assert first["state"] == "comprehension_check" and first["strategy_id"] == "S5"
    restarted = setup.runtime()
    result = restarted.turn(journey_id, "teachback", IDENTITY, "",
                            "It stays the same and can go up or down each year.")
    assert result["state"] == "resolved" and result["strategy_id"] == "S5"
    record = load_record(setup, journey_id)
    assert record.journey.teach_back_failures == 0 and record.journey.strategies_tried == ["S5"]
    assert "1,412" not in record.encode() and "monthly_pension" not in record.encode()
    messages = queued(setup, "journey-events")
    assert len(messages) == len(record.journey.events)
    for message in messages:
        assert message["message_id"] == f"{journey_id}:{message['payload']['event_id']}"


def test_b9_duplicate_command_and_delivery_create_one_handover(setup):
    runtime = setup.runtime()
    journey_id = runtime.start(IDENTITY, "chat", "standard")["journey_id"]
    response = runtime.turn(journey_id, "advice", IDENTITY, "", "Which pension option should I choose?")
    assert response["escalated"]
    repeated = setup.runtime().turn(journey_id, "advice", IDENTITY, "", "not retained")
    assert repeated["duplicate"] is True
    message = queued(setup, "escalations")[0]
    delivery = Delivery("escalations", message["message_id"], message["payload"])
    setup.consumer.handle(delivery, journey_id)
    restarted_consumer = JourneyConsumer(FileRecordStore(setup.root / "inbox", InboxRecord.decode), setup.centre)
    restarted_consumer.handle(delivery, journey_id)
    assert len(setup.centre.advisor_queue(journey_id)) == 1
    assert len(queued(setup, "escalations")) == 1


def test_b4_b7_restart_restores_closed_language_and_taught_concepts_not_raw_conversation(setup):
    from governedgraph.common.contracts import ExplanationContext, IntentDecision

    journey_id = setup.runtime().start(IDENTITY, "voice", "standard")["journey_id"]
    context = ExplanationContext(["official_after_transition_amount"], ["official_after_transition_amount"])
    store = FileJourneyStore(setup.root / "journeys")
    with store.transaction(journey_id) as transaction:
        record = transaction.record
        record.journey.response_language = "nl"
        record.journey.last_explanation = context
        transaction.save(record)
    restored = load_record(setup, journey_id)
    assert restored.journey.response_language == "nl" and restored.journey.last_explanation == context
    captured = []
    def factory(*args, **kwargs):
        counsellor = setup.factory(*args, **kwargs)
        def decide(_tools, _utterance, previous):
            captured.append(previous)
            return IntentDecision("explanation", "transition_impact_question", "official_transition_information", "en")
        counsellor.decision_agents["intent"] = SimpleNamespace(run=decide)
        return counsellor
    result = setup.runtime(factory).turn(journey_id, "restart", IDENTITY, "",
                                        "Will the transition change what I receive?")
    assert not result["escalated"]
    assert captured[0].response_language == "nl" and captured[0].last_explanation == context
    encoded = load_record(setup, journey_id).encode()
    assert "Will the transition" not in encoded and "1,412" not in encoded
    payload = restored.journey.to_dict()
    payload["last_explanation"]["age"] = 80
    with pytest.raises(ContractError):
        Journey.from_dict(payload)


def test_lock_expiry_redelivery_applies_one_projection_and_one_handover(setup):
    runtime = setup.runtime()
    journey_id = runtime.start(IDENTITY, "chat", "standard")["journey_id"]
    runtime.turn(journey_id, "human", IDENTITY, "", "I want a human advisor")
    receiver = Mock()
    receiver.complete_message.side_effect = [RuntimeError("message lock expired"), None]
    for queue in ("journey-events", "escalations"):
        item = min(queued(setup, queue), key=lambda row: row["payload"].get("sequence", 0))

        class Message:
            session_id = journey_id
            message_id = item["message_id"]

            def __str__(self):
                return json.dumps(item["payload"])

        receiver.complete_message.side_effect = [RuntimeError("message lock expired"), None]
        with pytest.raises(RuntimeError, match="lock expired"):
            handle_message(receiver, Message(), setup.consumer, queue)
        handle_message(receiver, Message(), setup.consumer, queue)
    with setup.consumer.store.transaction(journey_id) as transaction:
        assert transaction.record.state_changes == 1
        assert len(transaction.record.handovers) == 1
    assert len(setup.centre.advisor_queue(journey_id)) == 1


def test_send_timeout_retries_same_domain_id_with_no_duplicate_effect(setup):
    ids = []
    timed_out = False

    class Publisher:
        def send(self, journey_id, item):
            nonlocal timed_out
            ids.append(item.message_id)
            setup.consumer.handle(item, journey_id)
            if item.queue == "escalations" and not timed_out:
                timed_out = True
                raise TimeoutError("send accepted but acknowledgment lost")

    runtime = setup.runtime(publisher=Publisher())
    journey_id = runtime.start(IDENTITY, "chat", "standard")["journey_id"]
    with pytest.raises(DeliveryPending):
        runtime.turn(journey_id, "human", IDENTITY, "", "Please get a human")
    record = load_record(setup, journey_id)
    assert record.journey.state == State.ESCALATION_REQUESTED and record.pending
    setup.runtime(publisher=Publisher()).recover()
    assert ids.count(f"{journey_id}:escalation:1") == 2
    assert len(setup.centre.advisor_queue(journey_id)) == 1
    assert not load_record(setup, journey_id).pending


def test_handover_effect_before_inbox_commit_is_itself_idempotent(setup, monkeypatch):
    request = EscalationRequest("J-test", "member_request", HANDOVER_SUMMARY, [],
                                escalation_id="J-test:escalation:1")
    original = setup.centre.handover
    calls = []

    def crash_after_effect(request, key):
        calls.append(original(request, key))
        if len(calls) == 1:
            raise RuntimeError("consumer crashed before receipt commit")
        return calls[-1]

    monkeypatch.setattr(setup.centre, "handover", crash_after_effect)
    delivery = Delivery("escalations", request.escalation_id, asdict(request))
    with pytest.raises(RuntimeError):
        setup.consumer.handle(delivery, request.journey_id)
    setup.consumer.handle(delivery, request.journey_id)
    assert calls[0] == calls[1] and len(setup.centre.advisor_queue("J-test")) == 1


def test_b9_pending_event_delivery_cannot_block_new_human_request(setup):
    failed = False
    sent = []
    local = FileJourneyPublisher(setup.root / "queues")

    class Publisher:
        def send(self, journey_id, item):
            if failed and item.queue == "journey-events":
                raise TimeoutError("journey event queue unavailable")
            sent.append(item.queue)
            local.send(journey_id, item)

    runtime = setup.runtime(publisher=Publisher())
    journey_id = runtime.start(IDENTITY, "voice", "standard")["journey_id"]
    failed = True
    with pytest.raises(DeliveryPending):
        runtime.turn(journey_id, "explain", IDENTITY, "", "Explain my transition letter")
    with pytest.raises(DeliveryPending):
        runtime.turn(journey_id, "need-human", IDENTITY, "", "Please get me a human advisor")
    assert sent[-1] == "escalations"
    assert len(queued(setup, "escalations")) == 1
    assert load_record(setup, journey_id).journey.state == State.ESCALATION_REQUESTED


def test_b1_stepup_gate_is_rechecked_and_tools_cannot_bypass_it(setup):
    def factory(*args, **kwargs):
        counsellor = setup.factory(*args, **kwargs)
        counsellor.tools.values_client.get = Mock(side_effect=AssertionError("personal values accessed"))
        with pytest.raises(PermissionError, match="step-up"):
            counsellor.tools.get_member_values()
        return counsellor

    runtime = setup.runtime(factory)
    identity = MemberIdentity(IDENTITY.member_ref, False)
    journey_id = runtime.start(identity, "voice", "standard")["journey_id"]
    response = runtime.turn(journey_id, "general", identity, "", "Explain the transition letter")
    assert "step-up" in response["text"] and not response["escalated"]
    personal = setup.runtime().turn(journey_id, "verified", IDENTITY, "", "Explain my transition letter")
    assert personal["strategy_id"] == "S5" and personal["state"] == "comprehension_check"


def test_b7_persisted_state_and_consumer_contracts_reject_unknown_fields(setup):
    payload = Journey("J-test", "MBR-7Q2K").to_dict()
    with pytest.raises(ContractError):
        JourneyRecord.decode(json.dumps({"journey": {**payload, "age_band": "75+"}}))
    with pytest.raises(ContractError):
        Journey.from_dict({**payload, "utterance": "private"})
    with pytest.raises(ContractError):
        EscalationRequest.from_dict({"journey_id": "J-test", "age": 77})


def test_concurrent_replica_cannot_interleave_journey_transitions(setup):
    store = FileJourneyStore(setup.root / "journeys")
    with store.transaction("J-test"):
        with pytest.raises(JourneyBusy):
            with FileJourneyStore(setup.root / "journeys").transaction("J-test"):
                pytest.fail("second replica must not enter a leased journey")


@pytest.mark.parametrize("renewal_fails", [False, True])
def test_b9_blob_lease_is_renewed_and_loss_fences_commit(monkeypatch, renewal_fails):
    import sys
    from types import ModuleType
    import governedgraph.orchestrator.store as stores

    class AzureError(Exception):
        pass

    class HttpResponseError(AzureError):
        pass

    class ResourceExistsError(HttpResponseError):
        pass

    exceptions = ModuleType("azure.core.exceptions")
    exceptions.AzureError = AzureError
    exceptions.HttpResponseError = HttpResponseError
    exceptions.ResourceExistsError = ResourceExistsError
    for name in ("azure", "azure.core"):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    monkeypatch.setitem(sys.modules, "azure.core.exceptions", exceptions)
    lease = Mock()
    if renewal_fails:
        lease.renew.side_effect = AzureError("lease renewal transport failure")
    blob = Mock()
    blob.acquire_lease.return_value = lease
    record = JourneyRecord(Journey("J-test", "MBR-7Q2K"))
    blob.download_blob.return_value.readall.return_value = record.encode().encode()
    store = stores.BlobRecordStore.__new__(stores.BlobRecordStore)
    store.container, store.decoder = Mock(), JourneyRecord.decode
    store.container.get_blob_client.return_value = blob

    class Worker:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

        def join(self):
            pass

    event = Mock()
    event.wait.side_effect = [False, True]
    monkeypatch.setattr(stores, "threading", SimpleNamespace(Event=lambda: event, Thread=Worker))
    if renewal_fails:
        with pytest.raises(stores.LeaseLost):
            with store.transaction("J-test") as transaction:
                transaction.save(record)
        blob.upload_blob.assert_not_called()
    else:
        with store.transaction("J-test") as transaction:
            transaction.save(record)
        assert blob.upload_blob.call_args.kwargs == {"overwrite": True, "lease": lease}
    blob.acquire_lease.assert_called_once_with(lease_duration=60)
    lease.renew.assert_called_once()
    lease.release.assert_called_once()
