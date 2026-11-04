"""B8: fresh human decisions, durable replay rejection and explicit, token-free failures."""
import json
import os
from types import SimpleNamespace

import pytest
from azure.core.exceptions import AzureError, ResourceExistsError
from fastapi.testclient import TestClient

from scripts import lift_dev_freeze as command
from governedgraph.common.contracts import ContractError
from governedgraph.strategy_service.recovery import DecisionReferenceUsed, RecoveryRequest, release
from governedgraph.strategy_service.recovery_azure import BlobRecoveryJournal
from governedgraph.strategy_service.state import StateUnavailable
from test_strategy_recovery import AUDIT, HUMAN, Journal, authorization, frozen
from test_strategy_azure import MemoryState

NEW = "GB-DEV-2026-002"
OLD = "GB-DEV-2026-001"
ENDPOINT = "https://apim-gg-synthetic.azure-api.net/strategy-admin"
VALUES = {"AZURE_ENV_NAME": "gg-dev", "STRATEGY_TENANT_ID": "tenant",
          "STRATEGY_API_CLIENT_ID": "api", "STRATEGY_ADMIN_URL": ENDPOINT}
ACCOUNT = {"tenantId": "tenant", "user": {"type": "user"}}


@pytest.mark.parametrize("reference", [NEW, "GB-DEV-2027-123", "GB-DEV-0000-000"])
def test_b8_new_well_formed_decision_can_recover_once(tmp_path, reference):
    svc, _ = frozen(tmp_path)
    journal = Journal()
    request = RecoveryRequest.from_dict({"decision_ref": reference, "expected_audit_execution": AUDIT})
    assert release(svc, request, HUMAN, lambda: AUDIT, journal)["decision_ref"] == reference
    svc.freeze()
    before = svc.read_state()
    with pytest.raises(DecisionReferenceUsed, match=reference):
        release(svc, request, HUMAN, lambda: AUDIT, journal)
    assert svc.read_state() == before
    assert len(journal.records) == 2


@pytest.mark.parametrize("stage", ["requested", "completed"])
def test_b8_existing_legacy_records_consume_the_decision(tmp_path, stage):
    svc, _ = frozen(tmp_path)
    before = svc.read_state()
    journal = Journal()
    journal.write("recovery-old-random-id", stage, {"decision_ref": OLD})
    with pytest.raises(DecisionReferenceUsed, match="reuse check failed"):
        release(svc, RecoveryRequest(OLD, AUDIT), HUMAN, lambda: AUDIT, journal)
    assert svc.read_state() == before
    assert len(journal.records) == 1


@pytest.mark.parametrize("reference", [
    "GB-other", "GB-DEV-26-002", "GB-DEV-2026-02", "GB-DEV-2026-0002",
    "GB-PROD-2026-002", "GB-DEV-2026-002\n", "", None, 2,
])
def test_b8_malformed_reference_has_a_specific_error(reference):
    with pytest.raises(ContractError, match="decision-reference format check failed"):
        RecoveryRequest.from_dict({"decision_ref": reference, "expected_audit_execution": AUDIT})


@pytest.mark.parametrize("field,value,message", [
    ("AZURE_ENV_NAME", "production", "environment name check failed: detected 'production'"),
    ("AZURE_ENV_NAME", None, "environment name check failed: detected None"),
    ("STRATEGY_TENANT_ID", None, "tenant check failed: detected 'tenant'; required None"),
    ("STRATEGY_ADMIN_URL", "https://another.azure-api.net/strategy-admin", "endpoint check failed: detected"),
    ("STRATEGY_ADMIN_URL", ENDPOINT + "/other", "path='/strategy-admin/other'"),
    ("STRATEGY_ADMIN_URL", ENDPOINT.replace("https:", "http:"), "scheme='http'"),
    ("STRATEGY_ADMIN_URL", ENDPOINT + "?token=do-not-show", "query_present=True"),
    ("STRATEGY_ADMIN_URL", ENDPOINT + "#do-not-show", "fragment_present=True"),
    ("STRATEGY_ADMIN_URL", ENDPOINT.replace("https://", "https://user:do-not-show@"), "userinfo_present=True"),
    ("STRATEGY_ADMIN_URL", ENDPOINT.replace(".net/", ".net:8443/"), "port=8443"),
    ("STRATEGY_ADMIN_URL", "https://[", "malformed URL"),
])
def test_client_names_each_environment_tenant_and_endpoint_failure(field, value, message):
    with pytest.raises(ValueError) as failed:
        command._validate_context({**VALUES, field: value}, ACCOUNT, NEW)
    assert message in str(failed.value)
    assert "do-not-show" not in str(failed.value)


@pytest.mark.parametrize("kind", ["servicePrincipal", "managedIdentity", None])
def test_client_names_the_credential_type_failure(kind):
    account = {**ACCOUNT, "user": {"type": kind}}
    with pytest.raises(ValueError, match=f"credential type check failed: detected {kind!r}"):
        command._validate_context(VALUES, account, NEW)


def test_client_names_the_tenant_mismatch_and_reference_format():
    with pytest.raises(ValueError, match="tenant check failed: detected 'another'; required 'tenant'"):
        command._validate_context(VALUES, {**ACCOUNT, "tenantId": "another"}, NEW)
    with pytest.raises(ValueError, match="decision-reference format check failed"):
        command._validate_context(VALUES, ACCOUNT, "GB-other")
    assert command._validate_context(VALUES, ACCOUNT, NEW) == ENDPOINT


class Container:
    def __init__(self):
        self.records = {}

    def list_blobs(self, *, name_starts_with):
        return [SimpleNamespace(name=name) for name in self.records if name.startswith(name_starts_with)]

    def get_blob_client(self, name):
        def upload(raw, *, overwrite):
            assert overwrite is False
            if name in self.records:
                raise ResourceExistsError("already exists")
            self.records[name] = raw

        return SimpleNamespace(upload_blob=upload,
                               download_blob=lambda: SimpleNamespace(readall=lambda: self.records[name]))


def test_b8_blob_journal_checks_legacy_records_and_atomically_claims_new_refs():
    container = Container()
    container.records["human-recovery/recovery-legacy-completed.json"] = json.dumps({"decision_ref": OLD}).encode()
    journal = BlobRecoveryJournal(container)
    assert journal.used_decision_refs() == {OLD}
    journal.write("recovery-one", "requested", {"decision_ref": NEW})
    assert journal.used_decision_refs() == {OLD, NEW}
    with pytest.raises(DecisionReferenceUsed, match=NEW):
        BlobRecoveryJournal(container).write("recovery-two", "requested", {"decision_ref": NEW})
    assert len(container.records) == 2


def test_b8_atomic_claim_rejects_race_even_if_the_history_listing_is_stale(tmp_path, monkeypatch):
    svc, _ = frozen(tmp_path)
    container = Container()
    journal = BlobRecoveryJournal(container)
    journal.write("recovery-first", "requested", {"decision_ref": NEW})
    monkeypatch.setattr(container, "list_blobs", lambda **kwargs: [])
    before = svc.read_state()
    with pytest.raises(DecisionReferenceUsed):
        release(svc, RecoveryRequest(NEW, AUDIT), HUMAN, lambda: AUDIT, journal)
    assert svc.read_state() == before


@pytest.mark.parametrize("raw", [b"{", b'{"missing":"reference"}', b'{"decision_ref":"unapproved"}'])
def test_b8_corrupt_history_fails_closed(tmp_path, raw):
    svc, _ = frozen(tmp_path)
    container = Container()
    container.records["human-recovery/recovery-legacy-requested.json"] = raw
    with pytest.raises(StateUnavailable, match="history cannot be certified"):
        release(svc, RecoveryRequest(NEW, AUDIT), HUMAN, lambda: AUDIT, BlobRecoveryJournal(container))
    assert svc.frozen is True


def test_b8_unavailable_history_fails_closed(tmp_path, monkeypatch):
    svc, _ = frozen(tmp_path)
    container = Container()

    def unavailable(**kwargs):
        raise AzureError("simulated storage outage")

    monkeypatch.setattr(container, "list_blobs", unavailable)
    with pytest.raises(StateUnavailable, match="history cannot be certified"):
        release(svc, RecoveryRequest(NEW, AUDIT), HUMAN, lambda: AUDIT, BlobRecoveryJournal(container))
    assert svc.frozen is True


def test_b8_http_readiness_and_post_check_new_reused_and_malformed_refs(authorization, monkeypatch, tmp_path):
    from dataclasses import replace
    from governedgraph.strategy_service import api

    auth, token = authorization
    monkeypatch.setattr(auth, "settings", replace(auth.settings, environment_name="gg-dev",
                                                 strategy_recovery_operator_id=HUMAN))
    svc, store = frozen(tmp_path)
    journal = Journal()
    journal.write("legacy", "completed", {"decision_ref": OLD})
    store.container = object()
    monkeypatch.setattr(api, "BlobStateStore", MemoryState)
    monkeypatch.setattr(api, "BlobRecoveryJournal", lambda container: journal)
    monkeypatch.setattr(api, "svc", svc)
    monkeypatch.setattr(api, "audit_health", lambda: AUDIT)
    client = TestClient(api.app)
    headers = {"Authorization": token(HUMAN, [], idtyp="user", scp="Strategy.Recover",
                                      azp="04b07795-8ddb-461a-bbee-02f9e1bf7b46")}
    for reference, status in [(OLD, 409), ("GB-invalid", 400)]:
        assert client.get("/freeze-recovery", params={"decision_ref": reference}, headers=headers).status_code == status
        response = client.post("/freeze-recovery", headers=headers, json={
            "decision_ref": reference, "expected_audit_execution": AUDIT,
        })
        assert response.status_code == status
        assert reference in response.json()["detail"]
        assert svc.frozen is True
    ready = client.get("/freeze-recovery", params={"decision_ref": NEW}, headers=headers)
    assert ready.status_code == 200 and ready.json()["decision_ref"] == NEW
    assert svc.frozen is True and len(journal.records) == 1
    result = client.post("/freeze-recovery", headers=headers, json={
        "decision_ref": NEW, "expected_audit_execution": ready.json()["audit_execution"],
    })
    assert result.status_code == 200 and result.json()["frozen"] is False
    svc.freeze()
    assert client.post("/freeze-recovery", headers=headers, json={
        "decision_ref": NEW, "expected_audit_execution": AUDIT,
    }).status_code == 409
    assert svc.frozen is True


@pytest.fixture
def human_command(monkeypatch):
    import azure.identity

    events = []
    responses = {"ready": {"frozen": True, "decision_ref": NEW, "audit_execution": AUDIT},
                 "result": {"frozen": False, "decision_ref": NEW}, "ready_status": 200, "post_status": 200}
    monkeypatch.setattr(command.subprocess, "check_output", lambda args, **kwargs:
                        "\n".join(f"{key}={value}" for key, value in VALUES.items())
                        if args[0] == "azd" else json.dumps(ACCOUNT))

    class Credential:
        def __init__(self, *, tenant_id):
            assert tenant_id == "tenant"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get_token(self, scope):
            assert scope == "api://api/.default"
            return SimpleNamespace(token="synthetic-bearer-never-log")

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url):
            events.append(("GET", url))
            return SimpleNamespace(status_code=responses["ready_status"], json=lambda: responses["ready"])

        def post(self, url, json):
            events.append(("POST", url, json))
            return SimpleNamespace(status_code=responses["post_status"], json=lambda: responses["result"])

    monkeypatch.setattr(azure.identity, "AzureCliCredential", Credential)
    monkeypatch.setattr(command.httpx, "Client", Client)
    return events, responses


def test_client_accepts_new_reference_only_after_server_certifies_it(human_command, capsys):
    events, _ = human_command
    assert command.recover(NEW)["frozen"] is False
    assert events == [
        ("GET", ENDPOINT + "/freeze-recovery?decision_ref=" + NEW),
        ("POST", ENDPOINT + "/freeze-recovery",
         {"decision_ref": NEW, "expected_audit_execution": AUDIT}),
    ]
    assert "synthetic-bearer-never-log" not in str(capsys.readouterr())


@pytest.mark.parametrize("failures", [2, 6])
def test_b8_recovery_waits_boundedly_for_entra_assignment_propagation(
    human_command, monkeypatch, capsys, failures,
):
    import azure.identity
    from azure.core.exceptions import ClientAuthenticationError

    events, _ = human_command
    calls, waits = [], []
    original = azure.identity.AzureCliCredential.get_token

    def token(self, scope):
        calls.append(scope)
        if len(calls) <= failures:
            raise ClientAuthenticationError(message="AADSTS50105: synthetic assignment not active")
        return original(self, scope)

    monkeypatch.setattr(azure.identity.AzureCliCredential, "get_token", token)
    monkeypatch.setattr(command, "time", SimpleNamespace(sleep=waits.append), raising=False)
    if failures == 6:
        with pytest.raises(ClientAuthenticationError, match="AADSTS50105"):
            command.recover(NEW)
        assert events == []
    else:
        assert command.recover(NEW)["frozen"] is False
        assert [event[0] for event in events] == ["GET", "POST"]
    assert calls == ["api://api/.default"] * (min(failures, 5) + 1)
    assert waits == [60] * min(failures, 5)
    output = capsys.readouterr()
    assert "retrying" in output.err
    assert "synthetic-bearer-never-log" not in output.out + output.err


@pytest.mark.parametrize("code", ["AADSTS501051", "AADSTS65001", "AADSTS50076"])
def test_b8_recovery_does_not_retry_other_authentication_failures(human_command, monkeypatch, code):
    import azure.identity
    from azure.core.exceptions import ClientAuthenticationError

    events, _ = human_command
    calls = []

    def token(self, scope):
        calls.append(scope)
        raise ClientAuthenticationError(message=code + ": synthetic authentication failure")

    monkeypatch.setattr(azure.identity.AzureCliCredential, "get_token", token)
    monkeypatch.setattr(command, "time", SimpleNamespace(sleep=lambda _: pytest.fail("must not retry")), raising=False)
    with pytest.raises(ClientAuthenticationError, match=code):
        command.recover(NEW)
    assert calls == ["api://api/.default"] and events == []


def test_recovery_pins_gg_dev_despite_stale_shell_environment(human_command, monkeypatch):
    events, _ = human_command
    monkeypatch.setenv("AZURE_ENV_NAME", "other-dev")
    original = command.subprocess.check_output
    calls = []

    def output(args, **kwargs):
        if args[0] == "azd":
            calls.append((args, kwargs))
            selected = (args == ["azd", "env", "get-values", "--environment", "gg-dev"]
                        and kwargs.get("env", {}).get("AZURE_ENV_NAME") == "gg-dev")
            values = VALUES if selected else {**VALUES, "AZURE_ENV_NAME": "other-dev"}
            return "\n".join(f"{key}={value}" for key, value in values.items())
        return original(args, **kwargs)

    monkeypatch.setattr(command.subprocess, "check_output", output)
    assert command.recover(NEW)["frozen"] is False
    assert len(calls) == 1 and len(events) == 2
    assert os.environ["AZURE_ENV_NAME"] == "other-dev"


def test_recovery_still_rejects_mismatched_environment_metadata(human_command, monkeypatch):
    events, _ = human_command
    original = command.subprocess.check_output

    def output(args, **kwargs):
        if args[0] == "azd":
            return "\n".join(f"{key}={value}" for key, value in {**VALUES, "AZURE_ENV_NAME": "other-dev"}.items())
        return original(args, **kwargs)

    monkeypatch.setattr(command.subprocess, "check_output", output)
    with pytest.raises(ValueError, match="environment name check failed"):
        command.recover(NEW)
    assert events == []


def test_recovery_rejects_literal_reference_placeholder_before_external_calls(human_command, monkeypatch):
    events, _ = human_command
    monkeypatch.setattr(command.subprocess, "check_output",
                        lambda *args, **kwargs: pytest.fail("invalid reference must be rejected before external calls"))
    with pytest.raises(ContractError, match="decision-reference format check failed"):
        command.recover("GB-DEV-2026-NNN")
    assert events == []


@pytest.mark.parametrize("stage", ["readiness", "post"])
def test_client_reports_reuse_without_exposing_a_token(human_command, stage):
    events, responses = human_command
    key = "ready" if stage == "readiness" else "result"
    responses["ready_status" if stage == "readiness" else "post_status"] = 409
    responses[key] = {"detail": f"decision-reference reuse check failed: detected {NEW!r}; already recorded"}
    with pytest.raises(ValueError, match=f"reuse check failed: detected {NEW!r}") as error:
        command.recover(NEW)
    assert "synthetic-bearer-never-log" not in str(error.value)
    assert len(events) == (1 if stage == "readiness" else 2)


@pytest.mark.parametrize("field,value,message", [
    ("frozen", False, "frozen state check failed: detected False"),
    ("decision_ref", OLD, "server decision-reference check failed"),
    ("audit_execution", None, "audit execution check failed: detected None"),
])
def test_client_does_not_post_on_bad_readiness(human_command, field, value, message):
    events, responses = human_command
    responses["ready"][field] = value
    with pytest.raises((ValueError, RuntimeError), match=message):
        command.recover(NEW)
    assert len(events) == 1


def test_client_does_not_post_when_latest_audit_is_unavailable(human_command):
    events, responses = human_command
    responses["ready_status"] = 503
    with pytest.raises(RuntimeError, match="readiness check failed: detected HTTP 503"):
        command.recover(NEW)
    assert len(events) == 1
