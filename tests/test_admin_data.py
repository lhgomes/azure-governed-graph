"""B3/B4/B6/B7/B9: administration reads actual governed state, never cohort identities."""
import json

import pytest

from admin_doubles import LocalSources
from governedgraph.admin.sources import AdminSourceError
from governedgraph.common.contracts import SuspensionEvent
from governedgraph.strategy_service.snapshot import RankingSnapshot, sign_snapshot
from strategy_doubles import local_strategy_service
from test_admin_azure import aggregates, build


def overview(service, directory):
    return LocalSources(service, directory).overview()

KEY = "first_contact|transition_impact_question|voice|standard"


def test_b3_b6_admin_shows_verified_rankings_with_eligibility_and_universal_veto(tmp_path):
    service = local_strategy_service(tmp_path / "strategy")
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
    result = overview(local_strategy_service(tmp_path / "strategy"), tmp_path)
    encoded = json.dumps(result)
    assert "PRIVATE-COHORT" not in encoded and "gender" not in encoded and "cohort_fcr_pct" not in encoded
    assert result["cases"][0]["finding"]["gap_pts"] == 25
    assert result["cases"][0]["case_id"] == "GC-1"


def test_b4_admin_does_not_run_learning_or_invent_approvals(tmp_path):
    service = local_strategy_service(tmp_path / "strategy")
    first = overview(service, tmp_path)
    second = overview(service, tmp_path)
    assert first["revision"] == second["revision"]
    assert first["state"]["current"] is None
    assert first["decisions"] == first["cases"] == first["actions"] == []
    assert all(row["source"] == "expert priors fallback" for row in first["rankings"])
    assert not list(tmp_path.rglob("*.jsonl"))


def test_b4_b7_admin_learning_view_reads_only_verified_published_aggregates():
    empty, _, _, _ = build()
    assert empty.overview()["learning"] == {"manifest": None, "aggregates": []}
    files = aggregates()
    source, _, _, _ = build(learning=files)
    result = source.overview()["learning"]
    assert result["manifest"]["observed_decisions"] == 10
    assert result["aggregates"][0]["interactions"] == 400
    digest_name = next(name for name in files if name.startswith("live/"))
    files[digest_name] += b"corrupted"
    corrupted, _, _, _ = build(learning=files)
    with pytest.raises(AdminSourceError, match="failed verification"):
        corrupted.overview()
