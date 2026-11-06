"""B8: optional Entra group access control is fail-closed, level-separated and visible when off."""
import re
import time
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from governedgraph.admin import access as access_module
from governedgraph.admin import api
from governedgraph.admin.access import AccessPolicy
from governedgraph.admin.data import read_lines
from governedgraph.strategy_service.service import StrategyService
from governedgraph.strategy_service.state import LocalStateStore

TENANT, CLIENT = str(uuid4()), str(uuid4())
READERS, OPERATORS, OUTSIDERS = str(uuid4()), str(uuid4()), str(uuid4())
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def offline_keys(monkeypatch):
    fetcher = SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=KEY.public_key()))
    monkeypatch.setattr(access_module, "signing_keys", lambda tenant: fetcher)


def token(groups=(), *, key=KEY, **override):
    now = int(time.time())
    claims = {"iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "aud": CLIENT, "tid": TENANT,
              "oid": str(uuid4()), "iat": now, "exp": now + 600, "preferred_username": "synthetic.operator@example.test",
              "scp": "Admin.Access", "groups": groups}
    claims.update(override)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm="RS256")


def console(tmp_path, monkeypatch, policy):
    monkeypatch.setenv("GOVERNEDGRAPH_ADMIN_ENABLED", "true")
    service = StrategyService(store=LocalStateStore(tmp_path / "strategy_service" / "state.json"))
    client = TestClient(api.create_app(service, tmp_path, access=policy), base_url="http://127.0.0.1:8090")
    page = client.get("/")
    client.headers["x-admin-session"] = re.search(r'name="admin-session" content="([^"]+)"', page.text)[1]
    return client, page


def policy(reader=READERS, operator=OPERATORS, client=CLIENT, tenant=TENANT):
    return AccessPolicy(reader, operator, client, tenant)


def bearer(value):
    return {"authorization": f"Bearer {value}"}


def freeze(client, headers):
    state = client.get("/api/overview", headers=headers).json()
    return client.post("/api/actions", headers=headers, json={
        "action": "freeze", "reason": "Synthetic operator protection", "expected_revision": state["revision"]})


def test_blank_groups_leave_access_open_and_say_so(tmp_path, monkeypatch):
    web, _ = console(tmp_path, monkeypatch, AccessPolicy())
    assert web.get("/api/access").json() == {"mode": "disabled", "open_actions": True}
    overview = web.get("/api/overview").json()
    assert overview["access"] == {"mode": "disabled", "user": "Access control off", "can_act": True, "open_actions": True}
    assert freeze(web, {}).status_code == 200
    assert read_lines(tmp_path / "admin" / "actions.jsonl")[0]["actor"] == "local-loopback-operator"
    assert "login.microsoftonline.com" not in web.get("/").headers["content-security-policy"]
    assert "Access control is off" in web.get("/").text
    assert "GOVERNEDGRAPH_ADMIN_READER_GROUP_ID" in web.get("/").text


def test_whitespace_only_groups_count_as_not_configured(monkeypatch):
    monkeypatch.setenv("GOVERNEDGRAPH_ADMIN_READER_GROUP_ID", "   ")
    monkeypatch.delenv("GOVERNEDGRAPH_ADMIN_OPERATOR_GROUP_ID", raising=False)
    assert AccessPolicy.from_env().mode == "disabled"


@pytest.mark.parametrize("path,method", [("/api/overview", "get"), ("/api/journeys", "get"),
                                         ("/api/journeys/J-1", "get"), ("/api/traces/J-1", "get"),
                                         ("/api/actions", "post")])
def test_enabled_requires_a_signed_in_user_for_every_data_route(tmp_path, monkeypatch, path, method):
    web, _ = console(tmp_path, monkeypatch, policy())
    response = getattr(web, method)(path, **({"json": {}} if method == "post" else {}))
    assert response.status_code == 401
    public = web.get("/api/access").json()
    assert public["mode"] == "enabled" and public["client_id"] == CLIENT
    assert READERS not in str(public) and OPERATORS not in str(public)
    assert "https://login.microsoftonline.com" in web.get("/").headers["content-security-policy"]


def test_reader_can_view_but_cannot_apply_protective_actions(tmp_path, monkeypatch):
    web, _ = console(tmp_path, monkeypatch, policy())
    headers = bearer(token([READERS]))
    overview = web.get("/api/overview", headers=headers).json()
    assert overview["access"]["can_act"] is False and overview["access"]["mode"] == "enabled"
    assert web.get("/api/journeys", headers=headers).status_code == 200
    response = freeze(web, headers)
    assert response.status_code == 403 and "operator group" in response.json()["detail"]
    assert not (tmp_path / "admin" / "actions.jsonl").exists()
    assert not web.get("/api/overview", headers=headers).json()["state"]["frozen"]


def test_operator_acts_without_reader_membership_and_is_recorded_by_verified_identity(tmp_path, monkeypatch):
    web, _ = console(tmp_path, monkeypatch, policy())
    oid = str(uuid4())
    headers = bearer(token([OPERATORS], oid=oid))
    assert freeze(web, headers).status_code == 200
    assert [row["actor"] for row in read_lines(tmp_path / "admin" / "actions.jsonl")] == [f"entra:{oid}"] * 2


def test_user_in_neither_group_is_denied(tmp_path, monkeypatch):
    web, _ = console(tmp_path, monkeypatch, policy())
    for groups in ([], [OUTSIDERS]):
        response = web.get("/api/overview", headers=bearer(token(groups)))
        assert response.status_code == 403 and "not in a group" in response.json()["detail"]


def test_operator_only_configuration_lets_signed_in_users_read_but_not_act(tmp_path, monkeypatch):
    web, _ = console(tmp_path, monkeypatch, policy(reader=""))
    member = bearer(token([OUTSIDERS]))
    assert web.get("/api/overview", headers=member).json()["access"]["can_act"] is False
    assert freeze(web, member).status_code == 403
    assert freeze(web, bearer(token([OPERATORS]))).status_code == 200


def test_reader_only_configuration_leaves_actions_closed_to_everyone(tmp_path, monkeypatch):
    web, _ = console(tmp_path, monkeypatch, policy(operator=""))
    headers = bearer(token([READERS]))
    assert web.get("/api/overview", headers=headers).status_code == 200
    assert freeze(web, headers).status_code == 403
    assert web.get("/api/overview", headers=bearer(token([OUTSIDERS]))).status_code == 403


@pytest.mark.parametrize("bad", [
    lambda: token([OPERATORS], key=OTHER_KEY),
    lambda: token([OPERATORS], aud=str(uuid4())),
    lambda: token([OPERATORS], iss=f"https://login.microsoftonline.com/{uuid4()}/v2.0"),
    lambda: token([OPERATORS], tid=str(uuid4())),
    lambda: token([OPERATORS], exp=int(time.time()) - 30),
    lambda: token([OPERATORS], oid=None),
    lambda: token([OPERATORS], scp="User.Read"),
    lambda: token([OPERATORS], scp=None),
    lambda: token([OPERATORS], scp="Admin.AccessExtra"),
    lambda: token([OPERATORS], scp=["Admin.Access"]),
    lambda: token([OPERATORS], idtyp="app"),
    lambda: "not.a.jwt",
])
def test_invalid_or_non_id_tokens_are_rejected_without_detail(tmp_path, monkeypatch, bad):
    web, _ = console(tmp_path, monkeypatch, policy())
    response = web.get("/api/overview", headers=bearer(bad()))
    assert response.status_code == 401 and "invalid or expired" in response.json()["detail"]


def test_group_overage_and_missing_group_claims_fail_closed(tmp_path, monkeypatch):
    web, _ = console(tmp_path, monkeypatch, policy())
    overage = token(groups=None, _claim_names={"groups": "src1"})
    assert web.get("/api/overview", headers=bearer(overage)).status_code == 403
    assert web.get("/api/overview", headers=bearer(token(groups=OPERATORS))).status_code == 403


@pytest.mark.parametrize("broken", [
    {"operator": "not-a-guid"}, {"reader": "x"}, {"client": ""}, {"tenant": ""},
])
def test_misconfiguration_is_never_silently_open(tmp_path, monkeypatch, broken):
    web, _ = console(tmp_path, monkeypatch, policy(**broken))
    public = web.get("/api/access").json()
    assert public["mode"] == "misconfigured" and "misconfigured" in public["detail"]
    for headers in ({}, bearer(token([OPERATORS]))):
        assert web.get("/api/overview", headers=headers).status_code == 503
        assert web.post("/api/actions", headers=headers, json={}).status_code == 503


def test_session_capability_is_still_required_when_signed_in(tmp_path, monkeypatch):
    web, _ = console(tmp_path, monkeypatch, policy())
    headers = {**bearer(token([OPERATORS])), "x-admin-session": "wrong"}
    assert web.get("/api/overview", headers=headers).status_code == 403
