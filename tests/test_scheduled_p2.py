"""B3/B6/B7/B8: native schedules require fresh successful isolated audit and renewable lease."""
import hashlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from governedgraph.learning import scheduled_jobs as jobs
from governedgraph.orchestrator.store import LeaseLost
from governedgraph.strategy_service.recovery import latest_successful_audit
from governedgraph.strategy_service.state import StateUnavailable

NOW = datetime(2026, 10, 25, 20, 2, tzinfo=timezone.utc)
IMAGE = "registry/p2@sha256:" + "a" * 64
CYCLE, START = jobs.cycle_window(NOW, 300)
RAW = b"synthetic aggregate bytes"


def audit():
    return {"cycle_id": CYCLE, "cadence_seconds": 300, "image": IMAGE, "audit_id": "audit-test",
            "outcome_digest": hashlib.sha256(RAW).hexdigest(), "started_at": START.isoformat(),
            "completed_at": (START + timedelta(seconds=33)).isoformat(),
            "execution_id": "job-gg-p2-fairness-test", "watermark": jobs.WATERMARK}


def metadata(status="Succeeded"):
    return {"name": "job-gg-p2-fairness-test", "properties": {
        "status": status, "startTime": START.isoformat(), "template": {"containers": [{
            "image": IMAGE, "command": jobs.COMMAND,
            "env": [{"name": key, "value": value} for key, value in {
                "GOVERNEDGRAPH_P2_ROLE": "fairness", "GOVERNEDGRAPH_P2_STAGE": "audit", "GOVERNEDGRAPH_P2_IMAGE": IMAGE,
                "GOVERNEDGRAPH_P2_CADENCE_SECONDS": "300",
            }.items()],
        }]},
    }}


def test_b5_native_schedule_windows_are_utc_and_share_the_same_cycle_despite_offset():
    assert jobs.cycle_window(START, 300)[0] == CYCLE
    assert jobs.cycle_window(START + timedelta(seconds=120), 300)[0] == CYCLE
    assert jobs.cycle_window(START + timedelta(seconds=300), 300)[0] != CYCLE
    for invalid in (True, 0, 60, 301):
        with pytest.raises(ValueError):
            jobs.cycle_window(NOW, invalid)
    with pytest.raises(ValueError):
        jobs.cycle_window(NOW.replace(tzinfo=None), 300)


@pytest.mark.parametrize("change", [
    {"cycle_id": "old-cycle"}, {"cadence_seconds": 14400}, {"image": "wrong"},
    {"watermark": "unlabelled"}, {"age_band": "not-permitted"},
    {"completed_at": (NOW + timedelta(seconds=1)).isoformat()},
    {"started_at": (START - timedelta(seconds=1)).isoformat()},
    {"completed_at": "2026-10-25T20:00:00"}, {"outcome_digest": "wrong"},
])
def test_b7_same_cycle_handoff_is_closed_and_rejects_stale_future_or_foreign_audits(change):
    assert jobs.validate_audit(audit(), CYCLE, 300, IMAGE, NOW) == "audit-test"
    with pytest.raises(ValueError):
        jobs.validate_audit(audit() | change, CYCLE, 300, IMAGE, NOW)


@pytest.mark.parametrize("status", ["Failed", "Running", "Stopped", "Unknown"])
def test_b6_manifest_alone_is_not_successful_audit_execution(status):
    with pytest.raises(ValueError):
        jobs.check_execution(audit(), metadata(status))


def test_b8_audit_metadata_requires_actual_isolated_command_image_cycle_and_identity_role():
    jobs.check_execution(audit(), metadata())
    for field, value in (("image", "untrusted"), ("command", ["python", "--version"])):
        row = metadata()
        row["properties"]["template"]["containers"][0][field] = value
        with pytest.raises(ValueError):
            jobs.check_execution(audit(), row)
    row = metadata()
    row["properties"]["startTime"] = (START - timedelta(minutes=5)).isoformat()
    with pytest.raises(ValueError):
        jobs.check_execution(audit(), row)
    assert latest_successful_audit([metadata()], IMAGE) == "job-gg-p2-fairness-test"
    with pytest.raises(StateUnavailable):
        latest_successful_audit([metadata("Failed")], IMAGE)


def test_b6_pipeline_reuses_core_stages_but_never_runs_cohort_audit_under_p2(monkeypatch):
    calls, saved, checks = [], [], []

    def stage(name, state, inputs, service, signer):
        calls.append(name)
        state.completed_stage = name
        return state

    monkeypatch.setattr(jobs, "run_guarded_stage", stage)
    inputs = SimpleNamespace(outcome_bytes=lambda: RAW,
                             ranking_inputs=lambda audit_id, thresholds: checks.append(audit_id))
    result = jobs.run_pipeline(
        inputs, object(), audit(), CYCLE, 300, IMAGE, lambda stage, state: saved.append(stage),
        lambda: checks.append("lease"), lambda: NOW,
        lambda record: jobs.check_execution(record, metadata()), signer_factory=lambda **kwargs: object(),
    )
    assert calls == ["aggregate", "rank", "veto", "sign", "tier", "publish", "uplift"]
    assert saved == ["aggregate", "rank", "audit", "veto", "sign", "tier", "publish", "uplift"]
    assert result.completed_stage == "uplift" and result.audit_id == "audit-test"


def test_b5_native_worker_runs_real_bounded_domain_and_stores_tier1_evidence(tmp_path):
    from test_p2_components import REQUEST, SIGNER, setup

    service, inputs = setup(tmp_path)
    audit_id = inputs.run_fairness(service, expected_digest=hashlib.sha256(inputs.outcome_bytes()).hexdigest())
    record = audit() | {"audit_id": audit_id, "outcome_digest": hashlib.sha256(inputs.outcome_bytes()).hexdigest()}
    stages = []
    state = jobs.run_pipeline(
        inputs, service, record, CYCLE, 300, IMAGE, lambda stage, raw: stages.append(stage),
        lambda: None, lambda: NOW, lambda _: None, signer_factory=lambda **kwargs: SIGNER,
    )
    served = service.get_ranked(REQUEST)
    assert served.ranked[0].strategy_id == "S5" and served.suspended == ["S2"]
    assert state.approved and state.uplift["decision"] == "continue"
    assert inputs.evidence[("tier1", state.snapshot["snapshot_id"])]["approved"]
    assert stages[-2:] == ["publish", "uplift"]


@pytest.mark.parametrize("failure", ["missing", "stale", "changed-input", "failed", "lease-lost"])
def test_b6_missing_failed_stale_audit_or_lost_lease_never_reaches_publication(monkeypatch, failure):
    calls = []
    monkeypatch.setattr(jobs, "run_guarded_stage", lambda stage, *args: calls.append(stage))
    inputs = SimpleNamespace(outcome_bytes=lambda: b"changed" if failure == "changed-input" else RAW)

    def lease():
        if failure == "lease-lost":
            raise LeaseLost("synthetic lease loss")

    record = audit()
    if failure == "missing":
        record = {}
    if failure == "stale":
        record["cycle_id"] = "old-cycle"
    with pytest.raises((ValueError, LeaseLost)):
        jobs.run_pipeline(inputs, object(), record, CYCLE, 300, IMAGE, lambda *_: None, lease,
                          lambda: NOW, lambda record: jobs.check_execution(
                              record, metadata("Failed" if failure == "failed" else "Succeeded")))
    assert "publish" not in calls


def test_b6_rechecks_cadence_and_lease_immediately_before_publication(monkeypatch):
    calls = []

    def stage(name, state, inputs, service, signer):
        calls.append(name)
        if name == "tier":
            record["cycle_id"] = "expired"
        state.completed_stage = name
        return state

    record = audit()
    monkeypatch.setattr(jobs, "run_guarded_stage", stage)
    inputs = SimpleNamespace(outcome_bytes=lambda: RAW, ranking_inputs=lambda *_: None)
    with pytest.raises(ValueError):
        jobs.run_pipeline(inputs, object(), record, CYCLE, 300, IMAGE, lambda *_: None, lambda: None,
                          lambda: NOW, lambda record: None, signer_factory=lambda **kwargs: None)
    assert "publish" not in calls


def test_b6_missing_audit_execute_freezes_without_signing_or_publishing():
    frozen = []

    @contextmanager
    def transaction(_):
        yield SimpleNamespace(write=lambda _: None)

    missing = SimpleNamespace(get_blob_client=lambda _: SimpleNamespace(
        download_blob=lambda: (_ for _ in ()).throw(FileNotFoundError("no cycle audit"))))
    with pytest.raises(FileNotFoundError):
        jobs.execute("pipeline", 300, IMAGE, SimpleNamespace(get_blob_client=lambda _: SimpleNamespace(exists=lambda: False)), missing,
                     SimpleNamespace(transaction=transaction), object(),
                     SimpleNamespace(freeze=lambda: frozen.append(True)),
                     lambda _: None, "job-gg-p2-pipeline-test", clock=lambda: NOW)
    assert frozen == [True]


def test_b7_native_schedule_adds_no_start_permission_identity_or_container():
    root = Path(__file__).resolve().parents[1]
    bicep = (root / "infra/modules/p2-jobs.bicep").read_text()
    assert "scheduleTriggerConfig" in bicep and "parallelism: 1" in bicep and "replicaCompletionCount: 1" in bicep
    assert "'0 */4 * * *'" in bicep and "'2 */4 * * *'" in bicep
    assert "'governedgraph.learning.scheduled_jobs'" in bicep and "replicaRetryLimit: 0" in bicep
    for forbidden in ("roleAssignments", "roleDefinitions", "managedIdentities", "/start/action"):
        assert forbidden not in bicep
