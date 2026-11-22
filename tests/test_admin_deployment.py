"""B6/B8: the console's identity is protective-only; infra grants stay container-scoped and cohort-free."""
import re
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from governedgraph.admin.traces import TraceUnavailable, monitor_runner
from governedgraph.strategy_service import auth, provision_auth

ROOT = Path(__file__).resolve().parents[1]
TENANT = "00000000-0000-0000-0000-000000000001"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(auth, "settings", replace(
        auth.settings, strategy_tenant_id=TENANT, strategy_api_client_id="strategy-api",
        strategy_publisher_id="p2", strategy_fairness_id="fairness", strategy_runtime_id="runtime",
        strategy_admin_id="admin", strategy_governance_group="", strategy_recovery_operator_id=""))
    monkeypatch.setattr(auth, "signing_keys", lambda _: SimpleNamespace(
        get_signing_key_from_jwt=lambda value: SimpleNamespace(key=KEY.public_key())))

    def make(oid, roles, **overrides):
        claims = {"iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "aud": "strategy-api", "tid": TENANT,
                  "oid": oid, "iat": int(time.time()), "exp": int(time.time()) + 300, "idtyp": "app", "roles": roles}
        claims.update(overrides)
        return "Bearer " + jwt.encode(claims, KEY, algorithm="RS256")

    return make


@pytest.mark.parametrize(("action", "role"), [("suspend", "Strategy.Suspend"), ("freeze", "Strategy.Freeze")])
def test_b6_b8_admin_identity_can_apply_only_the_protective_actions_it_holds_a_role_for(token, action, role):
    auth.authorize(action, token("admin", [role]))
    with pytest.raises(HTTPException) as error:
        auth.authorize(action, token("admin", []))
    assert error.value.status_code == 403


@pytest.mark.parametrize(("action", "role"), [("publish", "Strategy.Publish"), ("publish", "Strategy.Freeze"),
                                              ("suspend", "Strategy.Freeze"), ("freeze", "Strategy.Publish")])
def test_b3_b8_admin_identity_can_never_publish_snapshots_or_borrow_another_role(token, action, role):
    with pytest.raises(HTTPException) as error:
        auth.authorize(action, token("admin", [role]))
    assert error.value.status_code == 403


def test_b8_admin_identity_cannot_reinstate_or_recover_and_an_unset_admin_grants_nothing(token, monkeypatch):
    for action in ("reinstate", "recover"):
        with pytest.raises(HTTPException) as error:
            auth.authorize(action, token("admin", ["Strategy.Suspend", "Strategy.Freeze"]))
        assert error.value.status_code == 403
    monkeypatch.setattr(auth, "settings", replace(auth.settings, strategy_admin_id=""))
    with pytest.raises(HTTPException):
        auth.authorize("freeze", token("admin", ["Strategy.Freeze"]))
    with pytest.raises(HTTPException):
        auth.authorize("freeze", token("", ["Strategy.Freeze"]))


def test_b8_entra_plan_gives_the_console_freeze_and_suspend_only_and_requires_distinct_identities():
    p2, fairness, runtime, admin = [f"00000000-0000-0000-0000-{i:012d}" for i in range(1, 5)]
    assert provision_auth.role_assignments(p2, fairness, runtime) == provision_auth.role_assignments(p2, fairness, runtime, "")
    extra = provision_auth.role_assignments(p2, fairness, runtime, admin) - provision_auth.role_assignments(p2, fairness, runtime)
    assert extra == {(admin, provision_auth.ROLES["Strategy.Suspend"]), (admin, provision_auth.ROLES["Strategy.Freeze"])}
    for reused in (p2, fairness, runtime):
        with pytest.raises(ValueError, match="distinct"):
            provision_auth.role_assignments(p2, fairness, runtime, reused)
    with pytest.raises(ValueError):
        provision_auth.role_assignments(p2, fairness, runtime, "not-a-guid")


class Columns:
    columns = ("timestamp", "journey_id")
    rows = ((datetime(2026, 11, 5), "J-1"),)


def fake_monitor(monkeypatch, result, calls):
    module = ModuleType("azure.monitor.query")

    class Client:
        def __init__(self, credential):
            calls.append("client")

        def query_workspace(self, workspace, query, *, timespan, server_timeout):
            calls.append((workspace, timespan.days, server_timeout))
            if isinstance(result, Exception):
                raise result
            return result

    module.LogsQueryClient, module.LogsQueryStatus = Client, SimpleNamespace(SUCCESS="success")
    monkeypatch.setitem(sys.modules, "azure.monitor.query", module)
    monkeypatch.setattr("azure.identity.DefaultAzureCredential", lambda: object())


def test_trace_lookup_on_azure_uses_the_managed_identity_with_a_bounded_window(monkeypatch):
    calls = []
    fake_monitor(monkeypatch, SimpleNamespace(status="success", tables=[Columns]), calls)
    run = monitor_runner("00000000-0000-0000-0000-0000000000aa")
    assert run("query") == [{"timestamp": "2026-11-05T00:00:00", "journey_id": "J-1"}]
    run("query")
    assert calls == ["client", ("00000000-0000-0000-0000-0000000000aa", 7, 30), ("00000000-0000-0000-0000-0000000000aa", 7, 30)]


@pytest.mark.parametrize("result,message", [(RuntimeError("PRIVATE denied"), "rejected trace lookup"),
                                            (SimpleNamespace(status="partial", tables=[]), "partial")])
def test_trace_lookup_failures_explain_the_access_problem_without_internal_detail(monkeypatch, result, message):
    fake_monitor(monkeypatch, result, [])
    with pytest.raises(TraceUnavailable, match=message) as error:
        monitor_runner("00000000-0000-0000-0000-0000000000aa")("query")
    assert "PRIVATE" not in str(error.value)


def block(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def test_b7_b8_console_grants_are_container_scoped_and_exclude_cohorts_content_and_signing():
    rbac = (ROOT / "infra/modules/rbac.bicep").read_text()
    admin = block(rbac, "// --- administration console", "// --- developer")
    scopes = re.findall(r"scope: (\w+)", admin)
    assert sorted(scopes) == ["adminActionsContainer", "approvalsContainer", "improvementContainer", "learningEvidence",
                              "outcomeContainer", "recordsContainer"]
    for forbidden in ("cohortContainer", "auditContainer", "rankingContainer", "contentContainer", "journey", "snapshotKey",
                      "scope: st\n", "scope: contentStorage", "scope: kv", "keySignerRole", "verifyRole", "scope: search"):
        assert forbidden not in admin
    roles = re.findall(r"roleDefinitionId: subscriptionResourceId\('Microsoft.Authorization/roleDefinitions', (roles\.\w+)\)", admin)
    assert roles.count("roles.blobDataContributor") == 2 and roles.count("roles.blobDataReader") == 4
    contributor = block(admin, "resource adminJournalWrite", "\n}\n")
    assert "adminActionsContainer" in contributor
    assert "roles.blobDataReader" in block(admin, "resource adminProposalRead", "\n}\n")
    assert "approvalsContainer" in block(admin, "resource adminApprovalWrite", "\n}\n")
    assert "principalId: adminPrincipalId" in admin and "principalId: runtimePrincipalId" not in admin


def test_b8_console_journal_is_an_immutable_container_and_the_identity_is_its_own():
    data = (ROOT / "infra/modules/data.bicep").read_text()
    assert "name: 'admin-actions'" in data and "adminActionsWorm" in data
    assert "allowProtectedAppendWrites: false" in block(data, "resource adminActionsWorm", "\n}\n")
    security = (ROOT / "infra/modules/security.bicep").read_text()
    assert "name: 'id-gg-admin-${token}'" in security
    compute = (ROOT / "infra/modules/compute.bicep").read_text()
    app = block(compute, "resource admin 'Microsoft.App/containerApps", "\nresource contact ")
    assert "userAssignedIdentities: { '${adminIdentityId}': {} }" in app
    for forbidden in ("runtimeIdentityId", "strategyIdentityId", "serviceEnv", "MEMBER_TOKEN", "SERVICE_BUS", "KEY_VAULT",
                      "AZURE_AI_", "allowInsecure: true"):
        assert forbidden not in app
    assert "minReplicas: 1, maxReplicas: 1" in app and "allowInsecure: false" in app
    assert "path: '/healthz'" in app


def test_b3_b8_gateway_lists_every_console_route_and_exposes_no_wildcard_or_publish_path():
    gateway = (ROOT / "infra/modules/gateway.bicep").read_text()
    ops = block(gateway, "var adminOperations", "resource adminOps")
    routes = re.findall(r"method: '(\w+)', path: '([^']+)'", ops)
    assert sorted(routes) == sorted([
        ("GET", "/"), ("GET", "/assets/{name}"), ("GET", "/api/access"), ("GET", "/api/overview"), ("GET", "/api/journeys"),
        ("GET", "/api/journeys/{journeyId}"), ("GET", "/api/traces/{journeyId}"), ("POST", "/api/actions"),
        ("POST", "/api/owner-actions"), ("POST", "/api/catalogue"), ("GET", "/api/proposals"),
        ("GET", "/api/proposals/{proposalId}"), ("POST", "/api/proposals/decision")])
    assert "/*" not in ops and "path: 'admin'" in gateway
    main = (ROOT / "infra/workloads.bicep").read_text()
    assert "STRATEGY_ADMIN_PRINCIPAL_ID: security.outputs.adminPrincipalId" in main


def test_b8_risky_console_options_are_off_by_default_and_reviewed_access_is_explicit():
    main = (ROOT / "infra/workloads.bicep").read_text()
    for parameter in ("adminAllowOpenActions", "adminTraceLookup"):
        assert re.search(rf"param {parameter} bool = false", main)
    assert re.search(r"module adminTraceRbac .* = if \(deployAdminConsole && adminTraceLookup\)", main)
    trace = (ROOT / "infra/modules/admin-trace-rbac.bicep").read_text()
    assert "scope: workspace" in trace and "73c42c96-874c-492b-b04d-ab87d138a893" in trace
    bootstrap = (ROOT / "scripts/bootstrap.py").read_text()
    driver = (ROOT / "scripts/deploy.py").read_text()
    assert '"adminAllowOpenActions"' not in driver
    assert '"adminTraceLookup": inputs.voice_demo' in driver
    assert '"adminReaderGroupId": console["reader_group_id"]' in bootstrap
    assert '"adminOperatorGroupId": console["operator_group_id"]' in bootstrap
    assert "azure-monitor-query==2.0.0" in (ROOT / "requirements-azure.txt").read_text()


def test_b3_b4_b8_gap_review_has_its_own_identity_reads_records_and_cannot_write_an_approval():
    rbac = (ROOT / "infra/modules/rbac.bicep").read_text()
    review = block(rbac, "// --- gap review, improvement proposals", "// --- administration console")
    grants = re.findall(r"scope: ([\w\[\]]+)\n\s+name: guid\([^,]+, (\w+), (roles\.\w+)\)", review)
    assert sorted(grants) == sorted([
        ("recordsContainer", "gapReviewPrincipalId", "roles.blobDataReader"),
        ("journeyContainers[0]", "gapReviewPrincipalId", "roles.blobDataReader"),
        ("improvementContainer", "gapReviewPrincipalId", "roles.blobDataContributor"),
        ("approvalsContainer", "gapReviewPrincipalId", "roles.blobDataReader"),
        ("declinedQuestionsContainer", "gapReviewPrincipalId", "roles.blobDataReader"),
        ("approvalsContainer", "contentPublisherPrincipalId", "roles.blobDataContributor"),
        ("adminActionsContainer", "contentPublisherPrincipalId", "roles.blobDataReader")])
    for forbidden in ("snapshotKey", "keySignerRole", "scope: search", "scope: kv", "cohortContainer", "contentContainer",
                      "pipelinePrincipalId", "adminPrincipalId", "runtimePrincipalId"):
        assert forbidden not in review
    security = (ROOT / "infra/modules/security.bicep").read_text()
    assert "name: 'id-gg-gap-review-${token}'" in security
    job = (ROOT / "infra/modules/gap-review-job.bicep").read_text()
    assert "governedgraph.learning.gap_review_job" in job and "triggerType: 'Schedule'" in job
    for forbidden in ("KEY_VAULT", "SEARCH", "SERVICE_BUS", "AZURE_AI_", "Strategy.", "keyVault"):
        assert forbidden not in job
    data = (ROOT / "infra/modules/data.bicep").read_text()
    for name, resource in (("improvement-proposals", "improvementProposals"), ("content-approvals", "contentApprovals"),
                           ("declined-questions", "declinedQuestions")):
        assert f"name: '{name}'" in data and "publicAccess: 'None'" in block(data, f"resource {resource} ", "\n}\n")
    assert "allowProtectedAppendWrites: false" in block(data, "resource contentApprovalsWorm", "\n}\n")
    # Member text must stay erasable: no immutability on the containers that hold it, and a lifecycle expiry instead.
    assert "improvementProposalsWorm" not in data and "declinedQuestionsWorm" not in data
    lifecycle = block(data, "resource improvementLifecycle", "\n}\n")
    assert "'declined-questions/'" in lifecycle and "'improvement-proposals/'" in lifecycle
    # Blob versioning is on: expiry must remove noncurrent versions too, and example questions expire with the questions.
    assert lifecycle.count("daysAfterModificationGreaterThan: 30") == 2 and lifecycle.count("version: { delete:") == 2
    assert "daysAfterModificationGreaterThan: 90" not in lifecycle


def test_b4_only_the_runtime_writes_declined_questions_and_only_the_gap_review_reads_them():
    rbac = (ROOT / "infra/modules/rbac.bicep").read_text()
    grants = re.findall(r"scope: declinedQuestionsContainer\n\s+name: guid\([^,]+, (\w+), (roles\.\w+)\)", rbac)
    assert sorted(grants) == [("gapReviewPrincipalId", "roles.blobDataReader"),
                              ("runtimePrincipalId", "roles.blobDataContributor")]
    workloads = (ROOT / "infra/workloads.bicep").read_text()
    assert "GOVERNEDGRAPH_CAPTURE_DECLINED_QUESTIONS: deployAdminConsole" in workloads
    consumer = (ROOT / "infra/modules/gap-review-agent-consumer.bicep").read_text()
    assert "scope: agent" in consumer and "name: 'gg-gap-review'" in consumer and "scope: account" not in consumer
