"""Observed operational costs; never computes an official value or changes behaviour."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from governedgraph.common.contracts import _strict


def aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware observation timestamps are required")


@dataclass(frozen=True)
class Contact:
    contact_id: str
    journey_id: str
    contact_key: str
    started_at: datetime
    resolved_at: datetime | None = None

    def __post_init__(self):
        aware(self.started_at)
        if not all(isinstance(value, str) and value for value in
                   (self.contact_id, self.journey_id, self.contact_key)):
            raise ValueError("nonempty pseudonymous contact identifiers required")
        if self.resolved_at is not None:
            aware(self.resolved_at)
            if self.resolved_at < self.started_at:
                raise ValueError("resolution cannot precede contact")

    @classmethod
    def from_dict(cls, payload: dict) -> Contact:
        body = dict(payload)
        for field in ("started_at", "resolved_at"):
            if body.get(field) is not None:
                body[field] = datetime.fromisoformat(body[field])
        return _strict(cls, body)


@dataclass(frozen=True)
class Cost:
    receipt_id: str
    journey_id: str
    agent_name: str
    amount: Decimal
    currency: str
    source: str

    def __post_init__(self):
        if (not isinstance(self.amount, Decimal) or not self.amount.is_finite() or self.amount < 0
                or not all(isinstance(value, str) and value for value in
                           (self.receipt_id, self.journey_id, self.agent_name, self.currency, self.source))):
            raise ValueError("measured nonnegative sourced cost receipt required")

    @classmethod
    def from_dict(cls, payload: dict) -> Cost:
        body = dict(payload)
        if not isinstance(body.get("amount"), str):
            raise ValueError("cost amount must be an exact decimal string")
        body["amount"] = Decimal(body["amount"])
        return _strict(cls, body)


def report(contacts: list[Contact], costs: list[Cost], *, as_of: datetime,
           observed_through: datetime, cohort_start: datetime, cohort_end: datetime,
           cost_scope: str) -> dict:
    for value in (as_of, observed_through, cohort_start, cohort_end):
        aware(value)
    if (observed_through < as_of or cohort_start >= cohort_end or cohort_end > as_of
            or not cost_scope.strip()):
        raise ValueError("complete observation coverage and explicit cost scope required")
    by_contact = {contact.contact_id: contact for contact in contacts}
    by_receipt = {cost.receipt_id: cost for cost in costs}
    if len(by_contact) != len(contacts) or len(by_receipt) != len(costs):
        raise ValueError("duplicate contact or cost receipt")
    if any(c.started_at > as_of or c.resolved_at is not None and c.resolved_at > as_of for c in contacts):
        raise ValueError("future observations cannot qualify outcomes")
    first_contacts = {}
    for contact in contacts:
        if contact.journey_id not in first_contacts or contact.started_at < first_contacts[contact.journey_id]:
            first_contacts[contact.journey_id] = contact.started_at
    starts = {journey_id for journey_id, started in first_contacts.items()
              if cohort_start <= started < cohort_end}
    resolutions = {}
    for contact in contacts:
        if contact.journey_id in starts and contact.resolved_at is not None:
            previous = resolutions.get(contact.journey_id)
            if previous is None or contact.resolved_at < previous.resolved_at:
                resolutions[contact.journey_id] = contact
    qualified = []
    immature = 0
    for journey_id, contact in resolutions.items():
        end = contact.resolved_at + timedelta(days=14)
        if end > as_of:
            immature += 1
            continue
        if not any(other.contact_key == contact.contact_key
                   and contact.resolved_at < other.started_at <= end for other in contacts):
            qualified.append(journey_id)
    attributed = [c for c in costs if c.journey_id in starts]
    currencies = {c.currency for c in attributed}
    if len(currencies) > 1:
        raise ValueError("cannot combine currencies without a sourced conversion")
    total = sum((c.amount for c in attributed), Decimal(0))
    return {
        "data": "ILLUSTRATIVE - synthetic data", "cost_scope": cost_scope,
        "cohort_journeys": len(starts), "qualified_resolutions": len(qualified),
        "immature_resolutions": immature, "cost_receipts": len(attributed),
        "currency": next(iter(currencies)) if currencies else None,
        "observed_cost": str(total) if attributed else None,
        "cost_per_resolved_query": str(total / len(qualified)) if attributed and qualified else None,
    }
