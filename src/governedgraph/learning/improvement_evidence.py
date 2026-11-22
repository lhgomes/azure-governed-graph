"""Azure-only, read-only verification of improvement effects against governed source records.

An operator's scope names an owner decision, closed situation and time windows,
not self-reported counts. This verifier reads protected Azure audit, journey,
approval and approved-content sources. It neither changes learning state nor
grants a model publication authority. No raw member records appear in output.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import re

from governedgraph.common.contracts import ContractError
from governedgraph.learning import gap_review, improvement_store
from governedgraph.learning.gap_review_job import MAX_RESPONSE_BLOB_BYTES, read_records
from governedgraph.learning.improvement_effectiveness import evaluate, time
from governedgraph.orchestrator.store import JourneyRecord, validate_id
from governedgraph.search.corpus import load_corpus

MAX_WINDOW_DAYS = 90


def _scope(value: dict) -> tuple[datetime, datetime, datetime, datetime]:
    if not isinstance(value, dict) or set(value) != {"decision_ref", "situation", "before", "after"}:
        raise ContractError("scope must name only the owner decision, situation and windows")
    if not re.fullmatch(r"GB-[a-f0-9]{32}", str(value["decision_ref"])):
        raise ContractError("owner decision reference required")
    situation = value["situation"]
    if (not isinstance(situation, dict)
            or set(situation) != {"journey_state", "topic", "channel", "accessibility_mode", "language"}
            or any(not isinstance(x, str) or not re.fullmatch(r"[A-Za-z0-9_*-]{1,80}", x)
                   for x in situation.values())):
        raise ContractError("a closed non-personal situation is required")
    spans = []
    for phase in ("before", "after"):
        row = value[phase]
        if not isinstance(row, dict) or set(row) != {"start", "end"}:
            raise ContractError("windows may contain dates only, never user-supplied counts")
        start, end = time(row["start"]), time(row["end"])
        if start >= end or (end - start).days > MAX_WINDOW_DAYS:
            raise ContractError("invalid closed observation window")
        spans.extend([start, end])
    if not spans[0] < spans[1] <= spans[2] < spans[3]:
        raise ContractError("before and after windows must not overlap")
    if spans[3] > datetime.now(timezone.utc):
        raise ContractError("future observations are not closed")
    return tuple(spans)


def _publication(approval_store, journal, content_blob, decision_ref: str) -> tuple:
    matching = [item for item in improvement_store.read_approvals(approval_store)
                if item.decision_ref == decision_ref]
    if len(matching) != 1:
        raise ContractError("a unique published owner-approved passage is required")
    approval = matching[0]
    improvement_store.verify_decision(journal, approval)
    # The create-only publication marker is emitted *after* Search indexing and
    # verification succeed; an owner approval alone is not a publication.
    marker = json.loads(approval_store.get_blob_client(
        improvement_store.PUBLISHED + approval.name).download_blob().readall())
    if (not isinstance(marker, dict) or set(marker) != {
            "decision_ref", "document_id", "execution", "published_at", "watermark"}
            or marker["decision_ref"] != approval.decision_ref
            or marker["document_id"] != approval.content.id
            or marker["watermark"] != gap_review.WATERMARK
            or not isinstance(marker["execution"], str)):
        raise ContractError("published marker does not match the journalled approved version")
    published = time(marker["published_at"])
    # For a valid post-publication evaluation, this version must still be
    # active. Otherwise another publication is a confounding change.
    live = load_corpus(content_blob.download_blob().readall().decode("utf-8"), indexed=True)
    matching_live = [item for item in live if item.id == approval.content.id and item.status == "approved"]
    if len(matching_live) != 1 or matching_live[0] != approval.content:
        raise ContractError("the exact owner-approved content version is no longer active")
    return approval, marker, published


def _verified_window(rows, journeys, *, start: datetime, end: datetime, situation: dict) -> dict:
    eligible = 0
    declined = 0
    observed_journeys: set[str] = set()
    checked: set[tuple[str, str]] = set()
    provenance: list[dict] = []
    states: dict[str, JourneyRecord] = {}
    for audit in rows:
        created = time(audit.created_at)
        if not start <= created < end:
            continue
        intent = next((item.decision for item in reversed(audit.decision_evidence)
                       if item.role == "intent"), None)
        if intent is None or intent.get("route") != "explanation":
            continue
        key = audit.request_key
        state = {"journey_state": key["journey_state"], "topic": intent["reason"],
                 "channel": key["channel"], "accessibility_mode": key["accessibility_mode"],
                 "language": intent["response_language"]}
        if state != situation:
            continue
        signal = gap_review.signal_for(audit)
        if signal is not None and signal.outcome == "escalated":
            continue  # protective/manual escalation is not a failed explanation
        if signal is None and audit.response_kind != "explanation":
            continue
        if not audit.response_id:
            raise ContractError("explanation record lacks a stable response identity")
        identity = (audit.journey_id, audit.response_id)
        if identity in checked:
            raise ContractError("duplicate response evidence in the same observation window")
        checked.add(identity)
        validate_id(audit.journey_id)
        if audit.journey_id not in states:
            raw = journeys.get_blob_client(f"{audit.journey_id}.json").download_blob().readall()
            if not raw or raw == b"null" or len(raw) > MAX_RESPONSE_BLOB_BYTES:
                raise ContractError("missing or oversized journey state invalidates coverage")
            states[audit.journey_id] = JourneyRecord.decode(raw.decode("utf-8"))
        observed = states[audit.journey_id].journey
        if observed.journey_id != audit.journey_id or observed.member_ref != audit.member_ref:
            raise ContractError("response does not belong to the stored member journey")
        if observed.test_strategy_id:
            continue
        eligible += 1
        declined += int(signal is not None and signal.outcome in {
            "unanswered", "review_flagged", "no_style"})
        observed_journeys.add(audit.journey_id)
        provenance.append(audit.to_dict())
    # The digest binds each result to the exact accepted, pseudonymous audit
    # records, but their text is never printed or returned to the caller.
    digest = hashlib.sha256(json.dumps(
        {"start": start.isoformat(), "end": end.isoformat(),
         "audits": sorted(provenance, key=lambda v: (v["journey_id"], v["response_id"]))},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"start": start.isoformat(), "end": end.isoformat(), "eligible": eligible,
            "declined": declined, "observed_journeys": len(observed_journeys),
            "coverage_complete": True, "source_digest": digest}


def evaluate_azure(scope: dict, *, audits, journeys, approvals, journal, content_blob) -> dict:
    """Explicit Azure operator verification; never a background agent action.

    Completeness means the bounded protected audit listing and every referenced
    journey record were read successfully. It does not prove population-wide
    participation or causal uplift.
    """
    before_start, before_end, after_start, after_end = _scope(scope)
    approval, marker, published = _publication(approvals, journal, content_blob, scope["decision_ref"])
    if (approval.content.language != scope["situation"]["language"]
            or scope["situation"]["journey_state"] not in approval.content.journey_states
            and "*" not in approval.content.journey_states):
        raise ContractError("published content must be applicable to the evaluated situation")
    if not before_end <= published <= after_start:
        raise ContractError("published version must fall between the two closed windows")
    rows = read_records(audits, before_start)
    before = _verified_window(rows, journeys, start=before_start, end=before_end, situation=scope["situation"])
    after = _verified_window(rows, journeys, start=after_start, end=after_end, situation=scope["situation"])
    result = evaluate({
        "decision_ref": approval.decision_ref,
        "proposal_id": approval.proposal_id,
        "situation": scope["situation"],
        "published_at": marker["published_at"],
        "before": before,
        "after": after,
    })
    return {**result, "evidence_verification": {
        "source": "protected_azure_audit_and_journey_states",
        "publication": "journal_verified_content_approval_and_marker",
        "content_version": approval.content.id,
        "content_sha256": improvement_store.document_digest(approval.document),
        "publication_execution": marker["execution"],
        "scope": "closed_explanation_interactions_only",
        "population_coverage_proven": False,
    }}
