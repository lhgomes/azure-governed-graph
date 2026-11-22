"""Private Azure verification uses actual audited responses and journalled publication, never supplied counts."""
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json

import pytest

from admin_doubles import MemoryContainer
from governedgraph.common.contracts import ContractError
from governedgraph.learning import improvement_store
from governedgraph.learning.improvement_evidence import _scope, _verified_window, _publication, evaluate_azure
from governedgraph.orchestrator.state_machine import Journey
from governedgraph.orchestrator.store import JourneyRecord
from test_approved_content import document
from test_gap_review import record


PUBLISHED = datetime(2026, 11, 10, tzinfo=timezone.utc)
START = datetime(2026, 11, 1, tzinfo=timezone.utc)
REVIEW = {"journey_state": "first_contact", "topic": "payment_continuity",
          "channel": "chat", "accessibility_mode": "standard", "language": "en"}
REF = "GB-" + "a" * 32
PID = "GP-" + "b" * 24


class Audits(MemoryContainer):
    def list_blobs(self, name_starts_with=""):
        return [SimpleNamespace(name=name, last_modified=PUBLISHED + timedelta(days=30),
                                size=len(self.blobs[name]))
                for name in sorted(self.blobs) if name.startswith(name_starts_with)]


def fixtures():
    approved = document(approval_ref=REF, language="en")
    approval = improvement_store.ContentApproval.for_document(approved, REF, PID)
    marker = {"decision_ref": REF, "document_id": approved.id, "execution": "synthetic-job",
              "published_at": PUBLISHED.isoformat(), "watermark": "ILLUSTRATIVE - synthetic data"}
    approvals = MemoryContainer({
        f"approved/{approval.name}": json.dumps(asdict(approval)).encode(),
        f"published/{approval.name}": json.dumps(marker).encode()})
    journal = MemoryContainer({f"{REF}/requested.json": json.dumps({
        "action_id": REF, "action": "proposal_content_approve",
        "document_id": approved.id, "content_name": approval.name, "base_id": "",
        "document_sha256": improvement_store.document_digest(approval.document),
        "source": "Synthetic article 12 owner verification",
        "proposal": {"proposal_id": PID}}).encode()})
    indexed = MemoryContainer({"corpus.jsonl": json.dumps(approved.index_document()).encode()})
    return approved, approvals, journal, indexed.get_blob_client("corpus.jsonl")


def scope():
    return {"decision_ref": REF, "situation": REVIEW,
            "before": {"start": START.isoformat(), "end": (PUBLISHED - timedelta(days=1)).isoformat()},
            "after": {"start": (PUBLISHED + timedelta(days=1)).isoformat(),
                      "end": (PUBLISHED + timedelta(days=9)).isoformat()}}


def evidence(count: int = 31):
    rows = []
    journeys = {}
    for batch, day in enumerate((5, 15)):
        for i in range(count):
            n = 1000 * batch + i + 1
            answer = replace(record(n, kind="explanation", strategy="S1", issues=[]),
                             created_at=(START + timedelta(days=day)).isoformat())
            rows.append(answer)
            journey = Journey(answer.journey_id, answer.member_ref)
            journeys[f"{answer.journey_id}.json"] = JourneyRecord(journey).encode().encode()
    audit = Audits({f"members/{row.member_ref}/{row.response_id}.json":
                    json.dumps(row.to_dict()).encode() for row in rows})
    return audit, MemoryContainer(journeys)


def test_azure_scope_rejects_claimed_counts_and_overlapping_windows():
    test = scope()
    test["before"]["eligible"] = 1000000
    with pytest.raises(ContractError, match="dates only"):
        _scope(test)
    test = scope()
    test["after"]["start"] = START.isoformat()
    with pytest.raises(ContractError, match="overlap"):
        _scope(test)


def test_closed_window_comes_from_audit_and_verifies_pseudonymous_journey():
    audit, journeys = evidence(2)
    from governedgraph.learning.gap_review_job import read_records
    rows = read_records(audit, START)
    result = _verified_window(rows, journeys, start=START, end=PUBLISHED, situation=REVIEW)
    assert result["eligible"] == 2 and result["declined"] == 0
    assert result["observed_journeys"] == 2 and result["coverage_complete"]
    assert len(result["source_digest"]) == 64
    first = rows[0]
    journeys.blobs[f"{first.journey_id}.json"] = JourneyRecord(Journey(first.journey_id, "wrong-member")).encode().encode()
    with pytest.raises(ContractError, match="stored member"):
        _verified_window(rows, journeys, start=START, end=PUBLISHED, situation=REVIEW)


def test_full_azure_report_binds_publication_and_actual_record_counts():
    approved, approvals, journal, content = fixtures()
    records, journeys = evidence()
    report = evaluate_azure(scope(), audits=records, journeys=journeys,
                            approvals=approvals, journal=journal, content_blob=content)
    assert report["status"] == "observed_descriptive_change"
    assert report["before"]["eligible"] == 31 == report["after"]["eligible"]
    assert report["decline_rate_delta_percentage_points"] == 0
    assert report["evidence_verification"]["content_version"] == approved.id
    assert report["evidence_verification"]["population_coverage_proven"] is False
    assert report["causal_uplift_proven"] is False


def test_missing_publication_marker_and_stale_content_fail_closed():
    approved, approvals, journal, content = fixtures()
    name = improvement_store.approval_name(approved)
    approvals.blobs.pop(f"published/{name}")
    with pytest.raises(Exception):
        _publication(approvals, journal, content, REF)
    _, approvals, journal, _ = fixtures()
    other = document(approval_ref=REF, language="en", id="C-TEST-v2.0", version="2.0")
    stale_feed = MemoryContainer({"corpus.jsonl": json.dumps(other.index_document()).encode()})
    with pytest.raises(ContractError, match="no longer active"):
        _publication(approvals, journal, stale_feed.get_blob_client("corpus.jsonl"), REF)


def test_invalid_owner_journal_does_not_validate_published_version():
    _, approvals, journal, content = fixtures()
    journal.blobs[f"{REF}/requested.json"] = b'{"action":"not-owner-approved"}'
    with pytest.raises(PermissionError):
        _publication(approvals, journal, content, REF)
