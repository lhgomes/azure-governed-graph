"""B4-B10: isolated component handoff, bounded publication and protective failure."""
import copy
import hashlib
import json
import logging
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from governedgraph.agents.fairness_explainability import FairnessExplainabilityAgent, run_cohort_audit
from governedgraph.common.config import DATA_DIR, REPO_ROOT
from governedgraph.common.contracts import ContractError
from governedgraph.learning.fairlearn_audit import aggregate_fcr, aggregate_repeat
from governedgraph.learning.pipeline_steps import PipelineState, STAGES, run_guarded_stage
from governedgraph.learning.ranking_pipeline import StrategyEvidence, load_outcome_aggregates, load_thresholds, max_shift
from governedgraph.learning.uplift import ArmOutcome, evaluate_window
from governedgraph.strategy_service.exploration import explore
from governedgraph.strategy_service.service import SnapshotRejected, StrategyService
from governedgraph.strategy_service.snapshot import LocalHmacSigner, RankingSnapshot, sign_snapshot
from governedgraph.strategy_service.state import LocalStateStore
from strategy_doubles import import_strategy_api, pinned_to_active

KEY = "first_contact|transition_impact_question|voice|standard"
REQUEST = {"journey_id": "J-x", "journey_state": "first_contact",
           "intent": "transition_impact_question", "channel": "voice"}
SIGNER = LocalHmacSigner("p2-components-test")
WINDOW = {"window_id": "synthetic-window", "window_closed": True,
          "treatment": {"interactions": 19000, "resolved": 14250, "repeat_contacts": 1500},
          "control": {"interactions": 1000, "resolved": 680, "repeat_contacts": 120},
          "watermark": "ILLUSTRATIVE - synthetic data"}


class LocalAdmin(StrategyService):
    def refresh(self):
        self._refresh(required=True)
        return self.read_state()


class Inputs:
    def __init__(self, root, service):
        self.root, self.service = root, service
        self.evidence = {}
        self.audit = None

    def pinned(self, name):
        source = DATA_DIR / ("synthetic/outcome_aggregates_illustrative.csv" if name == "outcomes"
                             else "fairness/cohort_aggregates_illustrative.csv")
        return pinned_to_active(self.service, source, self.root / "p2-inputs" / f"{name}.csv")

    def outcome_bytes(self):
        return self.pinned("outcomes").read_bytes()

    def run_fairness(self, service, *, expected_digest):
        assert expected_digest == hashlib.sha256(self.outcome_bytes()).hexdigest()
        self.audit = run_cohort_audit(load_thresholds(), self.pinned("cohorts"))
        FairnessExplainabilityAgent(service, self.root / "private-cohort-cases").apply_veto_gate(self.audit)
        return self.audit.audit_id

    def ranking_inputs(self, audit_id, thresholds):
        assert self.audit.audit_id == audit_id
        projected = copy.deepcopy(self.audit)
        projected.findings = []
        projected.vetoes = [(key, strategy, "protective-veto") for key, strategy, _ in self.audit.vetoes]
        return load_outcome_aggregates(self.pinned("outcomes"), catalogue=self.service.catalogue), projected

    def save_evidence(self, kind, identifier, payload):
        self.evidence[(kind, identifier)] = payload

    def uplift_input(self):
        return copy.deepcopy(WINDOW)


def setup(root):
    service = LocalAdmin(signer=SIGNER, store=LocalStateStore(root / "ranking" / "state.json"), control_share=0.0)
    return service, Inputs(root, service)


def advance(root, stop, *, sort_keys=False):
    service, inputs = setup(root)
    state = PipelineState()
    for stage in STAGES[:STAGES.index(stop) + 1]:
        state = run_guarded_stage(stage, state, inputs, service, SIGNER)
        state = PipelineState.from_dict(json.loads(json.dumps(asdict(state), sort_keys=sort_keys)))
    return state, inputs, service


@pytest.mark.parametrize("sort_keys", [False, True])
def test_worked_example_components_publish_s5_and_store_tier1_pack(tmp_path, sort_keys):
    state, inputs, service = advance(tmp_path, "uplift", sort_keys=sort_keys)
    result = service.get_ranked(REQUEST)
    assert result.ranked[0].strategy_id == "S5" and result.suspended == ["S2"]
    assert state.approved and state.snapshot["tier"] == "tier1"
    pack = inputs.evidence[("tier1", state.snapshot["snapshot_id"])]
    assert pack["approved"] and all(v["passed"] for v in pack["verdicts"])
    assert state.uplift["decision"] == "continue"
    assert inputs.evidence[("uplift", state.uplift["aggregate_digest"])] == state.uplift
    assert "age_band" not in json.dumps(asdict(state))
    assert "gender" not in json.dumps(asdict(state))
    assert "salary_band" not in json.dumps(asdict(state))
    assert all(c["shift"] <= 2 for c in state.candidates)


@pytest.mark.parametrize("stage", STAGES)
def test_failed_component_freezes_without_publishing_a_new_snapshot(tmp_path, monkeypatch, stage):
    import governedgraph.learning.pipeline_steps as steps

    service, inputs = setup(tmp_path)
    def fail(*args, **kwargs):
        raise OSError("synthetic component failure")
    monkeypatch.setattr(steps, "run_stage", fail)
    with pytest.raises(OSError, match="component failure"):
        run_guarded_stage(stage, PipelineState(), inputs, service, SIGNER)
    assert service.frozen and service.current is None


def test_candidate_tamper_cannot_be_signed(tmp_path):
    state, inputs, service = advance(tmp_path, "audit")
    state.candidates[0]["candidate"].reverse()
    with pytest.raises(ValueError, match="bounded reorder"):
        run_guarded_stage("veto", state, inputs, service, SIGNER)
    assert service.frozen and state.snapshot is None


@pytest.mark.parametrize("change", [
    lambda rows: rows.append(copy.deepcopy(rows[0])),
    lambda rows: rows.pop(),
    lambda rows: rows.__setitem__(1, copy.deepcopy(rows[0])),
    lambda rows: rows[0].update(request_key="foreign|request|voice|standard"),
    lambda rows: rows[0]["candidate"].reverse(),
    lambda rows: rows[0]["previous"].reverse(),
    lambda rows: rows[0]["scores"].update(S1=-1),
    lambda rows: rows[0].update(shift=999),
])
def test_serialized_candidate_changes_still_freeze_before_signing(tmp_path, change):
    state, inputs, service = advance(tmp_path, "audit", sort_keys=True)
    change(state.candidates)
    with pytest.raises(ValueError, match="bounded reorder"):
        run_guarded_stage("veto", state, inputs, service, SIGNER)
    assert service.frozen and state.snapshot is None


def test_candidate_request_key_order_is_not_strategy_order(tmp_path):
    state, inputs, service = advance(tmp_path, "audit", sort_keys=True)
    state.candidates.reverse()
    before = {row["request_key"]: list(row["candidate"]) for row in state.candidates}
    result = run_guarded_stage("veto", state, inputs, service, SIGNER)
    assert {row["request_key"]: row["candidate"] for row in result.candidates} == before
    assert result.completed_stage == "veto" and result.snapshot is None
    assert service.suspended_for("first_contact") == ["S2"] and not service.frozen


def test_veto_cannot_be_bypassed(tmp_path, monkeypatch):
    state, inputs, service = advance(tmp_path, "audit")
    monkeypatch.setattr(service, "suspended_for", lambda key: [])
    with pytest.raises(ValueError, match="veto was not applied"):
        run_guarded_stage("veto", state, inputs, service, SIGNER)
    assert service.frozen and state.snapshot is None


def test_audit_artifact_cannot_carry_a_cohort_column(tmp_path):
    state, _, _ = advance(tmp_path, "rank")
    raw = asdict(state)
    raw["evidence"][KEY]["S1"]["age_band"] = "synthetic"
    with pytest.raises(ContractError, match="B7"):
        PipelineState.from_dict(raw)
    with pytest.raises(ContractError):
        PipelineState.from_dict({**asdict(state), "transcript": "not permitted"})


def test_rank_baseline_changed_after_audit_refuses_publication(tmp_path):
    state, inputs, service = advance(tmp_path, "audit")
    replacement = sign_snapshot(RankingSnapshot("concurrent", service.catalogue.version, "1.0", state.audit_id,
                                               "tier0", {k: [[sid, service.catalogue.get(sid).version] for sid in v]
                                                         for k, v in state.previous.items()}), SIGNER)
    service.load_snapshot(replacement)
    with pytest.raises(ValueError, match="baseline changed"):
        run_guarded_stage("veto", state, inputs, service, SIGNER)
    assert service.frozen


def test_compare_and_swap_stops_a_racing_publisher(tmp_path):
    service, _ = setup(tmp_path)
    baseline = "expert-priors-" + service.priors["priors_version"]
    snapshot = sign_snapshot(RankingSnapshot("new", service.catalogue.version, "1.0", "audit", "tier0",
                                            {KEY: [["S5", service.catalogue.get("S5").version]]}), SIGNER)
    service.load_snapshot(snapshot, expected_snapshot_id=baseline)
    with pytest.raises(SnapshotRejected, match="baseline changed"):
        service.load_snapshot(snapshot, expected_snapshot_id=baseline)


def test_missing_durable_committee_pack_prevents_publish(tmp_path, monkeypatch):
    state, inputs, service = advance(tmp_path, "sign")
    def fail(*args):
        raise OSError("evidence store unavailable")
    monkeypatch.setattr(inputs, "save_evidence", fail)
    with pytest.raises(OSError, match="evidence store"):
        run_guarded_stage("tier", state, inputs, service, SIGNER)
    assert service.current is None and service.frozen


def test_closed_window_kill_switch_really_reverts_strategy_service(tmp_path):
    _, _, service = advance(tmp_path, "publish")
    window = {**WINDOW, "treatment": {"interactions": 10000, "resolved": 6800, "repeat_contacts": 1200}}
    result = evaluate_window(window, service)
    assert result["decision"] == "kill_switch"
    assert service.current is None and service.frozen
    assert service.suspended_for("first_contact") == ["S2"]


@pytest.mark.parametrize("bad", [{"age_band": "synthetic"}, {"transcript": "not permitted"},
                                  {"window_closed": "false"}, {"watermark": "unlabelled"}])
def test_uplift_window_rejects_unknown_or_untrusted_fields(tmp_path, bad):
    service, _ = setup(tmp_path)
    with pytest.raises(ValueError):
        evaluate_window({**WINDOW, **bad}, service)


@pytest.mark.parametrize("values", [(0, 0, 0), (10, 11, 0), (10, 2, 11), (10, -1, 0), (True, 1, 0)])
def test_uplift_rejects_invalid_counts(values):
    with pytest.raises(ValueError):
        ArmOutcome(*values)


@pytest.mark.parametrize("values", [(10, 11, 0), (10, 2, 11), (-1, 0, 0), (True, 1, 0)])
def test_rank_rejects_invalid_aggregate_counts(values):
    with pytest.raises(ValueError):
        StrategyEvidence("S1", *values)


def test_aggregate_metric_preserves_core_half_point_rounding():
    assert aggregate_fcr([510], [100], sample_weight=[800]) == 63.75
    assert round(aggregate_fcr([510], [100], sample_weight=[800]), 1) == 63.8
    assert aggregate_repeat([510], [100], sample_weight=[800]) == 12.5


@pytest.mark.parametrize("rate", [-1, 1.01, float("nan"), float("inf"), True])
def test_exploration_rejects_invalid_board_bounds(rate):
    with pytest.raises(ValueError):
        explore(["S1", "S5", "S2"], "J-test", KEY, rate, 2)


def test_exploration_is_stable_bounded_and_never_adds_strategies():
    ranking = ["S1", "S5", "S2", "S4", "S3"]
    for i in range(100):
        result, explored = explore(ranking, f"J-{i}", KEY, 1.0, 2)
        assert explored and result[0] in {"S5", "S2"}
        assert set(result) == set(ranking) and max_shift(ranking, result) <= 2
        assert (result, explored) == explore(ranking, f"J-{i}", KEY, 1.0, 2)
    assert explore(ranking, "J-test", KEY, 0.0, 2) == (ranking, False)


def test_exact_approved_exploration_rate_is_respected():
    rate = load_thresholds()["exploration_rate"]
    selected = sum(explore(["S1", "S5", "S2"], f"J-{i}", KEY, rate, 2)[1] for i in range(10000))
    assert 400 < selected < 600


def test_control_frozen_and_fallback_never_explore(tmp_path, caplog):
    state, _, service = advance(tmp_path, "publish")
    service.thresholds = {**service.thresholds, "exploration_rate": 1.0}
    assert not service.get_ranked(REQUEST).exploration
    service.exploration_enabled = True
    with caplog.at_level(logging.INFO, logger="governedgraph.strategy_service"):
        treatment = service.get_ranked(REQUEST)
    assert treatment.exploration and "strategy_exploration" in caplog.text
    assert "S2" not in [s.strategy_id for s in treatment.ranked]
    service.control_share = 1.0
    assert not service.get_ranked(REQUEST).exploration
    service.control_share = 0.0
    service.freeze()
    assert not service.get_ranked(REQUEST).exploration
    service.revert_to_static()
    assert not service.get_ranked(REQUEST).exploration


def test_revert_endpoint_uses_existing_protective_authorization(tmp_path, monkeypatch):
    import governedgraph.strategy_service.auth as auth

    api = import_strategy_api(monkeypatch, tmp_path)
    _, _, service = advance(tmp_path, "publish")
    monkeypatch.setattr(api, "svc", service)
    authorized = []
    monkeypatch.setattr(auth, "authorize", lambda action, authorization: authorized.append(action) or {})
    response = TestClient(api.app).post("/revert-to-static")
    assert response.status_code == 200 and authorized == ["freeze"]
    assert service.frozen and service.current is None


def test_revert_endpoint_does_not_bypass_entra_authorization(tmp_path, monkeypatch):
    import governedgraph.strategy_service.auth as auth
    from fastapi import HTTPException

    api = import_strategy_api(monkeypatch, tmp_path)

    seen = []
    def deny(action, authorization):
        seen.append(action)
        raise HTTPException(403, "synthetic identity denied")
    monkeypatch.setattr(auth, "settings", SimpleNamespace(is_azure=True))
    monkeypatch.setattr(auth, "authorize", deny)
    assert TestClient(api.app).post("/revert-to-static").status_code == 403
    assert seen == ["freeze"]
    assert "{ name: 'revert-to-static', method: 'POST', path: '/revert-to-static' }" in (
        REPO_ROOT / "infra/modules/gateway.bicep"
    ).read_text()


def test_protective_fairness_client_never_reads_a_snapshot_or_signing_key():
    from governedgraph.strategy_service.client import StrategyAdminClient

    client = object.__new__(StrategyAdminClient)
    client.protective_only = True
    with pytest.raises(PermissionError, match="cannot read or verify"):
        client.refresh()
    with pytest.raises(PermissionError, match="cannot publish"):
        client._post("snapshots", {})


def test_azure_fairness_failure_freezes_and_does_not_mask_error(monkeypatch):
    from governedgraph.learning.azure_inputs import BlobLearningInputs

    inputs = object.__new__(BlobLearningInputs)
    events = []
    def fail(*args, **kwargs):
        raise OSError("private audit unavailable")
    monkeypatch.setattr(inputs, "_run_fairness", fail)
    with pytest.raises(OSError, match="private audit unavailable"):
        inputs.run_fairness(SimpleNamespace(freeze=lambda: events.append("freeze")))
    assert events == ["freeze"]


def test_protective_retry_never_undoes_static_kill_switch(tmp_path):
    _, _, service = advance(tmp_path, "publish")
    replacement = copy.deepcopy(service.current)
    replacement.snapshot_id = "second"
    sign_snapshot(replacement, SIGNER)
    service.load_snapshot(replacement)
    assert service.last_good is not None
    service.revert_to_static()
    service.freeze()
    assert service.frozen and service.current is None


def test_production_image_does_not_embed_cohorts_or_personas():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()
    assert "examples/" not in dockerfile and "pension" not in dockerfile
    framework_p2 = (REPO_ROOT / "Dockerfile.p2").read_text()
    assert "examples/" not in framework_p2 and "pension" not in framework_p2
    assert "pip install --no-cache-dir -r requirements-p2.txt && pip check" in framework_p2
    assert "requirements-azure.txt" not in framework_p2
    assert "requirements-p2.txt" not in dockerfile
    overlay = (REPO_ROOT / "examples/pension_wtp/Dockerfile").read_text()
    offline_overlay = (REPO_ROOT / "examples/pension_wtp/Dockerfile.p2").read_text()
    base, builder_and_runtime = overlay.split("FROM sample-base AS simulation-builder", 1)
    builder, runtime_and_probe = builder_and_runtime.split("FROM sample-base AS runtime", 1)
    runtime_stage = runtime_and_probe.split("FROM runtime AS probe", 1)[0]
    runtime = base + runtime_stage
    p2 = offline_overlay.split("FROM p2 AS fixtures")[0]
    assert "members.json" in runtime and "members.json" not in p2
    for source in (runtime, p2):
        assert "cohort_aggregates" not in source and "personas" not in source and "outcome_aggregates" not in source
    assert "data/personas/personas.json" in builder
    assert "cohort_aggregates" not in builder and "outcome_aggregates" not in builder
    assert runtime_stage.strip().splitlines() == [
        ("COPY --from=simulation-builder --chown=10001:10001 /tmp/simulations.json "
         "./examples/pension_wtp/data/demo/simulations.json"),
    ]
    assert "FROM p2 AS fixtures" in offline_overlay and "FROM runtime AS probe" in overlay
    assert "FRAMEWORK_RUNTIME" not in offline_overlay
    assert "FRAMEWORK_P2" not in overlay


def test_watchdog_fails_closed_on_failed_or_missing_component_metadata():
    from governedgraph.learning.p2_components import STEPS, check_component_states

    assert not check_component_states({})
    assert not check_component_states({"audit": "Running"})
    assert check_component_states({name: "Completed" for name in STEPS})
    for status in ("Failed", "Canceled", "NotResponding"):
        with pytest.raises(RuntimeError, match="freeze required"):
            check_component_states({"audit": status})


def test_fairness_component_has_no_key_vault_configuration(monkeypatch):
    from governedgraph.learning.p2_components import P2Config, component_environment

    config = P2Config("sub", "rg", "https://storage.blob.core.windows.net", "registry.azurecr.io",
                      "p2", "fairness", "principal")
    for name in ("AZURE_KEY_VAULT_URL", "SNAPSHOT_SIGNING_KEY_NAME", "RANKING_STORAGE_URL",
                 "EXPLAINABILITY_STORAGE_URL", "STRATEGY_ADMIN_URL", "STRATEGY_API_CLIENT_ID"):
        monkeypatch.setenv(name, "synthetic")
    audit = component_environment(config, "audit")
    assert audit["AZURE_CLIENT_ID"] == "fairness"
    assert "AZURE_KEY_VAULT_URL" not in audit and "SNAPSHOT_SIGNING_KEY_NAME" not in audit
    assert component_environment(config, "sign")["AZURE_CLIENT_ID"] == "p2"


def test_azure_api_enables_exploration_from_start_without_a_rollout_flag():
    text = (REPO_ROOT / "src/governedgraph/strategy_service/api.py").read_text()
    assert "svc = StrategyService(exploration_enabled=True)" in text
    with pytest.raises(ValueError, match="boolean"):
        StrategyService(exploration_enabled="true")


def test_native_components_export_only_allowlisted_completion_metrics(tmp_path):
    from governedgraph.learning.p2_components import component_metrics

    state, _, _ = advance(tmp_path, "uplift")
    assert component_metrics("uplift", asdict(state)) == {
        "component_success": 1.0, "tier1_evidence_stored": 1.0, "uplift_continue": 1.0,
    }
    assert component_metrics("audit", {"untrusted": "never exported"}) == {"component_success": 1.0}
    with pytest.raises(ValueError, match="missing"):
        component_metrics("key-isolation", {})
    with pytest.raises(ValueError, match="invalid"):
        component_metrics("uplift", asdict(PipelineState()))


@pytest.mark.parametrize("outcome", ["stored", "retry", "conflict", "unavailable"])
def test_b8_durable_evidence_requires_exact_blob_readback(outcome):
    from azure.core.exceptions import ResourceExistsError
    from governedgraph.learning.azure_inputs import BlobLearningInputs

    writes = []
    def upload(encoded, *, overwrite):
        assert overwrite is False
        writes.append(encoded)
        if outcome == "retry":
            raise ResourceExistsError("already stored")
    def download():
        if outcome == "unavailable":
            raise OSError("readback unavailable")
        return SimpleNamespace(readall=lambda: b"conflict" if outcome == "conflict" else writes[0])
    inputs = object.__new__(BlobLearningInputs)
    inputs.private = SimpleNamespace(get_blob_client=lambda *args: SimpleNamespace(
        upload_blob=upload, download_blob=download))
    if outcome in {"stored", "retry"}:
        inputs.save_evidence("tier1", "snap-synthetic", {"approved": True})
    else:
        with pytest.raises(ValueError if outcome == "conflict" else OSError):
            inputs.save_evidence("tier1", "snap-synthetic", {"approved": True})

