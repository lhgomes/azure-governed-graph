"""Approval backlog must come from immutable P3 markers, not inferred success."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from governedgraph.learning import improvement_store
from governedgraph.search.publication_monitor import backlog, BACKLOG_MAX_AGE_SECONDS
from test_improvement_evidence import fixtures


NOW = datetime(2026, 11, 22, tzinfo=timezone.utc)


def with_properties(container, age: int):
    original = container.get_blob_client

    def client(name):
        base = original(name)
        return SimpleNamespace(download_blob=base.download_blob,
                               get_blob_properties=lambda: SimpleNamespace(
                                   last_modified=NOW-timedelta(seconds=age)))
    container.get_blob_client = client
    return container


def test_approval_without_publication_marker_is_pending_then_stale():
    approved, approvals, _, _ = fixtures()
    approvals.blobs.pop(f"published/{improvement_store.approval_name(approved)}")
    recent = with_properties(approvals, 3600)
    assert backlog(recent, now=NOW) == {"pending_records": 1, "age_seconds": 3600, "stale": False}
    aged = with_properties(approvals, BACKLOG_MAX_AGE_SECONDS + 1)
    assert backlog(aged, now=NOW)["stale"] is True


def test_published_and_refused_are_terminal_from_a_backlog_perspective():
    approved, approvals, _, _ = fixtures()
    assert backlog(with_properties(approvals, 200000), now=NOW)["pending_records"] == 0
    name = improvement_store.approval_name(approved)
    approvals.blobs.pop(f"published/{name}")
    approvals.blobs[f"refused/{name}"] = b'{"watermark": "ILLUSTRATIVE - synthetic data"}'
    assert backlog(approvals, now=NOW)["pending_records"] == 0


def test_no_backlog_or_missing_timestamp_is_never_interpreted_as_successful_publication():
    _, approvals, _, _ = fixtures()
    approvals.blobs.clear()
    assert backlog(approvals, now=NOW) == {"pending_records": 0, "age_seconds": 0, "stale": False}


def test_scheduled_publication_alerts_and_job_identity_are_wired_without_new_privilege():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    alerts = (root / "infra/modules/alerts.bicep").read_text()
    workloads = (root / "infra/workloads.bicep").read_text()
    reconciler = (root / "infra/modules/approved-content-reconciler.bicep").read_text()
    publisher = (root / "src/governedgraph/search/reindex_on_approval.py").read_text()
    assert "monitorPublication: deployAdminConsole" in workloads
    for event in ("publication_reconciliation_failed", "publication_backlog_stale",
                  "publication_reconciliation_completed"):
        assert event in alerts or event in publisher
    assert "publicationFreshness" in alerts and "36 hours" in alerts
    assert "GOVERNEDGRAPH_CONTENT_RECONCILER" in reconciler
    assert "contentPublisherIdentityId" in (root / "infra/bootstrap.bicep").read_text()
    assert "verify_decision(journal, item)" in publisher
