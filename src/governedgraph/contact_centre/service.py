"""Idempotent synthetic handovers, containing references only and no live callback."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import html
import json
import re

from governedgraph.common.contracts import EscalationRequest, _strict
from governedgraph.orchestrator.store import validate_id

HANDOVER_SUMMARY = "Human review requested; consult the governed journey references."
REASONS = {"advice_intent", "member_request", "teach_back_failed", "component_failure", "conversation_limit",
           "safety"}


def advisor_queue_html(rows: list[dict]) -> str:
    headings = ("handover_id", "escalation_id", "journey_id", "reason_code", "status")
    header = "".join(f"<th>{html.escape(field)}</th>" for field in headings)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(row[field]))}</td>" for field in headings)
                   + "</tr>" for row in rows)
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><title>Synthetic advisor queue</title>'
            '<h1>Advisor queue</h1><p>ILLUSTRATIVE - synthetic data. No live callbacks.</p>'
            f"<table border=1><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></html>")


def validate_escalation(request: EscalationRequest) -> None:
    validate_id(request.journey_id)
    if (not isinstance(request.escalation_id, str)
            or not re.fullmatch(re.escape(request.journey_id) + r":escalation:[1-9][0-9]*", request.escalation_id)
            or not isinstance(request.reason_code, str) or request.reason_code not in REASONS
            or request.context_summary != HANDOVER_SUMMARY or not isinstance(request.callback_preference, str)
            or request.callback_preference not in {"none", "phone", "secure_message"}
            or not isinstance(request.created_at, str)
            or not isinstance(request.value_ids, list)
            or any(not isinstance(item, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", item)
                   for item in request.value_ids)):
        raise ValueError("handover must contain governed references only")
    if datetime.fromisoformat(request.created_at).tzinfo is None:
        raise ValueError("handover creation time requires a timezone")


@dataclass
class Handover:
    escalation_id: str
    journey_id: str
    reason_code: str
    context_summary: str
    value_ids: list[str]
    callback_preference: str
    status: str
    handover_id: str
    created_at: str
    data: str = "ILLUSTRATIVE - synthetic data"

    def encode(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def decode(cls, text: str) -> "Handover":
        return _strict(cls, json.loads(text))


class SyntheticContactCentre:
    def __init__(self, store):
        self.store = store

    def handover(self, request: EscalationRequest, idempotency_key: str) -> str:
        validate_escalation(request)
        if idempotency_key != request.escalation_id:
            raise ValueError("Idempotency-Key must equal the escalation ID")
        key = hashlib.sha256(idempotency_key.encode()).hexdigest()
        with self.store.transaction(key) as transaction:
            if transaction.record:
                previous = transaction.record
                if any(getattr(previous, field) != getattr(request, field)
                       for field in ("escalation_id", "journey_id", "reason_code", "context_summary",
                                     "value_ids", "callback_preference")):
                    raise ValueError("escalation ID reused with different content")
                return previous.handover_id
            handover = Handover(
                request.escalation_id, request.journey_id, request.reason_code, request.context_summary,
                request.value_ids, request.callback_preference, "synthetic_pending", f"HO-{key[:24]}",
                request.created_at,
            )
            transaction.save(handover)
            return handover.handover_id

    def advisor_queue(self, journey_id: str) -> list[dict]:
        validate_id(journey_id)
        rows = []
        for key in self.store.ids():
            with self.store.transaction(key) as transaction:
                if transaction.record and transaction.record.journey_id == journey_id:
                    rows.append(asdict(transaction.record))
        return rows
