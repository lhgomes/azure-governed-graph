"""L3 offline stubs: document references only; no entitlement or eligibility decisions."""
from __future__ import annotations

from dataclasses import asdict
from typing import Callable

from pension_wtp.agents.design_contracts import DocumentCheck, EventOutcome, EventReason, IdempotentWrites, LifeEvent
from governedgraph.common.contracts import ContractError

ROUTINE = frozenset({"employment_change", "retirement"})
ALWAYS_HUMAN = frozenset({"divorce", "death"})


class EventProcessingAgent:
    TOOLS = ("ingest_life_event", "check_documents", "submit_event_to_administration",
             "route_to_exception_queue")

    def __init__(self, *, document_checker: Callable[[LifeEvent], DocumentCheck] | None = None):
        self.document_checker = document_checker
        self.events: dict[str, LifeEvent] = {}
        self.exception_queue: list[EventOutcome] = []
        self.submissions: list[EventOutcome] = []
        self._processed: dict[str, tuple[dict, EventOutcome]] = {}
        self._writes = IdempotentWrites()

    def ingest_life_event(self, event: LifeEvent, *, idempotency_key: str) -> LifeEvent:
        def ingest() -> LifeEvent:
            previous = self.events.get(event.event_id)
            if previous is not None and previous != event:
                raise ContractError("event ID reused with different content")
            self.events[event.event_id] = event
            return event

        return self._writes.apply("ingest", idempotency_key, asdict(event), ingest)

    def check_documents(self, event_id: str) -> DocumentCheck:
        event = self.events[event_id]
        if self.document_checker is None:
            raise NotImplementedError("trusted document completeness/authenticity checker is not configured")
        check = self.document_checker(event)
        if not isinstance(check, DocumentCheck) or check.event_id != event_id:
            raise ContractError("document checker returned a mismatched or invalid result")
        return check

    def submit_event_to_administration(self, event_id: str, *, idempotency_key: str) -> EventOutcome:
        event = self.events[event_id]
        if event.event_type in ALWAYS_HUMAN:
            return self.route_to_exception_queue(event_id, "complex_case_human_review",
                                                 idempotency_key=idempotency_key)
        check = self.check_documents(event_id)
        return self.process(event_id, event.event_type, check.complete, idempotency_key,
                            documents_authentic=check.authentic)

    def route_to_exception_queue(self, event_id: str, reason_code: EventReason = "member_request", *,
                                 idempotency_key: str) -> EventOutcome:
        outcome = EventOutcome(event_id, "exception_queue", reason_code)

        def enqueue() -> EventOutcome:
            if outcome not in self.exception_queue:
                self.exception_queue.append(outcome)
            return outcome

        return self._writes.apply("exception", idempotency_key, asdict(outcome), enqueue)

    def process(self, event_id: str, event_type: str, documents_complete: bool,
                idempotency_key: str, *, documents_authentic: bool = True) -> EventOutcome:
        """Compatibility seam: flags are trusted checker results, never document instructions."""
        if type(documents_complete) is not bool or type(documents_authentic) is not bool:
            raise ContractError("trusted document checks must be boolean")
        payload = {"event_id": event_id, "event_type": event_type,
                   "complete": documents_complete, "authentic": documents_authentic}

        def route() -> EventOutcome:
            if event_id in self._processed:
                previous, result = self._processed[event_id]
                if previous != payload:
                    raise ContractError("event ID reused with a different submission")
                return result
            reason: EventReason
            if event_type in ALWAYS_HUMAN:
                reason = "complex_case_human_review"
            elif event_type not in ROUTINE:
                reason = "unknown_event_type"
            elif not documents_complete:
                reason = "documents_incomplete"
            elif not documents_authentic:
                reason = "documents_unverified"
            else:
                result = EventOutcome(event_id, "submitted", "routine_rule_covered",
                                      administrator_ref=f"STUB-ADM-{event_id}")
                self.submissions.append(result)
                self._processed[event_id] = (payload, result)
                return result
            result = self.route_to_exception_queue(event_id, reason, idempotency_key=idempotency_key)
            self._processed[event_id] = (payload, result)
            return result

        return self._writes.apply("submit", idempotency_key, payload, route)
