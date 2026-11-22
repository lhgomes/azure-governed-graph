"""Blob layout of the two improvement containers, shared by the batch job, the console and the content publisher.

``improvement-proposals`` (the gap-review job writes, the console reads):
    proposals/<proposal-id>/<evidence-digest>.json
    batches/<batch-id>.json
``content-approvals`` (the console and the content publisher write; the gap-review job can only read):
    approved/<document-id>-<language>.json    written once by the console when an owner approves a passage
    published/<document-id>-<language>.json   written once by the content publisher after indexing

Both containers are immutable stores, and every blob is written create-only. Owner decisions live in the console's
immutable action journal; the publisher refuses an approval that has no matching journalled owner decision.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from governedgraph.common.contracts import ContractError, _strict
from governedgraph.learning.gap_review import PROPOSAL_ID, WATERMARK, Proposal
from governedgraph.search.corpus import ContentDocument

APPROVED = "approved/"
PUBLISHED = "published/"
MAX_PROPOSALS = 500
BATCH_FIELDS = {"batch_id", "created_at", "rules_version", "watermark", "records_read", "declined_signals",
                "test_records_excluded", "situations", "proposals", "window_start", "window_end", "questions_read",
                "suggestions_drafted"}


@dataclass(frozen=True)
class ContentApproval:
    decision_ref: str
    proposal_id: str
    document: dict

    def __post_init__(self):
        if (not isinstance(self.decision_ref, str) or not re.fullmatch(r"GB-[a-f0-9]{32}", self.decision_ref)
                or not isinstance(self.proposal_id, str) or not PROPOSAL_ID.fullmatch(self.proposal_id)):
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
    def for_document(cls, document: ContentDocument, decision_ref: str, proposal: str) -> ContentApproval:
        return cls(decision_ref, proposal, asdict(document))


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


def read_proposals(container, limit: int = MAX_PROPOSALS) -> tuple[list[Proposal], bool]:
    """The newest revision of every proposal; an unreadable record is an error, never a silent omission."""
    items, truncated = _blobs(container, "proposals/", limit * 4)
    newest: dict[str, object] = {}
    for item in items:
        identity = item.name.split("/")[1]
        held = newest.get(identity)
        if held is None or (item.last_modified, item.name) > (held.last_modified, held.name):
            newest[identity] = item
    chosen = sorted(newest.values(), key=lambda item: (item.last_modified, item.name), reverse=True)
    truncated = truncated or len(chosen) > limit
    proposals = [Proposal.from_dict(_load(container, item.name)) for item in chosen[:limit]]
    if any(item.blob_name != blob.name for item, blob in zip(proposals, chosen[:limit], strict=True)):
        raise ValueError("proposal storage path does not match its content")
    return proposals, truncated


def read_batches(container, limit: int = 10) -> list[dict]:
    items, _ = _blobs(container, "batches/", 1000)
    rows = []
    for item in sorted(items, key=lambda blob: blob.name, reverse=True)[:limit]:
        row = _load(container, item.name)
        if set(row) != BATCH_FIELDS or row["watermark"] != WATERMARK:
            raise ValueError("unexpected batch summary")
        rows.append(row)
    return rows


def read_approvals(container) -> list[ContentApproval]:
    items, _ = _blobs(container, APPROVED, 5000)
    approvals = [ContentApproval.from_dict(_load(container, item.name)) for item in items]
    if any(APPROVED + approval.name != item.name for approval, item in zip(approvals, items, strict=True)):
        raise ValueError("approved content storage path does not match its content")
    return approvals


def published_names(container) -> set[str]:
    items, _ = _blobs(container, PUBLISHED, 5000)
    return {item.name.removeprefix(PUBLISHED) for item in items}


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
            or not isinstance(row.get("source"), str) or len(row["source"].strip()) < 8
            or not isinstance(proposal, dict) or proposal.get("proposal_id") != approval.proposal_id):
        raise PermissionError(f"approval {approval.decision_ref} does not match its journalled owner decision")


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
