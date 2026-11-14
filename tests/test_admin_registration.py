"""B8: admin console sign-in registration creates only the groups, membership and app it describes, idempotently."""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import configure_admin_console as console

ORIGIN = "https://apim-test.azure-api.net"
USER = "00000000-0000-0000-0000-0000000000aa"
OTHER = "00000000-0000-0000-0000-0000000000bb"
SEED = "627df32699264204a391842dd9fa673c"


class Graph:
    """A small stateful stand-in for Microsoft Graph; every call is recorded."""

    def __init__(self, *, assignment_denied=False, existing_groups=None, already_member=False):
        self.calls, self.groups, self.members, self.apps, self.principals, self.assignments = [], {}, {}, {}, {}, []
        self.assignment_denied, self.already_member, self.counter = assignment_denied, already_member, 0
        for name, extra in (existing_groups or {}).items():
            self._add_group(name, **extra)

    def _id(self, prefix):
        self.counter += 1
        return f"00000000-0000-0000-{prefix:04d}-{self.counter:012d}"

    def _add_group(self, name, **extra):
        gid = self._id(1)
        self.groups[gid] = {"id": gid, "displayName": name, "securityEnabled": True, "mailEnabled": False,
                            "groupTypes": [], **extra}
        self.members[gid] = set()
        return gid

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "GET" and path.startswith("groups?"):
            name = re.search(r"displayName eq '([^']*)'", path.replace("%20", " ").replace("%27", "'"))[1]
            return {"value": [g for g in self.groups.values() if g["displayName"] == name]}
        if method == "GET" and path.startswith("groups/"):
            gid = path.split("/")[1].split("?")[0]
            if gid not in self.groups:
                raise subprocess.CalledProcessError(1, "az", stderr="not found")
            if "/members/" in path:
                member = path.split("/members/")[1].split("?")[0]
                if member not in self.members[gid]:
                    raise subprocess.CalledProcessError(1, "az", stderr="not a member")
                return {"id": member}
            return self.groups[gid]
        if method == "POST" and path == "groups":
            return {"id": self._add_group(body["displayName"], securityEnabled=body["securityEnabled"],
                                          mailEnabled=body["mailEnabled"])}
        if method == "POST" and path.endswith("/members/$ref"):
            gid, member = path.split("/")[1], body["@odata.id"].rsplit("/", 1)[1]
            if member in self.members[gid] or self.already_member:
                self.members[gid].add(member)
                raise subprocess.CalledProcessError(1, "az", stderr="One or more added object references already exist")
            self.members[gid].add(member)
            return {}
        if method == "GET" and path.startswith("applications?"):
            name = re.search(r"displayName eq '(.*)'", path.replace("%20", " ").replace("%27", "'"))[1]
            return {"value": [a for a in self.apps.values() if a["displayName"] == name]}
        if method == "POST" and path == "applications":
            app = {"id": self._id(2), "appId": self._id(3), **body}
            self.apps[app["id"]] = app
            return app
        if method == "PATCH" and path.startswith("applications/"):
            app = self.apps[path.split("/")[1]]
            for key, value in body.items():  # Graph merges nested objects such as `api`
                app[key] = {**app.get(key, {}), **value} if isinstance(value, dict) and isinstance(app.get(key), dict) else value
            return {}
        if method == "GET" and path.startswith("servicePrincipals?"):
            return {"value": list(self.principals.values())}
        if method == "POST" and path == "servicePrincipals":
            principal = {"id": self._id(4), "appId": body["appId"]}
            self.principals[principal["id"]] = principal
            return principal
        if method == "PATCH" and path.startswith("servicePrincipals/"):
            self.principals[path.split("/")[1]].update(body)
            return {}
        if method == "GET" and path.endswith("/appRoleAssignedTo"):
            return {"value": [{"principalId": p} for p in self.assignments]}
        if method == "POST" and path.endswith("/appRoleAssignedTo"):
            if self.assignment_denied:
                raise subprocess.CalledProcessError(1, "az", stderr="Request_BadRequest: license required")
            self.assignments.append(body["principalId"])
            return {}
        raise AssertionError(f"unexpected Graph call {method} {path}")

    def writes(self):
        return [(m, p) for m, p, _ in self.calls if m != "GET"]


def apply(graph, **kwargs):
    return console.configure("gg-dev", ORIGIN, USER, apply=True, naming_seed=SEED, call=graph, **kwargs)


def test_plan_makes_no_graph_call_and_creates_no_credential_or_permission():
    graph = Graph()
    plan = console.configure("gg-dev", ORIGIN, USER, naming_seed=SEED, call=graph)
    assert plan["apply"] is False and graph.calls == []
    assert plan["credentials"] == "none" and plan["api_permissions"] == "none"
    assert plan["redirect_uri"] == ORIGIN + "/admin/" and "readers" in plan["display_names"]["reader"]


def test_apply_creates_separate_groups_adds_only_the_deploying_user_and_registers_the_app():
    graph = Graph()
    result = apply(graph)
    assert sorted(result["created_groups"]) == ["operator", "owner", "reader"]
    assert len({result["reader_group_id"], result["operator_group_id"], result["owner_group_id"]}) == 3
    assert all(members == {USER} for members in graph.members.values()) and len(graph.members) == 3
    app = next(iter(graph.apps.values()))
    assert app["spa"] == {"redirectUris": [ORIGIN + "/admin/"]} and app["groupMembershipClaims"] == "ApplicationGroup"
    assert app["signInAudience"] == "AzureADMyOrg" and "passwordCredentials" not in app and "keyCredentials" not in app
    assert not any("requiredResourceAccess" in (body or {}) for _, _, body in graph.calls)
    # Consent-free sign-in: the app exposes its own scope and pre-authorizes itself, like the voice presenter.
    assert app["identifierUris"] == [f"api://{app['appId']}"] and app["api"]["requestedAccessTokenVersion"] == 2
    scopes = app["api"]["oauth2PermissionScopes"]
    assert [s["value"] for s in scopes] == ["Admin.Access"] and scopes[0]["type"] == "User"
    assert app["api"]["preAuthorizedApplications"] == [
        {"appId": app["appId"], "delegatedPermissionIds": [scopes[0]["id"]]}]
    assert not any("00000003-0000-0000-c000-000000000000" in json.dumps(body or {}) for _, _, body in graph.calls)
    principal = next(iter(graph.principals.values()))
    assert principal["appRoleAssignmentRequired"] is True
    assert sorted(graph.assignments) == sorted([result["reader_group_id"], result["operator_group_id"], result["owner_group_id"]])
    assert result["client_id"] == app["appId"] and result["member"] == USER


def test_rerun_is_idempotent_and_never_removes_or_widens_anything():
    graph = Graph()
    first = apply(graph)
    writes_after_first = len(graph.writes())
    second = apply(graph)
    assert second["created_groups"] == [] and second["client_id"] == first["client_id"]
    assert second["reader_group_id"] == first["reader_group_id"]
    assert second["owner_group_id"] == first["owner_group_id"]
    assert len(graph.apps) == len(graph.principals) == 1 and len(graph.groups) == 3
    assert not any(method == "DELETE" for method, _ in graph.writes())
    # Only the member re-add attempt and the app patches repeat; no new group, app or assignment is created.
    repeated = graph.writes()[writes_after_first:]
    assert not any(path in ("groups", "applications", "servicePrincipals") for _, path in repeated)
    assert len(graph.assignments) == 3


def test_an_already_existing_membership_is_not_an_error_but_other_failures_are():
    assert apply(Graph(already_member=True))["created_groups"] == ["reader", "operator", "owner"]

    class Failing(Graph):
        def __call__(self, method, path, body=None):
            if path.endswith("/members/$ref"):
                raise subprocess.CalledProcessError(1, "az", stderr="Insufficient privileges")
            return super().__call__(method, path, body)

    with pytest.raises(subprocess.CalledProcessError):
        apply(Failing())


def test_supplied_groups_are_used_as_given_and_their_membership_is_never_changed():
    graph = Graph()
    reader, operator, owner = graph._add_group("my readers"), graph._add_group("my operators"), graph._add_group("my owners")
    graph.members[reader].add(OTHER)
    result = apply(graph, reader_group=reader, operator_group=operator, owner_group=owner)
    assert result["created_groups"] == [] and result["reader_group_id"] == reader
    assert graph.members[reader] == {OTHER} and graph.members[operator] == set()
    assert not any(path.endswith("/members/$ref") or path == "groups" for _, path in graph.writes())
    with pytest.raises(subprocess.CalledProcessError):
        apply(Graph(), reader_group="00000000-0000-0000-0000-0000000000ee", operator_group=operator)
    with pytest.raises(ValueError, match="different"):
        apply(graph, reader_group=reader, operator_group=reader)


@pytest.mark.parametrize("extra", [
    {"mailEnabled": True}, {"securityEnabled": False}, {"groupTypes": ["DynamicMembership"]}, {"isAssignableToRole": True},
])
def test_an_existing_group_that_is_not_a_plain_security_group_is_never_reused(extra):
    name = console.names("gg-dev", SEED)["reader"]
    graph = Graph(existing_groups={name: extra})
    with pytest.raises(ValueError, match="plain security group"):
        apply(graph)
    assert not any(path.endswith("/members/$ref") for _, path in graph.writes())


def test_duplicate_groups_or_apps_are_ambiguous_not_guessed():
    names = console.names("gg-dev", SEED)
    graph = Graph(existing_groups={names["reader"]: {}})
    graph._add_group(names["reader"])
    with pytest.raises(ValueError, match="ambiguous"):
        apply(graph)
    graph = Graph()
    for _ in range(2):
        app_id = graph._id(2)
        graph.apps[app_id] = {"id": app_id, "appId": graph._id(3), "displayName": names["app"], "signInAudience": "AzureADMyOrg"}
    with pytest.raises(ValueError, match="ambiguous"):
        apply(graph)


def test_an_existing_app_with_credentials_or_foreign_tenancy_is_rejected():
    for change in ({"passwordCredentials": [{"keyId": "x"}]}, {"signInAudience": "AzureADandPersonalMicrosoftAccount"}):
        graph = Graph()
        app_id = graph._id(2)
        graph.apps[app_id] = {"id": app_id, "appId": graph._id(3), "displayName": console.names("gg-dev", SEED)["app"],
                              "signInAudience": "AzureADMyOrg", **change}
        with pytest.raises(ValueError, match="unexpected credentials or tenancy"):
            apply(graph)


def test_group_assignment_failure_explains_the_license_requirement_and_the_fallback():
    with pytest.raises(ValueError, match="Entra ID P1 or P2.*SecurityGroup"):
        apply(Graph(assignment_denied=True))
    graph = Graph(assignment_denied=True)
    result = apply(graph, group_claim="SecurityGroup")
    assert result["group_claim"] == "SecurityGroup" and graph.assignments == []
    assert next(iter(graph.principals.values()))["appRoleAssignmentRequired"] is False


@pytest.mark.parametrize("kwargs", [
    {"environment": "production"}, {"origin": "http://apim.example"}, {"origin": ORIGIN + "/admin/"},
    {"origin": "https://apim.example:8443"}, {"member": "not-a-guid"}, {"reader_group": "x"}, {"owner_group": "x"},
    {"owner_member": "not-a-guid"}, {"group_claim": "All"},
])
def test_unsafe_inputs_are_rejected_before_any_graph_call(kwargs):
    graph = Graph()
    arguments = {"environment": "gg-dev", "origin": ORIGIN, "member": USER, "apply": True, "call": graph, **kwargs}
    environment, origin, member = (arguments.pop(key) for key in ("environment", "origin", "member"))
    with pytest.raises(ValueError):
        console.configure(environment, origin, member, **arguments)
    assert graph.calls == []


def test_opt_out_and_the_environment_gate_skip_everything(monkeypatch, capsys):
    monkeypatch.setattr(console, "configure", lambda *a, **k: pytest.fail("must not run"))
    monkeypatch.setattr(sys, "argv", ["configure_admin_console", "--apply"])
    for name, value in (("GOVERNEDGRAPH_ADMIN_ACCESS_CONTROL", "false"), ("GOVERNEDGRAPH_DEPLOY_ADMIN_CONSOLE", "false")):
        monkeypatch.setenv(name, value)
        assert console.enabled() is False
        monkeypatch.delenv(name)
    assert console.enabled() is True


def test_cli_records_the_results_in_the_azd_environment_and_identifies_the_deploying_user(monkeypatch, capsys):
    runs = []
    monkeypatch.setattr(subprocess, "run", lambda command, **k: runs.append(command) or subprocess.CompletedProcess(command, 0, "", ""))
    for name in ("GOVERNEDGRAPH_ADMIN_MEMBER_ID", "STRATEGY_RECOVERY_OPERATOR_ID", "VOICE_DEMO_OPERATOR_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOICE_DEMO_OPERATOR_ID", OTHER)
    assert console.deploying_user() == OTHER
    monkeypatch.setenv("STRATEGY_RECOVERY_OPERATOR_ID", USER)
    assert console.deploying_user() == USER
    monkeypatch.delenv("STRATEGY_RECOVERY_OPERATOR_ID")
    monkeypatch.delenv("VOICE_DEMO_OPERATOR_ID")
    monkeypatch.setattr(subprocess, "run", lambda command, **k: subprocess.CompletedProcess(command, 0, USER + "\n", ""))
    assert console.deploying_user() == USER
    monkeypatch.setattr(subprocess, "run", lambda command, **k: subprocess.CompletedProcess(command, 1, "", "err"))
    with pytest.raises(SystemExit, match="could not identify"):
        console.deploying_user()


def test_the_page_requests_exactly_the_scope_the_registration_exposes_and_the_server_requires():
    from governedgraph.admin import access

    script = (ROOT / "src/governedgraph/admin/static/admin.js").read_text()
    assert console.SCOPE == access.SCOPE == "Admin.Access"
    assert "api://${access.client_id}/Admin.Access" in script
    assert "openid profile" not in script
    assert "tokens.access_token" in script and "id_token" not in script


def test_hook_and_guided_runner_register_the_console_in_the_same_places_as_the_voice_demo():
    manifest = (ROOT / "azure.yaml").read_text()
    assert "hooks:" not in manifest
    bootstrap = (ROOT / "scripts/bootstrap.py").read_text()
    assert "scripts.configure_voice_demo import configure" in bootstrap
    assert "scripts.configure_admin_console import configure" in bootstrap
    assert 'foundation["gateway"]["gatewayUrl"]' in bootstrap
    for name in ("adminReaderGroupId", "adminOperatorGroupId", "adminOwnerGroupId", "adminClientId"):
        assert f'"{name}": console[' in bootstrap


def test_owner_enrollment_uses_the_deploying_human_not_the_configured_operator():
    graph = Graph()
    result = apply(graph, owner_member=OTHER)
    assert graph.members[result["owner_group_id"]] == {OTHER}
    assert graph.members[result["operator_group_id"]] == {USER}
    assert graph.members[result["reader_group_id"]] == {USER}
    calls = []

    def caller(method, path):
        calls.append((method, path))
        return {"id": OTHER}

    assert console.signed_in_user(caller) == OTHER
    assert calls == [("GET", "me?$select=id")]
    with pytest.raises(ValueError, match="signed-in deploying user"):
        console.signed_in_user(lambda *args: {})


def test_duplicate_graph_membership_is_verified_and_permission_failures_are_not_ignored():
    from governedgraph.strategy_service.provision_auth import GraphError

    calls = []

    def member_exists(method, path, body=None):
        calls.append((method, path))
        if method == "POST":
            raise GraphError(method, path, 400, "One or more added object references already exist")
        return {"id": USER}

    console.ensure_member(member_exists, OTHER, USER)
    assert calls[-1] == ("GET", f"groups/{OTHER}/members/{USER}?$select=id")

    def denied(method, path, body=None):
        raise GraphError(method, path, 403, "Insufficient privileges")

    with pytest.raises(GraphError):
        console.ensure_member(denied, OTHER, USER)
