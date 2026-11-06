"""B3/B4/B6/B7/B9: administration reads actual governed state, never cohort identities."""
import hashlib
import json

import pytest

from governedgraph.admin.data import learning_evidence, overview
from governedgraph.common.contracts import SuspensionEvent
from governedgraph.strategy_service.service import StrategyService
from governedgraph.strategy_service.snapshot import RankingSnapshot, sign_snapshot

KEY = "first_contact|transition_impact_question|voice|standard"


def test_b3_b6_admin_shows_verified_rankings_with_eligibility_and_universal_veto(tmp_path):
    service = StrategyService(state_dir=tmp_path / "strategy")
    snapshot = sign_snapshot(RankingSnapshot(
        "snapshot-admin", service.catalogue.version, "1.0", "audit-1", "tier0",
        {KEY: [[sid, service.catalogue.get(sid).version] for sid in ["S2", "S5", "S1", "S4", "S3"]]},
    ), service.signer)
    service.load_snapshot(snapshot)
    service.suspend(SuspensionEvent("S2", service.catalogue.get("S2").version, "*", "audit-1", 25, "1.0", "GC-1"))
    result = overview(service, tmp_path)
    row = next(item for item in result["rankings"] if item["request_key"] == KEY)
    assert row["snapshot_order"][0] == "S2"
    assert "S2" not in row["effective_control"] + row["effective_treatment"]
    assert row["effective_treatment"][0] == "S5"
    assert row["effective_control"][0] == "S1"
    assert result["always_available"] == ["S6"]
    assert all("S6" not in item["effective_treatment"] for item in result["rankings"])
    assert result["state"]["current"]["snapshot_id"] == "snapshot-admin"


def test_b7_admin_projects_case_without_demographics_or_cohort_rates(tmp_path):
    governance = tmp_path / "governance"
    governance.mkdir()
    (governance / "cases.jsonl").write_text(json.dumps({
        "case_id": "GC-1", "type": "automatic_suspension", "tier": "Tier 2 to reinstate", "audit_id": "a",
        "finding": {"strategy_id": "S2", "request_key": KEY, "gap_pts": 25, "breach": True,
                    "dimension": "gender", "cohort": "PRIVATE-COHORT", "cohort_fcr_pct": 12},
    }) + "\n")
    result = overview(StrategyService(state_dir=tmp_path / "strategy"), tmp_path)
    encoded = json.dumps(result)
    assert "PRIVATE-COHORT" not in encoded and "gender" not in encoded and "cohort_fcr_pct" not in encoded
    assert result["cases"][0]["finding"]["gap_pts"] == 25
    assert result["cases"][0]["case_id"] == "GC-1"


def test_b4_admin_does_not_run_learning_or_invent_approvals(tmp_path):
    service = StrategyService(state_dir=tmp_path / "strategy")
    first = overview(service, tmp_path)
    second = overview(service, tmp_path)
    assert first["revision"] == second["revision"]
    assert first["state"]["current"] is None
    assert first["decisions"] == first["cases"] == first["actions"] == []
    assert all(row["source"] == "expert priors fallback" for row in first["rankings"])
    assert not list(tmp_path.rglob("*.jsonl"))


def test_b4_b7_admin_learning_view_reads_only_verified_published_aggregates(tmp_path):
    from governedgraph.learning.feedback import FeedbackManifest, FeedbackSnapshot
    from governedgraph.learning.feedback_store import FileFeedbackStore

    assert learning_evidence(tmp_path) == {"manifest": None, "aggregates": []}
    contents = ("request_key,strategy_id,strategy_version,interactions,resolved_first_contact,repeat_contact_14d\n"
                f"{KEY},S1,1.0,400,200,40\n").encode()
    digest = hashlib.sha256(contents).hexdigest()
    manifest = FeedbackManifest(digest, "a" * 64, "b" * 64, 10, 10, 0, 5)
    store = FileFeedbackStore(tmp_path / "learning-aggregates")
    store.save(FeedbackSnapshot(contents, manifest))
    result = learning_evidence(tmp_path)
    assert result["manifest"]["observed_decisions"] == 10
    assert result["aggregates"][0]["interactions"] == 400
    store.outcome_path().write_bytes(contents + b"corrupted")
    with pytest.raises(ValueError, match="digest mismatch"):
        learning_evidence(tmp_path)
