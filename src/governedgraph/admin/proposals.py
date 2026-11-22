"""Owner decisions on gap-review proposals: closed contracts, derived status and the publication state of each decision.

The proposals themselves are written only by the gap-review job. A decision is recorded in the console's immutable action
journal (written before it takes effect) and implemented through an existing governed path: a catalogue change for a style,
or an approved-content hand-off for the content publisher. Nothing here activates anything on its own (B3, B8).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from governedgraph.common.contracts import ContractError, _strict
from governedgraph.learning.gap_review import DIGEST, PROPOSAL_ID, Proposal

DECISIONS = {
    "content_gap": {"approve", "reject", "defer"},
    "content_revision": {"approve", "reject", "defer"},
    "strategy_gap": {"approve", "reject", "defer"},
    "false_negative_review": {"acknowledge", "reject", "defer"},
}
# Journal action recorded for each decision; approvals use the owner decision reference (GB-).
ACTIONS = {"reject": "proposal_reject", "defer": "proposal_defer", "acknowledge": "proposal_acknowledge",
           "approve_content": "proposal_content_approve"}
CLOSED = {"rejected", "acknowledged", "published", "queued", "queued_publication"}
RANK = {"requested": 0, "failed": 1, "uncertain": 1, "applied": 2, "pending_signature": 2, "pending_publication": 2}


@dataclass(frozen=True)
class ProposalDecision:
    proposal_id: str
    evidence_digest: str
    decision: str
    reason: str
    confirmed: bool
    expected_revision: str = ""
    content: dict | None = None
    style: dict | None = None

    def __post_init__(self):
        if (not isinstance(self.proposal_id, str) or not PROPOSAL_ID.fullmatch(self.proposal_id)
                or not isinstance(self.evidence_digest, str) or not DIGEST.fullmatch(self.evidence_digest)
                or self.decision not in {"approve", "reject", "defer", "acknowledge"}
                or not isinstance(self.reason, str) or not 8 <= len(self.reason.strip()) <= 500
                or self.confirmed is not True
                or not isinstance(self.expected_revision, str)
                or (self.expected_revision and not re.fullmatch(r"[a-f0-9]{64}", self.expected_revision))
                or any(item is not None and not isinstance(item, dict) for item in (self.content, self.style))):
            raise ContractError("a confirmed decision, reason and the proposal revision shown are required")
        if self.decision != "approve" and (self.content is not None or self.style is not None):
            raise ContractError("only an approval carries content or style settings")

    @classmethod
    def from_dict(cls, payload: dict) -> ProposalDecision:
        return _strict(cls, payload)


def _outcomes(journal: list[dict]) -> dict[str, dict]:
    """Collapse each journalled action to its furthest step, keeping the request row for its identity and time."""
    actions: dict[str, dict] = {}
    for row in journal:
        if not isinstance(row.get("proposal"), dict):
            continue
        held = actions.get(row["action_id"])
        if held is None or RANK.get(row["status"], 0) >= RANK.get(held["status"], 0):
            actions[row["action_id"]] = {**(held or {}), **row}
    return actions


def status_of(proposal: Proposal, journal: list[dict], state: dict, published: set[str]) -> dict:
    """Where a proposal stands, derived from the journal and live state; ``open`` until a human decides."""
    related = [row for row in _outcomes(journal).values() if row["proposal"]["proposal_id"] == proposal.proposal_id
               and row["status"] != "failed"]
    if not related:
        return {"code": "open", "label": "Open: waiting for an owner", "decision_ref": ""}
    row = max(related, key=lambda item: (item["created_at"], item["action_id"]))
    ref, action = row["action_id"], row["action"]
    if row["status"] in {"requested", "uncertain"}:
        return {"code": "uncertain", "label": "Decision not completed: investigate before deciding again", "decision_ref": ref}
    if action == ACTIONS["defer"]:
        if row["proposal"]["evidence_digest"] != proposal.evidence_digest:
            return {"code": "open", "label": "Open: new evidence since it was deferred", "decision_ref": ref}
        return {"code": "deferred", "label": "Deferred", "decision_ref": ref}
    if action == ACTIONS["reject"]:
        return {"code": "rejected", "label": "Rejected", "decision_ref": ref}
    if action == ACTIONS["acknowledge"]:
        return {"code": "acknowledged", "label": "Acknowledged for follow-up", "decision_ref": ref}
    if action == ACTIONS["approve_content"]:
        if row.get("content_name", "") in published:
            return {"code": "published", "label": "Published to approved content", "decision_ref": ref}
        return {"code": "queued_publication", "label": "Approved: waiting for the content publisher", "decision_ref": ref}
    if action == "owner_catalogue_add":
        if state.get("catalogue_release", {}) and state["catalogue_release"].get("decision_ref") == ref:
            return {"code": "published", "label": "Published as a new style", "decision_ref": ref}
        if (state.get("catalogue_pending") or {}).get("decision_ref") == ref:
            return {"code": "queued", "label": "Approved: queued for the next signed release", "decision_ref": ref}
        return {"code": "superseded", "label": "Approved, but no longer queued: superseded or cancelled", "decision_ref": ref}
    return {"code": "open", "label": "Open: waiting for an owner", "decision_ref": ""}


def view(improvement: dict, journal: list[dict], state: dict) -> dict:
    """Proposals newest first with their status, ready for the console."""
    rows = []
    for proposal in improvement["proposals"]:
        rows.append({**asdict(proposal), "status": status_of(proposal, journal, state, improvement["published"]),
                     "allowed_decisions": sorted(DECISIONS[proposal.kind])})
    return {"proposals": rows, "batches": improvement["batches"], "truncated": improvement["truncated"]}
