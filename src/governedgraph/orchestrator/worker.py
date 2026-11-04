"""Peek-lock consumers; settle only after durable inbox/effect commit."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path

from governedgraph.orchestrator.consumer import JourneyConsumer
from governedgraph.orchestrator.store import Delivery

log = logging.getLogger("governedgraph.orchestrator")


def handle_message(receiver, message, consumer: JourneyConsumer, queue: str) -> None:
    delivery = Delivery(queue, str(message.message_id), json.loads(str(message)))
    consumer.handle(delivery, str(message.session_id))
    receiver.complete_message(message)


class ServiceBusJourneyWorker:
    def __init__(self, namespace: str, consumer: JourneyConsumer):
        from azure.identity import DefaultAzureCredential
        from azure.servicebus import ServiceBusClient

        self.credential = DefaultAzureCredential()
        self.client = ServiceBusClient(namespace, self.credential, retry_total=0, logging_enable=False)
        self.consumer = consumer

    def _age(self, queue: str) -> None:
        with self.client.get_queue_receiver(queue_name=queue) as peek:
            messages = peek.peek_messages(max_message_count=1, timeout=5)
            age = ((datetime.now(timezone.utc) - messages[0].enqueued_time_utc).total_seconds()
                   if messages else 0)
            log.info("escalation_queue_age", extra={"queue": queue, "age_seconds": age})

    def poll(self) -> None:
        from azure.servicebus import AutoLockRenewer, NEXT_AVAILABLE_SESSION
        from azure.servicebus.exceptions import OperationTimeoutError, ServiceBusError

        for queue in ("escalations", "journey-events"):
            kwargs = {"session_id": NEXT_AVAILABLE_SESSION} if queue == "journey-events" else {}
            try:
                if queue == "escalations":
                    self._age(queue)
                with self.client.get_queue_receiver(queue_name=queue, max_wait_time=2,
                                                     prefetch_count=0, **kwargs) as receiver:
                    with AutoLockRenewer() as renewer:
                        for message in receiver.receive_messages(max_message_count=1, max_wait_time=2):
                            renewer.register(receiver, receiver.session or message, max_lock_renewal_duration=300)
                            try:
                                handle_message(receiver, message, self.consumer, queue)
                            except Exception as error:
                                log.error("journey_consumer_failed",
                                          extra={"queue": queue, "error_type": type(error).__name__})
                                receiver.abandon_message(message)
            except OperationTimeoutError as error:
                if queue == "escalations":
                    log.error("journey_receiver_unavailable", extra={"queue": queue,
                                                                     "error_type": type(error).__name__})
                continue  # a session poll can time out when no session is available
            except ServiceBusError as error:
                log.error("journey_receiver_unavailable", extra={"queue": queue,
                                                                 "error_type": type(error).__name__})


class FileJourneyWorker:
    def __init__(self, directory: Path, consumer: JourneyConsumer):
        self.directory, self.consumer = directory, consumer

    def poll(self) -> None:
        for queue in ("escalations", "journey-events"):
            messages = [json.loads(path.read_text()) for path in (self.directory / queue).glob("*.json")]
            messages.sort(key=lambda item: (item["session_id"], item["payload"].get("sequence", 0)))
            for message in messages:
                self.consumer.handle(Delivery(queue, message["message_id"], message["payload"]),
                                     message["session_id"])
