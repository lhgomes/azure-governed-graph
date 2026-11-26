"""Ordered, retry-safe outbox delivery; no utterances, tokens or member values."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from threading import Lock
from typing import Protocol

from governedgraph.orchestrator.store import Delivery, atomic_write, validate_id, validate_message_id

log = logging.getLogger("governedgraph.orchestrator")
SENDER_IDLE_SECONDS = 8 * 60


class JourneyPublisher(Protocol):
    def send(self, journey_id: str, item: Delivery) -> None: ...


class FileJourneyPublisher:
    def __init__(self, directory: Path):
        self.directory = directory

    def send(self, journey_id: str, item: Delivery) -> None:
        validate_id(journey_id)
        validate_message_id(item.message_id)
        directory = self.directory / item.queue
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{journey_id}-{item.message_id}.json"
        content = json.dumps({"message_id": item.message_id, "session_id": journey_id, "payload": item.payload},
                             sort_keys=True)
        if path.exists():
            if path.read_text() != content:
                raise ValueError("message ID reused with different content") from None
        else:
            atomic_write(path, content)


class ServiceBusJourneyPublisher:
    def __init__(self, namespace: str):
        from azure.identity import DefaultAzureCredential
        from azure.servicebus import ServiceBusClient

        self.credential = DefaultAzureCredential()
        self.client = ServiceBusClient(namespace, self.credential, retry_total=0,
                                       logging_enable=False)
        self._lock = Lock()
        self._senders = {}
        self._last_used = {}

    def send(self, journey_id: str, item: Delivery) -> None:
        from azure.servicebus import ServiceBusMessage
        from azure.servicebus.exceptions import ServiceBusConnectionError, ServiceBusError
        from governedgraph.agents.foundry_tracing import dependency_outcome

        message = ServiceBusMessage(
            json.dumps(item.payload), session_id=journey_id, message_id=item.message_id,
            content_type="application/json", correlation_id=journey_id,
        )
        # Service Bus SDK objects are not thread-safe; reuse only under this lock.
        with self._lock:
            for attempt in range(2):
                sender = self._senders.get(item.queue)
                idle_seconds = (time.monotonic() - self._last_used[item.queue]
                                if sender is not None else None)
                if idle_seconds is not None and idle_seconds >= SENDER_IDLE_SECONDS:
                    self._senders.pop(item.queue)
                    self._last_used.pop(item.queue)
                    sender.close()
                    dependency_outcome(journey_id, "service_bus_send", "idle_recycled",
                                       queue=item.queue, attempt=attempt + 1, idle_seconds=idle_seconds)
                    sender = None
                try:
                    if sender is None:
                        sender = self.client.get_queue_sender(item.queue)
                        sender.__enter__()
                        self._senders[item.queue] = sender
                    sender.send_messages(message, timeout=20)
                    self._last_used[item.queue] = time.monotonic()
                    if attempt:
                        dependency_outcome(journey_id, "service_bus_send", "recovered",
                                           queue=item.queue, attempt=attempt + 1)
                    return
                except ServiceBusError as error:
                    self._senders.pop(item.queue, None)
                    self._last_used.pop(item.queue, None)
                    retry = isinstance(error, ServiceBusConnectionError) and attempt == 0
                    dependency_outcome(journey_id, "service_bus_send", "reconnecting" if retry else "failed",
                                       error, queue=item.queue, attempt=attempt + 1, idle_seconds=idle_seconds)
                    if sender is not None:
                        sender.close()
                    if not retry:
                        raise
                    log.warning("journey_sender_reconnecting",
                                extra={"journey_id": journey_id, "queue": item.queue,
                                       "error_type": type(error).__name__})

    def close(self) -> None:
        with self._lock:
            for sender in self._senders.values():
                sender.close()
            self._senders.clear()
            self._last_used.clear()
            self.client.close()
            self.credential.close()
