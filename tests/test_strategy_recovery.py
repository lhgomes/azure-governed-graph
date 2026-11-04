"""B6/B8: only a human can recover after a healthy isolated audit; protection wins races."""
import copy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from governedgraph.common.contracts import ContractError
from governedgraph.strategy_service.recovery import REASON, RecoveryRequest, latest_successful_audit, release
from governedgraph.strategy_service.state import StateConflict, StateUnavailable
from test_strategy_azure import EVENT, authorization, service, snapshot, MemoryState

IMAGE = "registry/p2@sha256:" + "a" * 64
AUDIT = "job-gg-p2-fairness-healthy"
HUMAN = "00000000-0000-4000-8000-0000000000a1"
DECISION_REF = "GB-DEV-2026-001"


def row(name=AUDIT, status="Succeeded", start="2026-10-24T12:00:00Z"):
    return {"name": name, "properties": {"status": status, "startTime": start, "template": {"containers": [{
        "image": IMAGE, "command": ["python", "-m", "governedgraph.learning.container_jobs", "--stage", "audit"],
        "env": [{"name": key, "value": value} for key, value in {
            "GOVERNEDGRAPH_P2_STAGE": "audit", "GOVERNEDGRAPH_P2_ROLE": "fairness",
            "GOVERNEDGRAPH_P2_IMAGE": IMAGE, "GOVERNEDGRAPH_P2_CYCLE_ID": "exercise-healthy",
        }.items()],
    }]}}}


class Journal:
    def __init__(self, fail=None):
        self.records = []
        self.fail = fail

    def used_decision_refs(self):
        return {record["decision_ref"] for _, _, record in self.records}

    def write(self, identifier, stage, record):
        if stage == self.fail:
            raise StateUnavailable("simulated journal outage")
        self.records.append((identifier, stage, copy.deepcopy(record)))


def frozen(tmp_path):
    store = MemoryState()
    svc = service(store, tmp_path)
    svc.load_snapshot(snapshot())
    svc.suspend(EVENT)
    svc.freeze()
    return svc, store


def test_b8_human_delegated_recovery_only_and_reinstatement_remains_disabled(authorization, monkeypatch):
    auth, token = authorization
    monkeypatch.setattr(auth, "settings", replace(auth.settings, environment_name="gg-dev",
                                                 strategy_recovery_operator_id=HUMAN))
    claims = {"idtyp": "user", "scp": "Strategy.Recover", "azp": "04b07795-8ddb-461a-bbee-02f9e1bf7b46"}
    assert auth.authorize("recover", token(HUMAN, [], **claims))["oid"] == HUMAN
    for oid, override in [
        (HUMAN, {"idtyp": "app"}), (HUMAN, {"scp": ""}), (HUMAN, {"azp": "another-client"}),
        ("p2", {}), ("fairness", {}), ("runtime", {}), ("another-human", {}),
    ]:
        with pytest.raises(HTTPException) as denied:
            auth.authorize("recover", token(oid, ["Strategy.Freeze", "Strategy.Publish"], **(claims | override)))
        assert denied.value.status_code == 403
    with pytest.raises(HTTPException):
        auth.authorize("reinstate", token(HUMAN, [], **claims))
    monkeypatch.setattr(auth, "settings", replace(auth.settings, environment_name="production"))
    with pytest.raises(HTTPException):
        auth.authorize("recover", token(HUMAN, [], **claims))


@pytest.mark.parametrize("status", ["Failed", "Running", "Stopped", "Pending", "Unknown"])
def test_b8_latest_audit_not_old_success_controls_recovery(status):
    with pytest.raises(StateUnavailable):
        latest_successful_audit([row(), row("job-gg-p2-fairness-latest", status, "2026-10-24T13:00:00Z")], IMAGE)


def test_b8_latest_successful_audit_requires_exact_isolated_image_and_command():
    assert latest_successful_audit([row()], IMAGE) == AUDIT
    for change in ("image", "command", "role"):
        bad = row()
        container = bad["properties"]["template"]["containers"][0]
        if change == "role":
            container["env"][1]["value"] = "pipeline"
        else:
            container[change] = "unapproved"
        with pytest.raises(StateUnavailable):
            latest_successful_audit([bad], IMAGE)
    for rows in ([], [row(), row()], [row(start="2026-10-24T12:00:00")]):
        with pytest.raises(StateUnavailable):
            latest_successful_audit(rows, IMAGE)


def test_b8_native_recovery_monitor_pin_must_match_the_approved_audit_image():
    audit = row()
    container = audit["properties"]["template"]["containers"][0]
    container["command"] = ["python", "-m", "governedgraph.learning.scheduled_jobs"]
    container["env"].append({"name": "GOVERNEDGRAPH_P2_CADENCE_SECONDS", "value": "14400"})
    stale_monitor_image = "registry/p2@sha256:" + "b" * 64
    with pytest.raises(StateUnavailable, match="approved audit code"):
        latest_successful_audit([audit], stale_monitor_image)
    assert latest_successful_audit([audit], IMAGE) == AUDIT
    for item in container["env"]:
        if item["name"] == "GOVERNEDGRAPH_P2_IMAGE":
            item["value"] = stale_monitor_image
    with pytest.raises(StateUnavailable, match="approved audit code"):
        latest_successful_audit([audit], IMAGE)


@pytest.mark.parametrize("extra", [{"actor_id": HUMAN}, {"age_band": "older"}, {"reason": "override"}])
def test_b7_recovery_contract_is_closed(extra):
    with pytest.raises(ContractError):
        RecoveryRequest.from_dict({"decision_ref": DECISION_REF, "expected_audit_execution": AUDIT, **extra})
    with pytest.raises(ContractError):
        RecoveryRequest.from_dict({"decision_ref": "GB-other", "expected_audit_execution": AUDIT})


def test_b8_recovery_journals_actor_time_decision_and_preserves_rankings_and_veto(tmp_path):
    svc, store = frozen(tmp_path)
    before = copy.deepcopy(store.state)
    journal = Journal()
    result = release(svc, RecoveryRequest(DECISION_REF, AUDIT), HUMAN, lambda: AUDIT, journal)
    assert result["frozen"] is False
    assert store.state == {**before, "frozen": False}
    assert [stage for _, stage, _ in journal.records] == ["requested", "completed"]
    for _, _, record in journal.records:
        assert record["actor_id"] == HUMAN and record["decision_ref"] == DECISION_REF
        assert record["reason"] == REASON and record["requested_at"] and record["audit_execution"] == AUDIT
    assert journal.records[-1][2]["completed_at"]


@pytest.mark.parametrize("stage", ["requested", "completed"])
def test_b8_recovery_journal_failure_keeps_or_restores_freeze(tmp_path, stage):
    svc, store = frozen(tmp_path)
    with pytest.raises(StateUnavailable):
        release(svc, RecoveryRequest(DECISION_REF, AUDIT), HUMAN, lambda: AUDIT, Journal(fail=stage))
    assert store.state["frozen"] is True


def test_b8_newer_failed_audit_refuses_recovery_and_healthy_race_refreezes(tmp_path):
    for failure_call in (1, 2, 3):
        svc, store = frozen(tmp_path / str(failure_call))
        calls = []

        def health():
            calls.append(True)
            if len(calls) == failure_call:
                raise StateUnavailable("latest audit failed")
            return AUDIT

        with pytest.raises(StateUnavailable):
            release(svc, RecoveryRequest(DECISION_REF, AUDIT), HUMAN, health, Journal())
        assert store.state["frozen"] is True


def test_b8_concurrent_protective_freeze_wins_without_retrying_human_release(tmp_path):
    svc, store = frozen(tmp_path)
    other = service(store, tmp_path / "other")
    store.conflict = other.freeze
    with pytest.raises(StateConflict):
        release(svc, RecoveryRequest(DECISION_REF, AUDIT), HUMAN, lambda: AUDIT, Journal())
    assert store.state["frozen"] is True


def test_b8_ambiguous_state_write_that_committed_still_refreezes(tmp_path, monkeypatch):
    svc, store = frozen(tmp_path)
    write = store.write

    def ambiguous_once(state, etag):
        result = write(state, etag)
        if state["frozen"] is False:
            raise StateUnavailable("state committed but acknowledgment was lost")
        return result

    monkeypatch.setattr(store, "write", ambiguous_once)
    with pytest.raises(StateUnavailable):
        release(svc, RecoveryRequest(DECISION_REF, AUDIT), HUMAN, lambda: AUDIT, Journal())
    assert store.state["frozen"] is True


def test_b8_blob_recovery_journal_is_create_only_and_requires_exact_readback():
    from governedgraph.strategy_service.recovery_azure import BlobRecoveryJournal

    writes = []
    blob = SimpleNamespace(upload_blob=lambda raw, **kwargs: writes.append((raw, kwargs)),
                           download_blob=lambda: SimpleNamespace(readall=lambda: writes[-1][0]))
    journal = BlobRecoveryJournal(SimpleNamespace(get_blob_client=lambda name: blob))
    journal.write("recovery-test", "requested", {"decision_ref": DECISION_REF})
    assert writes[0][1] == {"overwrite": False}
    blob.download_blob = lambda: SimpleNamespace(readall=lambda: b"changed")
    with pytest.raises(StateUnavailable):
        journal.write("recovery-test", "completed", {"decision_ref": DECISION_REF})


def test_b8_recovery_http_routes_reject_workloads_and_use_server_audit_health(authorization, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from governedgraph.strategy_service import api

    auth, token = authorization
    monkeypatch.setattr(auth, "settings", replace(auth.settings, environment_name="gg-dev",
                                                 strategy_recovery_operator_id=HUMAN))
    svc, _ = frozen(tmp_path)
    monkeypatch.setattr(api, "svc", svc)
    monkeypatch.setattr(api, "audit_health", lambda: AUDIT)
    client = TestClient(api.app)
    for identity in ("p2", "fairness", "runtime"):
        headers = {"Authorization": token(identity, ["Strategy.Publish", "Strategy.Freeze"])}
        assert client.get("/freeze-recovery", headers=headers).status_code == 403
        assert client.post("/freeze-recovery", headers=headers, json={}).status_code == 403
    human = {"Authorization": token(HUMAN, [], idtyp="user", scp="Strategy.Recover",
                                    azp="04b07795-8ddb-461a-bbee-02f9e1bf7b46")}
    assert client.get("/freeze-recovery", headers=human).json()["audit_execution"] == AUDIT
    assert client.post("/freeze-recovery", headers=human, json={
        "decision_ref": DECISION_REF, "expected_audit_execution": AUDIT, "actor_id": "spoof",
    }).status_code == 400
    assert svc.read_state()["frozen"] is True


@pytest.mark.parametrize("user_type,status,expected_posts", [
    ("user", 200, 1), ("user", 503, 0), ("servicePrincipal", 200, 0),
])
def test_b8_human_command_uses_cli_and_never_posts_without_server_readiness(
    monkeypatch, user_type, status, expected_posts,
):
    import json
    import azure.identity
    from scripts import lift_dev_freeze

    values = (
        'AZURE_ENV_NAME="gg-dev"\nSTRATEGY_TENANT_ID="tenant"\nSTRATEGY_API_CLIENT_ID="api"\n'
        'STRATEGY_ADMIN_URL="https://apim-gg-synthetic.azure-api.net/strategy-admin"\n'
    )
    monkeypatch.setattr(lift_dev_freeze.subprocess, "check_output", lambda command, **kwargs:
                        values if command[0] == "azd" else json.dumps({
                            "tenantId": "tenant", "user": {"type": user_type},
                        }))
    posts = []

    class Credential:
        def __init__(self, **kwargs):
            assert kwargs == {"tenant_id": "tenant"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get_token(self, scope):
            assert scope == "api://api/.default"
            # Fake test fixture, assembled at runtime; never an Azure credential.
            return SimpleNamespace(token="-".join(['synthetic', 'non', 'secret', 'test', 'bearer']))

    class Client(Credential):
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
            assert kwargs["headers"] == {"Authorization": "Bearer synthetic-non-secret-test-bearer"}

        def get(self, endpoint):
            return SimpleNamespace(status_code=status, json=lambda: {
                "frozen": True, "decision_ref": DECISION_REF, "audit_execution": AUDIT,
            })

        def post(self, endpoint, json):
            posts.append(json)
            return SimpleNamespace(status_code=200, json=lambda: {
                "frozen": False, "decision_ref": DECISION_REF, "recovery_id": "recovery-test",
            })

    monkeypatch.setattr(azure.identity, "AzureCliCredential", Credential)
    monkeypatch.setattr(lift_dev_freeze.httpx, "Client", Client)
    if user_type != "user" or status != 200:
        with pytest.raises((ValueError, RuntimeError)):
            lift_dev_freeze.recover(DECISION_REF)
    else:
        assert lift_dev_freeze.recover(DECISION_REF)["frozen"] is False
    assert len(posts) == expected_posts
    if posts:
        assert posts == [{"decision_ref": DECISION_REF, "expected_audit_execution": AUDIT}]


def test_b8_recovery_registration_persists_scope_before_preauthorization_without_new_role(monkeypatch):
    import importlib
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    configure = importlib.import_module("configure_strategy_recovery")
    app_id = "00000000-0000-4000-8000-0000000000b2"
    environ = {"AZURE_ENV_NAME": "gg-dev", "STRATEGY_API_CLIENT_ID": app_id, "STRATEGY_RECOVERY_OPERATOR_ID": HUMAN}
    app = {"id": "application", "signInAudience": "AzureADMyOrg",
           "api": {"oauth2PermissionScopes": [], "preAuthorizedApplications": [], "requestedAccessTokenVersion": 2}}
    principal = {"id": "principal", "appRoleAssignmentRequired": True}
    assignments, mutations = [], []

    def graph(method, path, body=None):
        if method == "GET":
            if path.startswith("applications?"):
                return {"value": [copy.deepcopy(app)]}
            if path == "applications/application":
                return copy.deepcopy(app)
            if path.startswith("servicePrincipals?"):
                return {"value": [principal]}
            return {"value": copy.deepcopy(assignments)}
        mutations.append((method, path, copy.deepcopy(body)))
        if method == "PATCH":
            assert set(body) == {"api"}
            if body["api"].get("preAuthorizedApplications"):
                assert app["api"]["oauth2PermissionScopes"]
            app["api"] = copy.deepcopy(body["api"])
        else:
            assignments.append(body)
        return {}

    monkeypatch.setattr(configure, "graph", graph)
    for _ in range(2):
        result = configure.configure(app_id, HUMAN, apply=True, environ=environ)
        assert result["new_application_roles"] is False and result["credentials"] == "none"
    assert principal["appRoleAssignmentRequired"] is True
    assert [body for method, _, body in mutations if method == "POST"] == [{
        "principalId": HUMAN, "resourceId": "principal", "appRoleId": configure.DEFAULT_ROLE,
    }]
    assert len(app["api"]["oauth2PermissionScopes"]) == 1
    assert app["api"]["preAuthorizedApplications"] == [{
        "appId": configure.CLI_ID, "delegatedPermissionIds": [result["scope_id"]],
    }]


@pytest.mark.parametrize("environ,api,human", [
    ({}, "00000000-0000-4000-8000-0000000000b2", HUMAN),
    ({"AZURE_ENV_NAME": "production", "STRATEGY_API_CLIENT_ID": "00000000-0000-4000-8000-0000000000b2",
      "STRATEGY_RECOVERY_OPERATOR_ID": HUMAN}, "00000000-0000-4000-8000-0000000000b2", HUMAN),
    ({"AZURE_ENV_NAME": "gg-dev", "STRATEGY_API_CLIENT_ID": "00000000-0000-4000-8000-0000000000b2",
      "STRATEGY_RECOVERY_OPERATOR_ID": HUMAN}, "00000000-0000-4000-8000-0000000000b3", HUMAN),
    ({"AZURE_ENV_NAME": "gg-dev", "STRATEGY_API_CLIENT_ID": "00000000-0000-4000-8000-0000000000b2",
      "STRATEGY_RECOVERY_OPERATOR_ID": HUMAN}, "00000000-0000-4000-8000-0000000000b2",
     "00000000-0000-4000-8000-0000000000a2"),
    ({"AZURE_ENV_NAME": "gg-dev", "STRATEGY_API_CLIENT_ID": "00000000-0000-4000-8000-0000000000b2"},
     "00000000-0000-4000-8000-0000000000b2", HUMAN),
])
def test_b8_recovery_registration_is_restricted_to_the_configured_api_operator_and_dev_environment(
        monkeypatch, environ, api, human):
    import importlib
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    configure = importlib.import_module("configure_strategy_recovery")
    monkeypatch.setattr(configure, "graph", lambda *args, **kwargs: pytest.fail("no directory call may be made"))
    with pytest.raises(ValueError, match="gg-dev environment|restricted to the deployment"):
        configure.configure(api, human, apply=True, environ=environ)
