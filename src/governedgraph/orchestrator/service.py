"""Durable orchestration of request-scoped counsellors and a transactional outbox."""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import os
import re
from time import perf_counter
from uuid import uuid4

from governedgraph.agents.counsellor import DISCLOSURE, Counsellor, TurnResult
from governedgraph.common.contracts import (
    INTENT_DECISIONS, EscalationRequest, ExplainabilityRecord, ExplanationContext,
    OutcomeOffer, OutcomeSubmission, RequestKey, ResponseReference,
)
from governedgraph.common import domain
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


def _certainly_before(when: datetime, serialized: str, cutoff: datetime) -> bool:
    if when.tzinfo is None:
        return False
    # Historic events have coarser timestamps; do not infer order within their recorded interval.
    fraction = re.search(r"[T ]\d{2}:\d{2}:\d{2}[.,](\d+)", serialized)
    digits = min(len(fraction.group(1)), 6) if fraction else 0
    if digits < 6:
        return when + timedelta(microseconds=10 ** (6 - digits)) <= cutoff
    return when < cutoff


def _arrival(received_at: datetime | None) -> datetime:
    now = datetime.now(timezone.utc)
    if received_at is None:
        return now
    if received_at.tzinfo is None or received_at.utcoffset() is None or received_at > now:
        raise ValueError("request arrival requires a non-future, timezone-aware timestamp")
    return received_at


class MemberScopeViolation(PermissionError):
    pass


class DeliveryPending(RuntimeError):
    pass


class InvalidAnswerReference(ValueError):
    pass


class OutcomeConflict(ValueError):
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

    def start(self, identity: MemberIdentity, channel: str, accessibility_mode: str, *,
              test_strategy_id: str = "") -> dict:
        RequestKey("first_contact", "unclassified", channel, accessibility_mode)
        journey = Journey(f"J-{uuid4().hex}", identity.member_ref, channel=channel,
                          accessibility_mode=accessibility_mode, test_strategy_id=test_strategy_id)
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

    def _explanation(self, record: JourneyRecord, reference: ResponseReference, identity: MemberIdentity,
                     *, offered: bool = False, require_playback: bool = True,
                     before: datetime | None = None) -> ExplainabilityRecord:
        journey = record.journey
        cutoff = before if before is not None else datetime.now(timezone.utc)
        if journey.journey_id != reference.journey_id or journey.member_ref != identity.member_ref:
            raise InvalidAnswerReference("answer reference is not available for this member")
        delivered = [event for event in journey.events
                     if event.response_id == reference.response_id and not event.response_withheld]
        if len(delivered) != 1 or (offered and not delivered[0].outcome_offered):
            raise InvalidAnswerReference("answer was not delivered with the required evidence")
        try:
            created = datetime.fromisoformat(delivered[0].created_at)
        except ValueError as error:
            raise InvalidAnswerReference("invalid answer timestamp") from error
        if not _certainly_before(created, delivered[0].created_at, cutoff):
            raise InvalidAnswerReference("answer must precede this contact")
        audits = [item for item in record.pending_audits
                  if item.response_id == reference.response_id and item.journey_id == reference.journey_id]
        if not audits:
            audits = [ExplainabilityRecord.from_dict(item) for item in self.audit_store.read_member(identity.member_ref)
                      if item.get("response_id") == reference.response_id
                      and item.get("journey_id") == reference.journey_id]
        audits = [item for index, item in enumerate(audits) if item not in audits[:index]]
        if (len(audits) != 1 or audits[0].member_ref != identity.member_ref
                or audits[0].response_kind not in {"explanation", "general_information"}
                or not audits[0].numeric_fidelity_passed):
            raise InvalidAnswerReference("answer lacks a matching checked audit record")
        try:
            audited_at = datetime.fromisoformat(audits[0].created_at)
        except ValueError as error:
            raise InvalidAnswerReference("invalid audit timestamp") from error
        if audited_at.tzinfo is None or audited_at > created:
            raise InvalidAnswerReference("answer audit must precede delivery")
        if journey.channel == "voice" and require_playback:
            confirmations = [event for event in journey.events
                             if event.event == Event.PLAYBACK_CONFIRMED.value
                             and event.playback_response_id == reference.response_id]
            if len(confirmations) != 1:
                raise InvalidAnswerReference("voice answer requires confirmed playback")
            try:
                played = datetime.fromisoformat(confirmations[0].created_at)
            except ValueError as error:
                raise InvalidAnswerReference("invalid playback timestamp") from error
            if (played.tzinfo is None or played < created
                    or not _certainly_before(played, confirmations[0].created_at, cutoff)
                    or confirmations[0].sequence <= delivered[0].sequence):
                raise InvalidAnswerReference("playback must follow the checked answer")
        return audits[0]

    def _linked_answer(self, record: JourneyRecord, reference: ResponseReference, identity: MemberIdentity,
                       *, before: datetime) -> ExplainabilityRecord:
        if record.journey.test_strategy_id:
            raise InvalidAnswerReference("test conversations cannot link learning evidence")
        if reference.journey_id == record.journey.journey_id:
            return self._explanation(record, reference, identity, before=before)
        with self.store.transaction(reference.journey_id) as target:
            if target.record is None or target.record.journey.test_strategy_id:
                raise InvalidAnswerReference("answer reference is not available for this member")
            return self._explanation(target.record, reference, identity, before=before)

    @staticmethod
    def _answer_context(journey: Journey, audit: ExplainabilityRecord):
        from governedgraph.agents.decision_agents import DecisionContext

        intent = audit.request_key["intent"]
        if intent not in {row[1] for row in INTENT_DECISIONS}:
            log.warning("linked_answer_intent_unavailable", extra={"journey_id": journey.journey_id})
            intent = "unclassified"
        review = next((item.decision for item in reversed(audit.decision_evidence)
                       if item.role == "answer-review"), None)
        routing = next((item.decision for item in audit.decision_evidence
                        if item.role == "intent" and item.decision["route"] == "explanation"), None)
        previous = (ExplanationContext(review["concepts"], review["teach_back_concepts"],
                                       routing["reason"] if routing else None)
                    if review is not None and review["verdict"] == "pass" else None)
        return DecisionContext(journey.state.value, intent, None, journey.response_language, previous)

    def _offer_outcome(self, counsellor: Counsellor, result: TurnResult,
                       record: JourneyRecord) -> OutcomeOffer | None:
        # Explicitly scoped to synthetic Azure gg-dev; other environments fail closed.
        if (os.getenv("AZURE_ENV_NAME") != "gg-dev"
                or os.getenv("GOVERNEDGRAPH_ENABLE_OUTCOME_CHECK") != "true"):
            return None
        from governedgraph.guardrails.advice_intent import response_steers_choice
        from governedgraph.guardrails.numeric_fidelity import check

        audit = counsellor.tools.trace.audit_record
        if (audit is None or result.escalated or result.state == State.COMPREHENSION_CHECK.value
                or result.audit_status not in {"committed", "queued"}):
            return None
        if (audit.response_kind == "acknowledgment"
                and record.journey.events[-1].event == Event.TEACH_BACK_PASSED.value):
            previous = next((event for event in reversed(record.journey.events[:-1])
                             if event.event == Event.RESPONSE_DELIVERED.value
                             and event.to_state == State.COMPREHENSION_CHECK.value), None)
            if previous is None or previous.response_id is None:
                raise InvalidAnswerReference("understanding check requires a delivered explanation")
            if record.journey.channel == "voice" and not any(
                    event.playback_response_id == previous.response_id for event in record.journey.events):
                return None
            audit = self._explanation(
                record, ResponseReference(record.journey.journey_id, previous.response_id),
                MemberIdentity(counsellor.tools.auth_ref, counsellor.step_up_verified),
            )
        if audit.response_kind != "explanation":
            return None
        try:
            vocabulary = domain.vocabulary()
            language = counsellor.journey.response_language
            offer = OutcomeOffer(audit.response_id, language, vocabulary["version"],
                                 **vocabulary["outcome_check"][language])
            wording = (offer.question, offer.yes, offer.no, offer.skip)
            text = f"{result.text}\n\n{offer.question}"
            fidelity = check(text, counsellor.tools.trace.values, counsellor.tools.trace.content_texts)
            if (not fidelity.passed or response_steers_choice(text)
                    or any(domain.has_numeric_characters(part) or response_steers_choice(part) for part in wording)):
                raise ValueError("approved outcome wording failed the response gates")
            counsellor.journey.offer_outcome(audit.response_id, offer.version)
        except (KeyError, TypeError, ValueError, domain.DomainPackError):
            counsellor.journey.withhold_response()
            raise
        result.text = text
        result.fidelity = fidelity
        return offer

    def confirm_playback(self, journey_id: str, request_id: str, identity: MemberIdentity,
                         response_id: str, *, received_at: datetime | None = None) -> dict:
        received_at = _arrival(received_at)
        reference = ResponseReference(journey_id, response_id)
        digest = hashlib.sha256(response_id.encode()).hexdigest()
        with self.store.transaction(journey_id) as transaction:
            record = transaction.record
            if record is None:
                raise KeyError(journey_id)
            journey = record.journey
            if journey.member_ref != identity.member_ref:
                raise MemberScopeViolation(journey_id)
            if request_id in record.processed_requests:
                if record.playback_requests.get(request_id) != digest:
                    raise OutcomeConflict("idempotency key already used for a different request")
                self._flush(transaction, record)
                return {"journey_id": journey_id, "state": journey.state.value, "duplicate": True}
            if journey.channel != "voice":
                raise InvalidAnswerReference("playback confirmation requires a voice journey")
            self._explanation(record, reference, identity, require_playback=False, before=received_at)
            previous = [event for event in journey.events
                        if event.event == Event.PLAYBACK_CONFIRMED.value and event.playback_response_id == response_id]
            if len(previous) > 1:
                raise OutcomeConflict("voice answer has conflicting playback confirmations")
            count = len(journey.events)
            if not previous:
                journey.record_playback(response_id, response_reference(journey_id, f"playback:{request_id}"),
                                        received_at=received_at.isoformat(timespec="microseconds"))
            self._events(record, count)
            record.processed_requests.append(request_id)
            record.playback_requests[request_id] = digest
            transaction.save(record)
            self._flush(transaction, record)
            return {"journey_id": journey_id, "state": journey.state.value, "duplicate": bool(previous)}

    def record_outcome(self, journey_id: str, request_id: str, identity: MemberIdentity,
                       submission: OutcomeSubmission, *, received_at: datetime | None = None) -> dict:
        received_at = _arrival(received_at)
        digest = hashlib.sha256(f"{submission.response_id}:{submission.choice}".encode()).hexdigest()
        with self.store.transaction(journey_id) as transaction:
            record = transaction.record
            if record is None:
                raise KeyError(journey_id)
            journey = record.journey
            if journey.member_ref != identity.member_ref:
                raise MemberScopeViolation(journey_id)
            if request_id in record.processed_requests:
                if record.outcome_requests.get(request_id) != digest:
                    raise OutcomeConflict("idempotency key already used for a different request")
                self._flush(transaction, record)
                return {"journey_id": journey_id, "state": journey.state.value, "duplicate": True}
            reference = ResponseReference(journey_id, submission.response_id)
            self._explanation(record, reference, identity, offered=True, before=received_at)
            previous = [event for event in journey.events
                        if event.event == Event.OUTCOME_REPORTED.value
                        and event.outcome_response_id == submission.response_id]
            if len(previous) > 1 or (previous and previous[0].outcome_choice != submission.choice):
                raise OutcomeConflict("this answer already has a different outcome")
            count = len(journey.events)
            if not previous:
                journey.record_outcome(submission, response_reference(journey_id, f"outcome:{request_id}"),
                                       received_at=received_at.isoformat(timespec="microseconds"))
            self._events(record, count)
            record.processed_requests.append(request_id)
            record.outcome_requests[request_id] = digest
            transaction.save(record)
            self._flush(transaction, record)
            return {"journey_id": journey_id, "state": journey.state.value, "duplicate": bool(previous)}

    def turn(self, journey_id: str, request_id: str, identity: MemberIdentity,
             token: str, utterance: str, trace_parent: str = "", *, voice_model: str = "",
             related_answer: ResponseReference | None = None, received_at: datetime | None = None,
             allow_admin_test: bool = False) -> dict:
        started = perf_counter()
        received_at = _arrival(received_at)
        with self.store.transaction(journey_id) as transaction:
            record = transaction.record
            if record is None:
                raise KeyError(journey_id)
            journey = record.journey
            if journey.test_strategy_id and not allow_admin_test:
                log.warning("admin_test_access_denied", extra={"journey_id": journey_id})
                raise MemberScopeViolation(journey_id)
            if journey.member_ref != identity.member_ref:
                log.error("member_scope_violation", extra={"journey_id": journey_id})
                raise MemberScopeViolation(journey_id)
            if request_id in record.processed_requests:
                if request_id in record.outcome_requests or request_id in record.playback_requests:
                    raise OutcomeConflict("idempotency key already used for an evidence event")
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
            selected_audit = None
            if related_answer is not None:
                selected_audit = self._linked_answer(record, related_answer, identity, before=received_at)
                journey.record_linked_contact(related_answer, response_reference(journey_id, f"link:{request_id}"),
                                              received_at=received_at.isoformat(timespec="microseconds"))
            was_resolved = journey.state == State.RESOLVED
            if was_resolved:
                emit("journey_contact", event_id=f"{journey_id}:contact:{request_id}", journey_id=journey_id,
                     contact_key=contact_key(identity.member_ref), agent_name="gg-orchestrator",
                     occurred_at=received_at.isoformat())
            requests: list[EscalationRequest] = []
            counsellor = None
            offer = None
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
                if selected_audit is not None:
                    counsellor.tools.related_answer_context = self._answer_context(journey, selected_audit)
                counsellor._current_strategy = journey.strategies_tried[-1] if journey.strategies_tried else None
                loaded = perf_counter()
                result = counsellor.handle(utterance)
                if related_answer is not None:
                    for index in range(count, len(journey.events)):
                        if journey.events[index].event == Event.INTENT_IN_SCOPE.value:
                            journey.events[index] = replace(
                                journey.events[index], related_journey_id=related_answer.journey_id,
                                related_response_id=related_answer.response_id,
                            )
                            break
                offer = self._offer_outcome(counsellor, result, record)
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
            released_response = journey.events[-1].response_id
            if released_response is not None:
                # The answer is not eligible as earlier evidence until all final response gates pass.
                journey.record_response(released_response, count)
            self._events(record, count)
            record.processed_requests.append(request_id)
            transaction.save(record)
            self._flush(transaction, record)
            result.stage_ms["durable_commit"] = (perf_counter() - committing) * 1000
            if result.audit_status == "unavailable":
                raise AuditUnavailable("response withheld; human escalation requested")
            out = asdict(result)
            out.pop("fidelity", None)
            out["journey_id"] = journey_id
            out["request_id"] = request_id
            if (counsellor is not None and counsellor.tools.trace.audit_record is not None
                    and counsellor.tools.trace.audit_record.response_kind == "explanation"
                    and not result.escalated and released_response is not None):
                out["response_id"] = released_response
            if offer is not None:
                out["outcome_offer"] = asdict(offer)
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
        playbacks = set()
        for journey_id in self.store.ids():
            with self.store.transaction(journey_id) as transaction:
                record = transaction.record
                if record and record.journey.member_ref == member_ref:
                    pending.extend(asdict(item) for item in record.pending_audits)
                    delivered.update(event.response_id for event in record.journey.events if event.response_id)
                    playbacks.update(event.playback_response_id for event in record.journey.events
                                     if event.event == Event.PLAYBACK_CONFIRMED.value and event.playback_response_id)
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
        report["playback_confirmed_response_ids"] = sorted(playbacks)
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
