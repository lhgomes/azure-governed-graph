"""The new scheduled P3 reconciler cannot approve or bypass the journal."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_reconciler_reuses_existing_publisher_identity_and_closed_approval_path():
    bootstrap = (ROOT / "infra/bootstrap.bicep").read_text()
    module = (ROOT / "infra/modules/approved-content-reconciler.bicep").read_text()
    code = (ROOT / "src/governedgraph/search/reindex_on_approval.py").read_text()

    assert "approved-content-reconciler.bicep" in bootstrap
    assert "if (deployAdminConsole)" in bootstrap
    assert "contentPublisherIdentityId" in bootstrap
    assert "triggerType: 'Schedule'" in module
    assert "'--from-approvals'" in module
    assert "GOVERNEDGRAPH_MODE" not in module  # injected via the existing governed environment
    assert "improvement_store.verify_decision(journal, item)" in code
    assert "publisher.publish_owner_approved" in code
    assert "gap_review" not in module and "catalogue" not in module
