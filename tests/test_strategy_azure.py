"""B3/B6/B8/B10: durable, verified state and separately authorized mutations."""
import copy
import hashlib
import json
import logging
import math
import sys
import time
from dataclasses import asdict, replace
from types import ModuleType, SimpleNamespace
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient

from governedgraph.common.contracts import ContractError, SuspensionEvent
from governedgraph.strategy_service.service import SnapshotRejected, StrategyService
from governedgraph.strategy_service.snapshot import LocalHmacSigner, RankingSnapshot, sign_snapshot
from governedgraph.strategy_service.state import StateConflict, StateUnavailable

KEY = "first_contact|transition_impact_question|voice|standard"
SIGNER = LocalHmacSigner("azure-state-test")
REQUEST = {"journey_id": "J-1", "journey_state": "first_contact",
           "intent": "transition_impact_question", "channel": "voice"}
EVENT = SuspensionEvent("S2", "1.0", "first_contact", "audit-test", 26.0, "1.0", "GC-test")


class MemoryState:
    def __init__(self):
        self.state = None
        self.etag = None
        self.read_failure = False
        self.write_failure = False
        self.conflict = None
        self.reads = 0

    def read(self, etag=None):
        self.reads += 1
        if self.read_failure:
            raise StateUnavailable("simulated outage")
        return (None if etag == self.etag else copy.deepcopy(self.state)), self.etag

    def write(self, state, etag):
        if self.write_failure:
            raise StateUnavailable("simulated write outage")
        if self.conflict:
            callback, self.conflict = self.conflict, None
            callback()
        if etag != self.etag:
            raise StateConflict("simulated conflicting writer")
        self.state = copy.deepcopy(state)
        self.etag = str(int(self.etag or "0") + 1)
        return self.etag


def snapshot(name="snap-one", order=("S2", "S5", "S1", "S4", "S3")):
    return sign_snapshot(RankingSnapshot(name, "1.0.0", "1.0", "audit-test", "tier0",
                                        {KEY: [[sid, "1.0"] for sid in order]}), SIGNER)


def service(store, tmp_path, share=0.0):
    return StrategyService(store=store, signer=SIGNER, state_dir=tmp_path, control_share=share)


def test_b6_suspension_visible_on_next_request_across_two_instances(tmp_path):
    store = MemoryState()
    a, b = service(store, tmp_path / "a"), service(store, tmp_path / "b")
    a.load_snapshot(snapshot())
    assert b.get_ranked(REQUEST).ranked[0].strategy_id == "S2"
    a.suspend(EVENT)
    for jid in ("J-1", "J-2"):
        result = b.get_ranked({**REQUEST, "journey_id": jid})
        assert result.suspended == ["S2"]
        assert "S2" not in [r.strategy_id for r in result.ranked]


def test_b3_restart_verifies_current_and_explicit_rollback(tmp_path):
    store = MemoryState()
    a = service(store, tmp_path)
    a.load_snapshot(snapshot("old"))
    a.load_snapshot(snapshot("new", ("S5", "S2", "S1", "S4", "S3")))
    restored = service(store, tmp_path)
    assert restored.current.snapshot_id == "new"
    assert restored.last_good.snapshot_id == "old"
    restored.freeze()
    again = service(store, tmp_path)
    assert again.frozen and again.current.snapshot_id == "old"
    with pytest.raises(SnapshotRejected, match="frozen"):
        again.load_snapshot(snapshot("forbidden"))


@pytest.mark.parametrize("field", ["current", "last_good"])
@pytest.mark.parametrize("invalid", ["signature", "catalogue", "inactive"])
def test_b3_tampered_or_inactive_persisted_snapshots_never_served(tmp_path, field, invalid, caplog):
    store = MemoryState()
    a = service(store, tmp_path)
    a.load_snapshot(snapshot("old"))
    a.load_snapshot(snapshot("new"))
    bad = RankingSnapshot.from_dict(store.state[field])
    if invalid == "signature":
        bad.rankings[KEY] = [["S3", "1.0"]]
    elif invalid == "catalogue":
        bad.catalogue_version = "retired"
        sign_snapshot(bad, SIGNER)
    else:
        bad.rankings[KEY] = [["S2", "9.9"]]
        sign_snapshot(bad, SIGNER)
    store.state[field] = bad.to_dict()
    store.etag = "99"
    restored = service(store, tmp_path)
    assert restored.get_ranked(REQUEST).source == "expert_priors_fallback"
    assert "strategy_state_alert" in caplog.text
    with pytest.raises(StateUnavailable):
        restored.freeze()


def test_b6_read_failure_retains_known_suspensions_and_uses_priors(tmp_path, caplog):
    store = MemoryState()
    a = service(store, tmp_path)
    a.load_snapshot(snapshot())
    a.suspend(EVENT)
    store.read_failure = True
    result = a.get_ranked(REQUEST)
    assert result.source == "expert_priors_fallback" and result.suspended == ["S2"]
    assert "S2" not in [r.strategy_id for r in result.ranked]
    assert "strategy_state_alert" in caplog.text
    store.read_failure = False
    assert a.get_ranked(REQUEST).signature_valid


def test_b9_startup_storage_failure_serves_priors_and_alerts(tmp_path, caplog):
    store = MemoryState()
    store.read_failure = True
    a = service(store, tmp_path)
    assert a.get_ranked(REQUEST).source == "expert_priors_fallback"
    assert "strategy_state_alert" in caplog.text


@pytest.mark.parametrize("operation", ["suspend", "freeze"])
def test_b6_failed_protective_write_is_not_success(tmp_path, operation, caplog):
    store = MemoryState()
    a = service(store, tmp_path)
    a.load_snapshot(snapshot())
    store.write_failure = True
    with pytest.raises(StateUnavailable):
        a.suspend(EVENT) if operation == "suspend" else a.freeze()
    assert not a.frozen and a.suspended_for("first_contact") == []
    assert "strategy_state_write_failed" in caplog.text


def test_b6_etag_retry_does_not_lose_concurrent_suspension_or_freeze(tmp_path):
    store = MemoryState()
    a, b = service(store, tmp_path / "a"), service(store, tmp_path / "b")
    a.load_snapshot(snapshot())
    store.conflict = lambda: b.suspend(EVENT)
    a.freeze()
    restored = service(store, tmp_path)
    assert restored.frozen and restored.suspended_for("first_contact") == ["S2"]


def test_b10_control_arm_stays_static_with_live_overlay(tmp_path):
    store = MemoryState()
    a = service(store, tmp_path, share=1.0)
    a.load_snapshot(snapshot())
    a.suspend(EVENT)
    result = a.get_ranked(REQUEST)
    assert result.arm == "control" and result.source == "expert_priors"
    assert [r.strategy_id for r in result.ranked] == ["S1", "S5", "S4", "S3"]


def test_strategy_audit_events_include_snapshot_id(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="governedgraph.strategy_service")
    a = service(MemoryState(), tmp_path)
    a.load_snapshot(snapshot())
    a.suspend(EVENT)
    a.freeze()
    for event in ("strategy_snapshot_load", "strategy_suspension", "strategy_freeze"):
        assert any(r.message == event and r.snapshot_id == "snap-one" for r in caplog.records)


def test_get_ranked_in_memory_p99_below_50ms(tmp_path):
    a = service(MemoryState(), tmp_path)
    a.load_snapshot(snapshot())
    for _ in range(100):
        a.get_ranked(REQUEST)
    durations = []
    for _ in range(5000):
        start = time.perf_counter_ns()
        a.get_ranked(REQUEST)
        durations.append((time.perf_counter_ns() - start) / 1_000_000)
    p99 = sorted(durations)[math.ceil(0.99 * len(durations)) - 1]
    print(f"get_ranked in-memory p99={p99:.3f}ms across {len(durations)} measured requests")
    assert p99 < 50, f"get_ranked p99={p99:.3f}ms, limit=50ms"


@pytest.fixture
def authorization(monkeypatch):
    from governedgraph.strategy_service import auth

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    tenant = "00000000-0000-0000-0000-000000000001"
    monkeypatch.setattr(auth, "settings", replace(auth.settings, mode="azure", strategy_tenant_id=tenant,
                       strategy_api_client_id="strategy-api", strategy_publisher_id="p2",
                       strategy_fairness_id="fairness", strategy_runtime_id="runtime",
                       strategy_governance_group=""))
    monkeypatch.setattr(auth, "signing_keys", lambda _: SimpleNamespace(
        get_signing_key_from_jwt=lambda token: SimpleNamespace(key=private.public_key())))

    def token(oid, roles, **overrides):
        claims = {"iss": f"https://login.microsoftonline.com/{tenant}/v2.0", "aud": "strategy-api",
                  "tid": tenant, "oid": oid, "iat": int(time.time()), "exp": int(time.time()) + 300,
                  "idtyp": "app", "roles": roles}
        claims.update(overrides)
        return "Bearer " + jwt.encode(claims, private, algorithm="RS256")

    return auth, token


@pytest.mark.parametrize(("action", "oid", "role"), [
    ("publish", "p2", "Strategy.Publish"), ("suspend", "fairness", "Strategy.Suspend"),
    ("freeze", "p2", "Strategy.Freeze"), ("freeze", "fairness", "Strategy.Freeze"),
    ("freeze", "runtime", "Strategy.Freeze"),
])
def test_b8_only_assigned_identities_and_roles_mutate(authorization, action, oid, role):
    auth, token = authorization
    auth.authorize(action, token(oid, [role]))
    with pytest.raises(HTTPException) as error:
        auth.authorize(action, token("strategy-service", [role]))
    assert error.value.status_code == 403
    with pytest.raises(HTTPException):
        auth.authorize(action, token(oid, []))


@pytest.mark.parametrize(("oid", "role", "action"), [
    ("runtime", "Strategy.Publish", "publish"), ("runtime", "Strategy.Suspend", "suspend"),
    ("p2", "Strategy.Suspend", "suspend"), ("fairness", "Strategy.Publish", "publish"),
])
def test_b8_cross_plane_mutations_denied_even_with_wrongly_assigned_role(authorization, oid, role, action):
    auth, token = authorization
    with pytest.raises(HTTPException) as error:
        auth.authorize(action, token(oid, [role]))
    assert error.value.status_code == 403


def test_b8_bad_or_delegated_tokens_and_agent_reinstatement_rejected(authorization):
    auth, token = authorization
    for bearer in ("", "Bearer bad", token("p2", ["Strategy.Publish"], exp=1),
                   token("p2", ["Strategy.Publish"], aud="other-api")):
        with pytest.raises(HTTPException) as error:
            auth.authorize("publish", bearer)
        assert error.value.status_code == 401
    for claims in ({"idtyp": "user"}, {"scp": "mutate"}):
        with pytest.raises(HTTPException) as error:
            auth.authorize("publish", token("p2", ["Strategy.Publish"], **claims))
        assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        auth.authorize("reinstate", token("fairness", ["Strategy.Suspend"], groups=["human-group"]))
    assert error.value.status_code == 403


def test_strategy_api_auth_and_failed_writes_are_explicit(authorization, tmp_path, monkeypatch):
    from governedgraph.strategy_service import api

    auth, token = authorization
    store = MemoryState()
    monkeypatch.setattr(api, "svc", service(store, tmp_path))
    client = TestClient(api.app)
    assert client.post("/snapshots", json=snapshot().to_dict()).status_code == 401
    assert client.post("/snapshots", json=snapshot().to_dict(), headers={
        "Authorization": token("runtime", ["Strategy.Freeze"])}).status_code == 403
    assert client.post("/snapshots", json=snapshot().to_dict(), headers={
        "Authorization": token("p2", ["Strategy.Publish"])}).status_code == 200
    store.write_failure = True
    assert client.post("/suspensions", json=asdict(EVENT), headers={
        "Authorization": token("fairness", ["Strategy.Suspend"])}).status_code == 503
    assert client.post("/freeze", headers={"Authorization": token("runtime", ["Strategy.Freeze"])}).status_code == 503
    read = client.post("/strategies/ranked", json=REQUEST)
    assert read.status_code == 200
    assert read.headers["X-Strategy-Replica"]
    assert read.headers["Server-Timing"].startswith("get_ranked;dur=")


def test_b7_suspension_contract_rejects_demographics():
    with pytest.raises(ContractError):
        SuspensionEvent.from_dict({**asdict(EVENT), "age_band": "75+"})


@pytest.fixture
def blob_store(monkeypatch):
    from governedgraph.strategy_service.state import BlobStateStore

    class AzureError(Exception):
        pass

    class Exists(AzureError):
        pass

    class Modified(AzureError):
        pass

    class Missing(AzureError):
        error_code = "BlobNotFound"

    core = ModuleType("azure.core")
    core.MatchConditions = SimpleNamespace(IfNotModified="IfNotModified")
    errors = ModuleType("azure.core.exceptions")
    errors.AzureError, errors.ResourceExistsError = AzureError, Exists
    errors.ResourceModifiedError = Modified
    errors.ResourceNotFoundError = Missing
    monkeypatch.setitem(sys.modules, "azure", ModuleType("azure"))
    monkeypatch.setitem(sys.modules, "azure.core", core)
    monkeypatch.setitem(sys.modules, "azure.core.exceptions", errors)
    data = {}
    calls = []

    class Blob:
        def __init__(self, name):
            self.name = name

        def upload_blob(self, raw, **options):
            calls.append(("write", self.name, options))
            existing = data.get(self.name)
            if existing and not options.get("overwrite"):
                raise Exists()
            if options.get("match_condition") == "IfNotModified" and (
                    existing is None or options["etag"] != existing[1]):
                raise Modified()
            etag = str(int(existing[1]) + 1) if existing else "1"
            data[self.name] = raw, etag
            return {"etag": etag}

        def get_blob_properties(self):
            calls.append(("properties", self.name, {}))
            if self.name not in data:
                raise Missing()
            return SimpleNamespace(etag=data[self.name][1])

        def download_blob(self, **options):
            calls.append(("read", self.name, options))
            if self.name not in data:
                raise Missing()
            raw, etag = data[self.name]
            return SimpleNamespace(readall=lambda: raw, properties=SimpleNamespace(etag=etag))

    store = BlobStateStore.__new__(BlobStateStore)
    store.container = SimpleNamespace(get_blob_client=Blob)
    store.blob = Blob("state.json")
    return store, data, calls


def test_b3_blob_store_retains_explicit_verified_last_good_and_conditional_state(blob_store, tmp_path):
    store, data, calls = blob_store
    a = service(store, tmp_path)
    a.load_snapshot(snapshot("previous"))
    a.load_snapshot(snapshot("current"))
    assert any(name.startswith("last_good/") for name in data)
    restored = service(store, tmp_path)
    assert restored.current.snapshot_id == "current"
    assert restored.last_good.snapshot_id == "previous"
    before = len(calls)
    restored.get_ranked(REQUEST)
    assert calls[before:] == [("properties", "state.json", {})]
    assert any(kind == "write" and name == "state.json" and options.get("match_condition") == "IfNotModified"
               for kind, name, options in calls)
    before = dict(data)
    bad = snapshot("tampered")
    bad.rankings[KEY] = [["S3", "1.0"]]
    with pytest.raises(SnapshotRejected):
        restored.load_snapshot(bad)
    assert before == data


def test_b3_missing_or_modified_snapshot_blob_is_not_empty_state(blob_store, tmp_path, caplog):
    store, data, _ = blob_store
    a = service(store, tmp_path)
    a.load_snapshot(snapshot())
    snapshot_blob = next(name for name in data if name.startswith("current/"))
    data[snapshot_blob] = b"tampered", "2"
    assert service(store, tmp_path).get_ranked(REQUEST).source == "expert_priors_fallback"
    del data[snapshot_blob]
    assert service(store, tmp_path).get_ranked(REQUEST).source == "expert_priors_fallback"
    assert "strategy_state_alert" in caplog.text


def test_b6_blob_cas_conflict_is_explicit(blob_store):
    store, _, _ = blob_store
    state = {"current": None, "last_good": None, "frozen": False, "suspensions": []}
    etag = store.write(state, None)
    store.write({**state, "frozen": True}, etag)
    with pytest.raises(StateConflict):
        store.write(state, etag)


@pytest.mark.parametrize("status", [304, 412])
def test_blob_read_does_not_treat_unrequested_304_or_412_as_a_cache_hit(blob_store, status):
    from azure.core.exceptions import ResourceModifiedError

    store, _, _ = blob_store
    store.write({"current": None, "last_good": None, "frozen": False, "suspensions": []}, None)
    error = ResourceModifiedError()
    error.status_code = status

    def download(**kwargs):
        raise error

    store.blob.download_blob = download
    with pytest.raises(StateUnavailable, match="could not read"):
        store.read('"cached"')


@pytest.mark.parametrize("status", [403, 412, 500])
def test_b3_blob_version_check_failure_is_not_an_unchanged_state(blob_store, status):
    from azure.core.exceptions import AzureError

    store, _, _ = blob_store
    error = AzureError()
    error.status_code = status

    def properties():
        raise error

    store.blob.get_blob_properties = properties
    with pytest.raises(StateUnavailable, match="could not read"):
        store.read('"cached"')


def test_b3_blob_download_returns_its_own_version_after_a_metadata_read_race(blob_store):
    store, _, _ = blob_store
    state = {"current": None, "last_good": None, "frozen": False, "suspensions": []}
    old = store.write(state, None)
    store.write(state, old)
    properties = store.blob.get_blob_properties

    def update_after_head():
        result = properties()
        store.write({**state, "frozen": True}, result.etag)
        return result

    store.blob.get_blob_properties = update_after_head
    result, etag = store.read(old)
    assert result == {**state, "frozen": True} and etag == "3"


def test_b6_blob_version_check_observes_new_suspensions_for_every_member(blob_store, tmp_path):
    store, _, calls = blob_store
    a, b = service(store, tmp_path / "a"), service(store, tmp_path / "b")
    a.load_snapshot(snapshot())
    assert b.get_ranked(REQUEST).ranked[0].strategy_id == "S2"
    before = len(calls)
    assert b.get_ranked(REQUEST).ranked[0].strategy_id == "S2"
    assert calls[before:] == [("properties", "state.json", {})]
    a.suspend(EVENT)
    for jid in ("J-1", "J-2"):
        result = b.get_ranked({**REQUEST, "journey_id": jid})
        assert result.suspended == ["S2"]
        assert "S2" not in [item.strategy_id for item in result.ranked]


def test_b7_p2_reads_only_closed_aggregate_audit_projection(tmp_path):
    from governedgraph.common.config import DATA_DIR
    from governedgraph.learning.azure_inputs import BlobLearningInputs, WATERMARK
    from governedgraph.learning.ranking_pipeline import load_thresholds

    source = (DATA_DIR / "synthetic" / "outcome_aggregates_illustrative.csv").read_bytes()
    projection = {
        "audit_id": "audit-1", "threshold_version": load_thresholds()["threshold_version"],
        "outcome_digest": hashlib.sha256(source).hexdigest(), "vetoes": [[KEY, "S2"]],
        "created_at": "2026-10-22T00:00:00Z", "watermark": WATERMARK,
    }
    calls = []

    def blob(container, name):
        calls.append((container, name))
        assert container == "learning-audits", "P2 must never touch the cohort zone"
        return SimpleNamespace(download_blob=lambda: SimpleNamespace(
            readall=lambda: json.dumps(projection).encode()))

    inputs = BlobLearningInputs.__new__(BlobLearningInputs)
    inputs.outcome_bytes = lambda: source
    inputs.private = SimpleNamespace(get_blob_client=blob)
    evidence, audit = inputs.ranking_inputs("audit-1", load_thresholds())
    assert evidence and audit.vetoes == [(KEY, "S2", "protective-veto")] and not audit.findings
    assert calls == [("learning-audits", "audit-1.json")]
    projection["gender"] = "forbidden"
    with pytest.raises(ValueError, match="hand-off"):
        inputs.ranking_inputs("audit-1", load_thresholds())
    del projection["gender"]
    projection["outcome_digest"] = "different"
    with pytest.raises(ValueError, match="hand-off"):
        inputs.ranking_inputs("audit-1", load_thresholds())


def test_b7_outcomes_reject_any_extra_column(tmp_path):
    from governedgraph.learning.ranking_pipeline import load_outcome_aggregates

    path = tmp_path / "outcomes.csv"
    path.write_text("request_key,strategy_id,strategy_version,interactions,resolved_first_contact,"
                    "repeat_contact_14d,nationality\nkey,S1,1.0,1,1,0,forbidden\n")
    with pytest.raises(ValueError, match="B7"):
        load_outcome_aggregates(path)


def test_b8_entra_role_plan_is_read_only_and_has_no_service_identity_role(monkeypatch):
    from governedgraph.strategy_service import provision_auth

    calls = []

    def cli(*args):
        calls.append(args)
        return []

    monkeypatch.setattr(provision_auth, "az", cli)
    p2, fairness, runtime = [f"00000000-0000-0000-0000-{i:012d}" for i in range(1, 4)]
    assert provision_auth.configure("test", p2, fairness, runtime) is None
    assert calls == [("ad", "app", "list", "--display-name", "gg-strategy-test")]
    assignments = provision_auth.role_assignments(p2, fairness, runtime)
    assert len(assignments) == 5
    assert (runtime, provision_auth.ROLES["Strategy.Freeze"]) in assignments
    assert (runtime, provision_auth.ROLES["Strategy.Publish"]) not in assignments


def test_fresh_strategy_registration_is_seed_scoped_and_does_not_read_legacy_registration(monkeypatch):
    from governedgraph.strategy_service import provision_auth

    calls = []
    monkeypatch.setattr(provision_auth, "az", lambda *args: calls.append(args) or [])
    principals = [f"00000000-0000-0000-0000-{i:012d}" for i in range(1, 4)]
    seed = "a" * 32
    assert provision_auth.configure("gg-dev", *principals, naming_seed=seed) is None
    assert calls == [("ad", "app", "list", "--display-name", f"gg-strategy-gg-dev-{seed}")]


@pytest.fixture
def recovery_registration(monkeypatch):
    from governedgraph.strategy_service import provision_auth

    principals = [f"00000000-0000-0000-0000-{i:012d}" for i in range(1, 4)]
    operator = "00000000-0000-0000-0000-000000000099"
    monkeypatch.setenv("STRATEGY_RECOVERY_OPERATOR_ID", operator)
    api = {"requestedAccessTokenVersion": 2, "oauth2PermissionScopes": [{"id": "recovery-scope"}],
           "preAuthorizedApplications": [{"appId": "cli", "delegatedPermissionIds": ["recovery-scope"]}]}
    app = {"id": "application", "appId": "api",
           "appRoles": [{"id": role} for role in provision_auth.ROLES.values()], "api": copy.deepcopy(api)}
    grants = [{"principalId": principal, "appRoleId": role, "principalType": "ServicePrincipal"}
              for principal, role in provision_auth.role_assignments(*principals)]
    grants.append({"principalId": operator, "appRoleId": "00000000-0000-0000-0000-000000000000", "principalType": "User"})
    mutations = []

    def cli(*args):
        if args[:3] == ("ad", "app", "list"):
            return [copy.deepcopy(app)]
        if args[:3] == ("ad", "sp", "list"):
            return [{"id": "service"}]
        pytest.fail("unexpected CLI operation")

    def graph(method, path, body=None):
        if method == "GET":
            assert path == "servicePrincipals/service/appRoleAssignedTo"
            return {"value": copy.deepcopy(grants)}
        mutations.append((method, path, copy.deepcopy(body)))
        if path == "applications/application":
            app.update(copy.deepcopy(body))
        return {}

    monkeypatch.setattr(provision_auth, "az", cli)
    monkeypatch.setattr(provision_auth, "graph", graph)
    return provision_auth, principals, api, app, grants, mutations


def test_b8_redeploy_preserves_existing_operator_assignment_and_delegated_api(recovery_registration):
    module, principals, api, app, grants, mutations = recovery_registration
    assert module.configure("gg-dev", *principals, apply=True) == "api"
    assert app["api"] == api
    assert len(grants) == 6 and not any(method == "POST" for method, _, _ in mutations)


def test_b8_workload_registration_does_not_create_human_access(recovery_registration):
    module, principals, api, app, grants, mutations = recovery_registration
    grants.pop()
    assert module.configure("gg-dev", *principals, apply=True) == "api"
    assert app["api"] == api
    assert len(grants) == 5 and not any(method == "POST" for method, _, _ in mutations)


@pytest.mark.parametrize("change", [
    {"principalId": "00000000-0000-0000-0000-000000000098"},
    {"principalType": "Group"}, {"principalType": "ServicePrincipal"}, {"principalType": None},
    {"appRoleId": "00000000-0000-0000-0000-000000000098"},
])
def test_b8_redeploy_still_rejects_foreign_or_nonhuman_recovery_grants(recovery_registration, change):
    module, principals, _, _, grants, mutations = recovery_registration
    grants[-1].update(change)
    with pytest.raises(RuntimeError, match="unexpected API app-role grants"):
        module.configure("gg-dev", *principals, apply=True)
    assert not any(method == "POST" for method, _, _ in mutations)


def test_b8_redeploy_requires_an_explicit_dev_operator_to_preserve_access(recovery_registration, monkeypatch):
    module, principals, _, _, _, _ = recovery_registration
    monkeypatch.delenv("STRATEGY_RECOVERY_OPERATOR_ID")
    with pytest.raises(RuntimeError, match="unexpected API app-role grants"):
        module.configure("gg-dev", *principals, apply=True)


def test_b8_redeploy_does_not_accept_dev_recovery_access_in_other_environments(recovery_registration):
    module, principals, _, _, _, _ = recovery_registration
    with pytest.raises(RuntimeError, match="unexpected API app-role grants"):
        module.configure("other-env", *principals, apply=True)


def test_b8_storage_cleanup_targets_only_superseded_account_grants(monkeypatch):
    from governedgraph.strategy_service import revoke_legacy_storage

    role = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"
    rows = [
        {"id": "old-runtime", "scope": "/storage", "principalId": "runtime", "roleDefinitionId": "/roles/" + role},
        {"id": "old-p2", "scope": "/storage", "principalId": "p2", "roleDefinitionId": "/roles/" + role},
        {"id": "new-scoped", "scope": "/storage/container", "principalId": "runtime", "roleDefinitionId": "/roles/" + role},
        {"id": "other-user", "scope": "/storage", "principalId": "operator", "roleDefinitionId": "/roles/" + role},
    ]
    calls = []

    def cli(*args):
        calls.append(args)
        return rows if args[:3] == ("role", "assignment", "list") else None

    monkeypatch.setattr(revoke_legacy_storage, "az", cli)
    assert revoke_legacy_storage.revoke("/storage", "runtime", "p2") == ["old-runtime", "old-p2"]
    assert len(calls) == 1
    revoke_legacy_storage.revoke("/storage", "runtime", "p2", apply=True)
    assert calls[-2:] == [("role", "assignment", "delete", "--ids", "old-runtime"),
                          ("role", "assignment", "delete", "--ids", "old-p2")]


def test_b3_b7_infra_isolates_state_and_cohort_permissions():
    root = Path(__file__).resolve().parents[1]
    source = (root / "infra/modules/rbac.bicep").read_text()
    role = source.split("resource strategyVerifyRole ", 1)[1].split("\nresource ", 1)[0]
    assert "'Microsoft.KeyVault/vaults/keys/read'" in role
    assert "'Microsoft.KeyVault/vaults/keys/verify/action'" in role and "sign/action" not in role
    runtime = source.split("resource rtBlob ", 1)[1].split("\nresource ", 1)[0]
    assert "scope: recordsContainer" in runtime and "scope: st" not in runtime
    cohort = source.split("resource fairnessCohort ", 1)[1].split("\nresource ", 1)[0]
    assert "scope: cohortContainer" in cohort and "principalId: fairnessPrincipalId" in cohort
    assert "scope: cohortContainer" not in source.split("resource pipelineAuditRead ", 1)[1]
    learning = (root / "infra/modules/learning.bicep").read_text()
    assert "scope: artifactContainers[i]" in learning
    assert "scope: aggregates" in learning
    assert "fileServices" not in learning
