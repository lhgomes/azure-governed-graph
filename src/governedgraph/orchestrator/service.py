"""Durable orchestration of request-scoped counsellors and a transactional outbox."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import logging
from time import perf_counter
from uuid import uuid4

from governedgraph.agents.counsellor import DISCLOSURE, Counsellor, TurnResult
from governedgraph.common.contracts import EscalationRequest, RequestKey
from governedgraph.common.domain import message
from governedgraph.common.member_auth import MemberIdentity
from governedgraph.common.tracing import traceparent
from governedgraph.observability import contact_key, emit, operation
from governedgraph.contact_centre.service import HANDOVER_SUMMARY
from governedgraph.explainability.store import (
    AuditConflict, AuditUnavailable, QueuedExplainabilityStore, default_store, enqueue_in_transaction, system_record,
)
from governedgraph.explainability.reconcile import retry_audits
from governedgraph.orchestrator.messaging import JourneyPublisher
from governedgraph.orchestrator.state_machine import Event, Journey, State
from governedgraph.orchestrator.store import Delivery, JourneyBusy, JourneyRecord, JourneyStore

log = logging.getLogger("governedgraph.orchestrator")

def response_reference(journey_id: str, request_id: str) -> str:
    return hashlib.sha256(f"{journey_id}:{request_id}".encode()).hexdigest()


class MemberScopeViolation(PermissionError):
    pass


class DeliveryPending(RuntimeError):
    pass


class OrchestratorService:
    def __init__(self, store: JourneyStore, publisher: JourneyPublisher,
                 counsellor_factory=Counsellor, *, contact_centre=None, worker=None, audit_store=None,
                 feedback_store=None):
        self.store = store
        self.publisher = publisher
        self.counsellor_factory = counsellor_factory
        self.contact_centre = contact_centre
        self.worker = worker
        self.audit_store = audit_store if audit_store is not None else default_store()
        self.feedback_store = feedback_store
        self.feedback_manifest = None

    def _audit(self, transaction, record, audit) -> None:
        QueuedExplainabilityStore(
            self.audit_store, lambda item: enqueue_in_transaction(transaction, record, item),
        ).append(audit)

    def _flush(self, transaction, record: JourneyRecord) -> None:
        retry_audits(transaction, record, self.audit_store)
        for queue in ("escalations", "journey-events"):
            while item := next((item for item in record.pending if item.queue == queue), None):
                try:
                    self.publisher.send(record.journey.journey_id, item)
                except Exception as error:
                    log.error("journey_outbox_delivery_failed",
                              extra={"journey_id": record.journey.journey_id, "queue": queue,
                                     "error_type": type(error).__name__})
                    raise DeliveryPending(record.journey.journey_id) from error
                record.pending.remove(item)
                transaction.save(record)

    def recover(self) -> None:
        for journey_id in self.store.ids():
            try:
                with self.store.transaction(journey_id) as transaction:
                    if transaction.record:
                        self._flush(transaction, transaction.record)
            except JourneyBusy:
                continue
            except DeliveryPending:
                continue  # already logged; the next recovery pass retries the committed outbox
        if self.worker:
            self.worker.poll()
        if self.feedback_store is not None:
            from governedgraph.learning.feedback_store import BaselinePending, publish_feedback

            try:
                self.feedback_manifest = publish_feedback(self.store, self.audit_store, self.feedback_store)
            except JourneyBusy:
                log.info("feedback_projection_deferred_busy")
            except BaselinePending:
                log.warning("feedback_baseline_pending")

    @staticmethod
    def _events(record: JourneyRecord, previous_count: int) -> None:
        for event in record.journey.events[previous_count:]:
            record.pending.append(Delivery("journey-events", f"{event.journey_id}:{event.event_id}", asdict(event)))

    def start(self, identity: MemberIdentity, channel: str, accessibility_mode: str) -> dict:
        RequestKey("first_contact", "unclassified", channel, accessibility_mode)
        journey = Journey(f"J-{uuid4().hex}", identity.member_ref, channel=channel,
                          accessibility_mode=accessibility_mode)
        with operation(journey.journey_id, "governedgraph.orchestrator.start",
                      agent_name="gg-orchestrator", server=True):
            journey.fire(Event.DISCLOSED_AND_AUTHENTICATED)
            record = JourneyRecord(journey)
            with self.store.transaction(journey.journey_id) as transaction:
               response_id = response_reference(journey.journey_id, "disclosure")
               self._audit(transaction, record, system_record(journey, response_id, "disclosure"))
               journey.record_response(response_id, 0)
               self._events(record, 0)
               transaction.save(record)
               self._flush(transaction, record)
            emit("journey_started", event_id=f"{journey.journey_id}:start", journey_id=journey.journey_id,
                 contact_key=contact_key(identity.member_ref), agent_name="gg-orchestrator")
            from governedgraph.agents.foundry_tracing import record_synthetic_conversation

            record_synthetic_conversation(journey.journey_id, identity.member_ref, None, DISCLOSURE, response_id)
        return {"journey_id": journey.journey_id, "text": DISCLOSURE, "state": journey.state.value}

    def turn(self, journey_id: str, request_id: str, identity: MemberIdentity,
             token: str, utterance: str, trace_parent: str = "", *, voice_model: str = "") -> dict:
        started = perf_counter()
        received_at = datetime.now(timezone.utc).isoformat()
        with self.store.transaction(journey_id) as transaction:
            record = transaction.record
            if record is None:
                raise KeyError(journey_id)
            journey = record.journey
            if journey.member_ref != identity.member_ref:
                log.error("member_scope_violation", extra={"journey_id": journey_id})
                raise MemberScopeViolation(journey_id)
            if request_id in record.processed_requests:
                self._flush(transaction, record)
                return {"journey_id": journey_id, "state": journey.state.value,
                        "request_id": request_id, "duplicate": True}
            if journey.state in (State.ESCALATION_REQUESTED, State.HANDED_OVER, State.CLOSED):
                count = len(journey.events)
                response_id = response_reference(journey_id, request_id)
                self._audit(transaction, record, system_record(journey, response_id, "acknowledgment"))
                journey.record_response(response_id, count)
                self._events(record, count)
                record.processed_requests.append(request_id)
                transaction.save(record)
                self._flush(transaction, record)
                return {"journey_id": journey_id, "state": journey.state.value,
                        "text": message("journey_closed"),
                        "escalated": journey.state != State.CLOSED}
            count = len(journey.events)
            was_resolved = journey.state == State.RESOLVED
            if was_resolved:
                emit("journey_contact", event_id=f"{journey_id}:contact:{request_id}", journey_id=journey_id,
                     contact_key=contact_key(identity.member_ref), agent_name="gg-orchestrator",
                     occurred_at=received_at)
            requests: list[EscalationRequest] = []
            counsellor = None
            try:
                counsellor = self.counsellor_factory(
                    journey, identity.member_ref, member_token=token,
                    step_up_verified=identity.step_up_verified,
                    audit_enqueue=lambda item: enqueue_in_transaction(transaction, record, item),
                    audit_store=self.audit_store,
                    **({"voice_model": voice_model} if voice_model else {}),
                )
                counsellor.tools.response_id = response_reference(journey_id, request_id)
                counsellor.tools.trace_parent = traceparent(trace_parent)
                counsellor._current_strategy = journey.strategies_tried[-1] if journey.strategies_tried else None
                loaded = perf_counter()
                result = counsellor.handle(utterance)
                result.stage_ms["durable_load"] = (loaded - started) * 1000
                requests = counsellor.tools.trace.escalations
            except Exception as error:
                log.error("orchestrator_turn_failed",
                          extra={"journey_id": journey_id, "error_type": type(error).__name__})
                journey.fire(Event.ESCALATE)
                result = TurnResult("A service is unavailable. I'm requesting help from a human advisor.",
                                    journey.state.value, "S6", escalated=True)
                response_id = response_reference(journey_id, request_id + ":failure")
                try:
                    self._audit(transaction, record, system_record(journey, response_id, "escalation"))
                    journey.record_response(response_id, count)
                except AuditUnavailable:
                    journey.withhold_response()
                    result.audit_status = "unavailable"
            if journey.state == State.ESCALATION_REQUESTED:
                reason = requests[-1].reason_code if requests else "component_failure"
                if reason not in {"advice_intent", "member_request", "teach_back_failed", "component_failure",
                                  "conversation_limit", "safety"}:
                    reason = "safety"
                record.escalation_sequence += 1
                escalation_id = f"{journey_id}:escalation:{record.escalation_sequence}"
                escalation = EscalationRequest(
                    journey_id, reason, HANDOVER_SUMMARY,
                    [value.value_id for value in counsellor.tools.trace.values.values]
                    if counsellor is not None and counsellor.tools.trace.values else [],
                    escalation_id=escalation_id,
                )
                record.pending.append(Delivery("escalations", escalation.escalation_id, asdict(escalation)))
            committing = perf_counter()
            self._events(record, count)
            record.processed_requests.append(request_id)
            transaction.save(record)
            self._flush(transaction, record)
            result.stage_ms["durable_commit"] = (perf_counter() - committing) * 1000
            if result.audit_status == "unavailable":
                raise AuditUnavailable("response withheld; human escalation requested")
            out = asdict(result)
            out.pop("fidelity", None)
            if self.feedback_manifest is not None:
                out["learning_feedback"] = asdict(self.feedback_manifest)
            emit("journey_turn_delivered", event_id=f"{journey_id}:{request_id}", journey_id=journey_id,
                 request_id=request_id, contact_key=contact_key(identity.member_ref),
                 agent_name="gg-counsellor", state=journey.state.value,
                 arm=result.arm or "n/a", duration_ms=(perf_counter() - started) * 1000,
                 escalated=result.escalated, numeric_fidelity_passed=result.numeric_fidelity_passed,
                 strategy_id=result.strategy_id or "none")
            if (result.audit_status in {"committed", "queued"}
                    and result.numeric_fidelity_passed and result.no_option_recommendation):
                from governedgraph.agents.foundry_tracing import record_synthetic_conversation

                record_synthetic_conversation(journey_id, identity.member_ref, utterance,
                                              result.text, response_reference(journey_id, request_id))
            return out

    def explainability_report(self, member_ref: str, identity: MemberIdentity) -> dict:
        from governedgraph.agents.fairness_explainability import assemble_explainability_report

        if member_ref != identity.member_ref or not identity.step_up_verified:
            log.warning("member_report_access_denied")
            raise MemberScopeViolation("verified member access required")
        rows = self.audit_store.read_member(member_ref)
        pending = []
        delivered = set()
        for journey_id in self.store.ids():
            with self.store.transaction(journey_id) as transaction:
                record = transaction.record
                if record and record.journey.member_ref == member_ref:
                    pending.extend(asdict(item) for item in record.pending_audits)
                    delivered.update(event.response_id for event in record.journey.events if event.response_id)
        known = {(r["journey_id"], r["response_id"]): r for r in rows if r.get("response_id")}
        for row in pending:
            key = (row["journey_id"], row["response_id"])
            if key in known:
                if known[key] != row:
                    log.error("explainability_record_conflict", extra={"journey_id": row["journey_id"]})
                    raise AuditConflict("queued and committed audit records disagree")
            else:
                rows.append(row)
                known[key] = row
        report = assemble_explainability_report(member_ref, rows)
        report["pending_response_ids"] = sorted({r["response_id"] for r in pending})
        report["delivered_response_ids"] = sorted(delivered)
        return report

    def advisor_queue(self, journey_id: str, identity: MemberIdentity) -> dict:
        with self.store.transaction(journey_id) as transaction:
            if transaction.record is None:
                raise KeyError(journey_id)
            if transaction.record.journey.member_ref != identity.member_ref:
                log.error("member_scope_violation", extra={"journey_id": journey_id})
                raise MemberScopeViolation(journey_id)
        return {"data": "ILLUSTRATIVE - synthetic data",
                "handovers": self.contact_centre.advisor_queue(journey_id)}
