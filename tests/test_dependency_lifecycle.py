"""ILLUSTRATIVE - synthetic data. Expected outcomes never hide genuine dependency errors."""
from contextlib import contextmanager
from threading import Lock
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from governedgraph.agents import foundry_tracing
from governedgraph.orchestrator import messaging
from governedgraph.orchestrator.store import BlobJourneyStore, Delivery, JourneyRecord
from governedgraph.orchestrator.state_machine import Journey


@pytest.mark.parametrize("exists,race", [(True, False), (False, False), (False, True)])
def test_b4_blob_initialization_does_not_write_existing_state_and_preserves_create_races(exists, race):
    from azure.core.exceptions import ResourceExistsError

    lease, blob = Mock(), Mock()
    record = JourneyRecord(Journey("J-synthetic", "MBR-7Q2K"))
    blob.exists.return_value = exists
    blob.acquire_lease.return_value = lease
    blob.download_blob.return_value.readall.return_value = record.encode().encode()
    if race:
        error = ResourceExistsError("synthetic collision")
        error.error_code, error.status_code = "BlobAlreadyExists", 409
        blob.upload_blob.side_effect = error
    store = BlobJourneyStore.__new__(BlobJourneyStore)
    store.container = Mock(get_blob_client=Mock(return_value=blob))
    store.decoder = JourneyRecord.decode
    with store.transaction("J-synthetic") as transaction:
        assert transaction.record.journey.journey_id == "J-synthetic"
    if exists:
        blob.upload_blob.assert_not_called()
    else:
        blob.upload_blob.assert_called_once_with("null", overwrite=False)
    blob.acquire_lease.assert_called_once_with(lease_duration=60)
    blob.download_blob.assert_called_once_with(lease=lease)
    lease.release.assert_called_once()


def test_b4_unexpected_blob_conflict_is_not_reported_as_success():
    from azure.core.exceptions import ResourceExistsError

    blob = Mock()
    blob.exists.return_value = False
    error = ResourceExistsError("synthetic conflict")
    error.error_code = "ContainerBeingDeleted"
    blob.upload_blob.side_effect = error
    store = BlobJourneyStore.__new__(BlobJourneyStore)
    store.container = Mock(get_blob_client=Mock(return_value=blob))
    with pytest.raises(ResourceExistsError), store.transaction("J-synthetic"):
        pytest.fail("unexpected conflict must not enter the transaction")
    blob.acquire_lease.assert_not_called()


@pytest.mark.parametrize("queue", ["journey-events", "escalations"])
def test_b9_idle_sender_recycled_before_send_without_replaying_or_changing_identity(monkeypatch, queue):
    old, fresh = MagicMock(), MagicMock()
    publisher = messaging.ServiceBusJourneyPublisher.__new__(messaging.ServiceBusJourneyPublisher)
    publisher._lock = Lock()
    publisher._senders = {queue: old}
    publisher._last_used = {queue: 10.0}
    publisher.client = Mock(get_queue_sender=Mock(return_value=fresh))
    monkeypatch.setattr(messaging.time, "monotonic", lambda: 10.0 + messaging.SENDER_IDLE_SECONDS)
    item = Delivery(queue, "J-synthetic:stable-1", {"data": "ILLUSTRATIVE - synthetic data"})
    publisher.send("J-synthetic", item)
    old.close.assert_called_once()
    old.send_messages.assert_not_called()
    fresh.send_messages.assert_called_once()
    message = fresh.send_messages.call_args.args[0]
    assert message.message_id == item.message_id and message.session_id == "J-synthetic"
    assert publisher._senders == {queue: fresh}


@pytest.mark.parametrize("detail,cause,condition", [
    ("IdleTimerExpired; amqp:link:detach-forced", "idle_timeout", "amqp:link:detach-forced"),
    ("handler has already been shutdown", "handler_shutdown", None),
    ("ConnectionResetError: private payload", "connection_reset", None),
    ("private payload and credentials", "unknown", None),
])
def test_b4_dependency_diagnostics_export_only_closed_cause_and_condition(
        monkeypatch, detail, cause, condition):
    from azure.servicebus.exceptions import ServiceBusConnectionError

    attributes = {}
    @contextmanager
    def span(name, **kwargs):
        attributes.update(kwargs["attributes"])
        yield SimpleNamespace(set_attribute=lambda key, value: attributes.update({key: value}))
    monkeypatch.setattr(foundry_tracing, "settings", SimpleNamespace(is_azure=True))
    monkeypatch.setattr(foundry_tracing, "configure_tracing", lambda: SimpleNamespace(
        get_tracer=lambda _: SimpleNamespace(start_as_current_span=span)))
    foundry_tracing.dependency_outcome(
        "J-synthetic", "service_bus_send", "reconnecting", ServiceBusConnectionError(message=detail),
        queue="escalations", attempt=1, idle_seconds=601.9)
    assert attributes["governedgraph.dependency.cause"] == cause
    assert attributes.get("governedgraph.dependency.amqp_condition") == condition
    assert attributes["governedgraph.dependency.idle_seconds"] == 601
    assert attributes["governedgraph.dependency.queue"] == "escalations"
    assert attributes["governedgraph.dependency.attempt"] == 1
    assert "private" not in repr(attributes) and detail not in repr(attributes)


def test_b9_terminal_send_failure_diagnostic_remains_an_error(monkeypatch):
    from azure.servicebus.exceptions import ServiceBusConnectionError

    recorded = Mock()
    @contextmanager
    def span(*args, **kwargs):
        yield recorded
    monkeypatch.setattr(foundry_tracing, "settings", SimpleNamespace(is_azure=True))
    monkeypatch.setattr(foundry_tracing, "configure_tracing", lambda: SimpleNamespace(
        get_tracer=lambda _: SimpleNamespace(start_as_current_span=span)))
    foundry_tracing.dependency_outcome(
        "J-synthetic", "service_bus_send", "failed", ServiceBusConnectionError(message="private"),
        queue="journey-events", attempt=2)
    assert recorded.set_status.call_args.args[0].status_code.name == "ERROR"
    assert "private" not in repr(recorded.mock_calls)
