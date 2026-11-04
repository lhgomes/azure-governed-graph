"""Durable inboxes: atomically apply event projections; idempotently request handovers."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from typing import Protocol

from governedgraph.common.contracts import EscalationRequest, JourneyEvent, _strict
from governedgraph.orchestrator.state_machine import State
from governedgraph.orchestrator.store import Delivery, validate_message_id


class ContactCentre(Protocol):
    def handover(self, request: EscalationRequest, idempotency_key: str) -> str: ...


@dataclass
class InboxRecord:
    journey_id: str
    processed_messages: list[str] = field(default_factory=list)
    state: str = State.INITIATED.value
    last_sequence: int = 0
    state_changes: int = 0
    handovers: dict[str, str] = field(default_factory=dict)

    def encode(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def decode(cls, text: str) -> "InboxRecord":
        record = _strict(cls, json.loads(text))
        State(record.state)
        for message_id in record.processed_messages:
            validate_message_id(message_id)
        return record


class JourneyConsumer:
    def __init__(self, store, contact_centre: ContactCentre):
        self.store = store
        self.contact_centre = contact_centre

    def handle(self, delivery: Delivery, session_id: str) -> None:
        delivery = Delivery.from_dict(asdict(delivery))
        journey_id = delivery.payload["journey_id"]
        if session_id != journey_id:
            raise ValueError("queue session does not match journey")
        with self.store.transaction(journey_id) as transaction:
            record = transaction.record or InboxRecord(journey_id)
            if delivery.message_id in record.processed_messages:
                return
            if delivery.queue == "journey-events":
                event = JourneyEvent.from_dict(delivery.payload)
                if delivery.message_id != f"{journey_id}:{event.event_id}":
                    raise ValueError("event message ID does not match domain ID")
                if event.sequence != record.last_sequence + 1 or event.from_state != record.state:
                    raise ValueError("out-of-order journey event")
                State(event.to_state)
                record.state = event.to_state
                record.last_sequence = event.sequence
                record.state_changes += 1
            else:
                request = EscalationRequest.from_dict(delivery.payload)
                if delivery.message_id != request.escalation_id:
                    raise ValueError("escalation message ID does not match domain ID")
                handover = self.contact_centre.handover(request, request.escalation_id)
                record.handovers[request.escalation_id] = handover
            record.processed_messages.append(delivery.message_id)
            transaction.save(record)
