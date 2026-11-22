"""Blob layout of the two improvement containers, shared by the batch job, the console and the content publisher.

``improvement-proposals`` (the gap-review job writes, the console reads):
    proposals/<proposal-id>/<evidence-digest>.json
    batches/<batch-id>.json
``content-approvals`` (the console and the content publisher write; the gap-review job can only read):
    approved/<document-id>-<language>.json    written once by the console when an owner approves a passage
    published/<document-id>-<language>.json   written once by the content publisher after indexing
    refused/<document-id>-<language>.json     written once by the content publisher when the live passage moved on

Only `content-approvals` has storage-enforced WORM retention. `improvement-proposals` is lifecycle-expired and
its records are created once per evidence revision but may be overwritten solely to redact expired member wording.
Owner decisions live in the immutable console action journal; the publisher rejects approvals without a
matching journalled owner decision.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from governedgraph.common.contracts import ContractError, _strict
from governedgraph.learning.gap_review import PROPOSAL_ID, WATERMARK, Proposal
from governedgraph.search.corpus import ContentDocument

APPROVED = "approved/"
PUBLISHED = "published/"
REFUSED = "refused/"
MAX_PROPOSALS = 500
MAX_BLOBS = 5000
BATCH_FIELDS = {"batch_id", "created_at", "rules_version", "watermark", "records_read", "declined_signals",
                "test_records_excluded", "situations", "proposals", "window_start", "window_end", "questions_read",
                "suggestions_drafted", "current"}


class StoreTooLarge(ValueError):
    """A listing hit its cap. Deciding from part of the history could hide a decision or reuse an identity."""


@dataclass(frozen=True)
class ContentApproval:
    decision_ref: str
    proposal_id: str
    document: dict
    base_id: str = ""

    def __post_init__(self):
        if (not isinstance(self.decision_ref, str) or not re.fullmatch(r"GB-[a-f0-9]{32}", self.decision_ref)
                or not isinstance(self.proposal_id, str) or not PROPOSAL_ID.fullmatch(self.proposal_id)
                or not isinstance(self.base_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{0,100}", self.base_id)):
            raise ContractError("an owner decision reference and proposal are required")
        document = ContentDocument.from_dict(self.document)
        if document.status != "approved" or document.approval_ref != self.decision_ref:
            raise ContractError("approved content must carry the owner decision reference")

    @classmethod
    def from_dict(cls, payload: dict) -> ContentApproval:
        return _strict(cls, payload)

    @property
    def content(self) -> ContentDocument:
        return ContentDocument.from_dict(self.document)

    @property
    def name(self) -> str:
        return approval_name(self.content)

    @classmethod
    def for_document(cls, document: ContentDocument, decision_ref: str, proposal: str,
                     base_id: str = "") -> ContentApproval:
        return cls(decision_ref, proposal, asdict(document), base_id)


def document_digest(document: dict) -> str:
    """Digest of the complete approved document, journalled by the console and checked again before publishing."""
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def approval_name(document: ContentDocument) -> str:
    return f"{document.id}-{document.language}.json"


def _blobs(container, prefix: str, limit: int):
    items = []
    for item in container.list_blobs(name_starts_with=prefix):
        if not item.name.endswith(".json"):
            continue
        if len(items) >= limit:
            return items, True
        items.append(item)
    return items, False


def _load(container, name: str) -> dict:
    payload = json.loads(container.get_blob_client(name).download_blob().readall())
    if not isinstance(payload, dict):
        raise ValueError("improvement records are objects")
    return payload


def _all(container, prefix: str):
    items, truncated = _blobs(container, prefix, MAX_BLOBS)
    if truncated:
        raise StoreTooLarge(f"more than {MAX_BLOBS} records under {prefix}; ask a technical operator to review the store")
    return items


def observed(container) -> dict[str, str]:
    """The evidence digest each proposal has in the newest batch that saw it, so a digest that comes back is shown again."""
    items, _ = _blobs(container, "batches/", 400)
    current: dict[str, str] = {}
    for item in sorted(items, key=lambda blob: blob.name, reverse=True):
        for proposal, digest in _batch(container, item.name)["current"].items():
            current.setdefault(proposal, digest)
    return current


def read_proposals(container, limit: int = MAX_PROPOSALS) -> tuple[list[Proposal], bool]:
    """The revision of every proposal that the newest batch observed; an unreadable record is an error, never an omission."""
    items = {item.name: item for item in _all(container, "proposals/")}
    chosen: dict[str, object] = {}
    for proposal, digest in observed(container).items():
        item = items.get(f"proposals/{proposal}/{digest}.json")
        if item is not None:
            chosen[proposal] = item
    # A proposal no batch summary names (an interrupted run) falls back to its newest revision.
    newest: dict[str, object] = {}
    for item in items.values():
        identity = item.name.split("/")[1]
        held = newest.get(identity)
        if identity not in chosen and (held is None or (item.last_modified, item.name) > (held.last_modified, held.name)):
            newest[identity] = item
    chosen.update(newest)
    ordered = sorted(chosen.values(), key=lambda item: (item.last_modified, item.name), reverse=True)
    proposals = [Proposal.from_dict(_load(container, item.name)) for item in ordered[:limit]]
    if any(item.blob_name != blob.name for item, blob in zip(proposals, ordered[:limit], strict=True)):
        raise ValueError("proposal storage path does not match its content")
    return proposals, len(ordered) > limit


def _batch(container, name: str) -> dict:
    row = _load(container, name)
    if (set(row) != BATCH_FIELDS or row["watermark"] != WATERMARK or not isinstance(row["current"], dict)
            or any(not PROPOSAL_ID.fullmatch(str(key)) or not re.fullmatch(r"[a-f0-9]{64}", str(value))
                   for key, value in row["current"].items())):
        raise ValueError("unexpected batch summary")
    return row


def read_batches(container, limit: int = 10) -> list[dict]:
    items, _ = _blobs(container, "batches/", 1000)
    return [_batch(container, item.name) for item in sorted(items, key=lambda blob: blob.name, reverse=True)[:limit]]


def read_approvals(container) -> list[ContentApproval]:
    items = _all(container, APPROVED)
    approvals = [ContentApproval.from_dict(_load(container, item.name)) for item in items]
    if any(APPROVED + approval.name != item.name for approval, item in zip(approvals, items, strict=True)):
        raise ValueError("approved content storage path does not match its content")
    return approvals


def published_names(container) -> set[str]:
    return {item.name.removeprefix(PUBLISHED) for item in _all(container, PUBLISHED)}


def refused_names(container) -> set[str]:
    return {item.name.removeprefix(REFUSED) for item in _all(container, REFUSED)}


def verify_decision(journal, approval: ContentApproval) -> None:
    """An approval is publishable only when the console journalled the owner decision before writing it."""
    from azure.core.exceptions import ResourceNotFoundError

    try:
        row = _load(journal, f"{approval.decision_ref}/requested.json")
    except ResourceNotFoundError as error:
        raise PermissionError(f"approval {approval.decision_ref} has no journalled owner decision") from error
    proposal = row.get("proposal")
    if (row.get("action_id") != approval.decision_ref or row.get("action") != "proposal_content_approve"
            or row.get("document_id") != approval.content.id or row.get("content_name") != approval.name
            or row.get("base_id", "") != approval.base_id
            or row.get("document_sha256") != document_digest(approval.document)
            or not isinstance(row.get("source"), str) or len(row["source"].strip()) < 8
            or not isinstance(proposal, dict) or proposal.get("proposal_id") != approval.proposal_id):
        raise PermissionError(f"approval {approval.decision_ref} does not match its journalled owner decision")


def redact_expired_questions(container, now: datetime) -> int:
    """Remove member wording from stored proposals once the question's own 30 days have passed.

    Copying a question into a proposal would otherwise restart its retention clock. The evidence digest ignores
    examples and cases, so the proposal keeps its place; only the content digest changes, which asks an owner to refresh."""
    from governedgraph.learning.gap_review import redacted

    changed = 0
    for item in _all(container, "proposals/"):
        proposal = Proposal.from_dict(_load(container, item.name))
        clean = redacted(proposal, now)
        if clean is not None:
            container.get_blob_client(item.name).upload_blob(clean.encode(), overwrite=True)
            changed += 1
    return changed


def exists(container, name: str) -> bool:
    from azure.core.exceptions import ResourceNotFoundError

    try:
        container.get_blob_client(name).download_blob()
    except ResourceNotFoundError:
        return False
    return True


def create_once(container, name: str, payload: bytes) -> bool:
    """Create a blob; False when it already exists, which makes every writer idempotent."""
    from azure.core.exceptions import ResourceExistsError

    try:
        container.get_blob_client(name).upload_blob(payload, overwrite=False)
    except ResourceExistsError:
        return False
    return True


def write_approval(container, approval: ContentApproval) -> None:
    if not create_once(container, APPROVED + approval.name, json.dumps(asdict(approval), sort_keys=True).encode()):
        raise FileExistsError("this content version is already approved")


def mark_published(container, approval: ContentApproval, execution: str) -> None:
    create_once(container, PUBLISHED + approval.name, json.dumps({
        "decision_ref": approval.decision_ref, "document_id": approval.content.id, "execution": execution,
        "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "watermark": WATERMARK,
    }, sort_keys=True).encode())


def mark_refused(container, approval: ContentApproval, reason: str, execution: str) -> None:
    """The publisher declined an approval because the live passage moved on. The owner decides again on a new idea."""
    create_once(container, REFUSED + approval.name, json.dumps({
        "decision_ref": approval.decision_ref, "document_id": approval.content.id, "reason": reason[:300],
        "execution": execution, "refused_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "watermark": WATERMARK}, sort_keys=True).encode())

