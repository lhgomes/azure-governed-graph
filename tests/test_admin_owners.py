"""B7/B8: owner membership is explicit and recorded usage contains only count projections."""
import re
import time
from types import SimpleNamespace
from uuid import uuid4

import jwt
import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient

from governedgraph.admin import access, api
from governedgraph.admin.sources import AdminSourceError, AdminStrategyClient, AzureSources, Containers
from governedgraph.admin.data import revision
from governedgraph.admin.traces import SyntheticTraceReader


def unused_strategy_client() -> AdminStrategyClient:
    raise AssertionError("this read-only test must not construct an action client")


@pytest.fixture
def owners(monkeypatch):
    tenant, client, owner_group = (str(uuid4()) for _ in range(3))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(access, "signing_keys", lambda _: SimpleNamespace(
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key())))
    policy = access.AccessPolicy(client_id=client, tenant_id=tenant, owner_group=owner_group)

    def token(groups, **extra):
        now = int(time.time())
        claims = {"iss": f"https://login.microsoftonline.com/{tenant}/v2.0", "aud": client, "tid": tenant,
                  "oid": str(uuid4()), "iat": now, "exp": now + 600, "scp": "Admin.Access", "groups": groups}
        claims.update(extra)
        return "Bearer " + jwt.encode(claims, key, algorithm="RS256")

    return policy, owner_group, token


def test_b8_owner_only_configuration_grants_owners_access_without_admitting_other_users(owners):
    policy, group, token = owners
    identity = policy.require(token([group]), act=True)
    assert identity.can_read and identity.can_act and identity.can_own
    assert policy.public()["owner_group_set"] is True
    assert policy.public()["restricts_reads"] is True
    assert group not in str(policy.public())
    with pytest.raises(HTTPException) as error:
        policy.require(token([]), act=False)
    assert error.value.status_code == 403


def test_b8_operator_access_does_not_imply_owner_authority(owners):
    policy, _, token = owners
    operator = str(uuid4())
    configured = access.AccessPolicy(client_id=policy.client_id, tenant_id=policy.tenant_id,
                                    owner_group=policy.owner_group, operator_group=operator)
    identity = configured.require(token([operator]), act=True)
    assert identity.can_act and not identity.can_own
    with pytest.raises(HTTPException) as error:
        configured.require_owner(token([operator]))
    assert error.value.status_code == 403


def test_b8_strategy_owner_route_requires_both_human_owner_and_the_journal_writing_proxy(owners, monkeypatch):
    from governedgraph.strategy_service import auth

    policy, group, token = owners
    admin_id, api_id, fairness_id = (str(uuid4()) for _ in range(3))
    monkeypatch.setattr(auth, "settings", SimpleNamespace(
        strategy_api_client_id=api_id, strategy_tenant_id=policy.tenant_id, strategy_admin_id=admin_id,
        strategy_fairness_id=fairness_id, strategy_publisher_id=str(uuid4()), strategy_runtime_id=str(uuid4())))
    monkeypatch.setattr(auth, "signing_keys", access.signing_keys)
    monkeypatch.setenv("GOVERNEDGRAPH_ADMIN_CLIENT_ID", policy.client_id)
    monkeypatch.setenv("GOVERNEDGRAPH_ADMIN_OWNER_GROUP_ID", group)
    proxy = token([], oid=admin_id, aud=api_id, idtyp="app", scp="", roles=["Strategy.Suspend"])
    assert auth.require_portal_owner(token([group]), proxy).startswith("entra:")
    for human, machine in [
        (token([group]), ""), (token([]), proxy),
        (token([group]), token([], oid=fairness_id, aud=api_id, idtyp="app", scp="", roles=["Strategy.Suspend"])),
        (token([group], idtyp="app", scp=""), proxy),
    ]:
        with pytest.raises(HTTPException):
            auth.require_portal_owner(human, machine)


@pytest.mark.parametrize("fail_at, executed, status", [
    ("requested", False, 503), ("applied", True, 503), ("backend_conflict", True, 409), ("", True, 200),
])
@pytest.mark.parametrize("kind", ["override", "catalogue"])
def test_b8_owner_journal_precedes_execution_and_completion_failure_is_not_success(owners, monkeypatch, fail_at, executed, status, kind):
    policy, group, token = owners
    monkeypatch.setenv("GOVERNEDGRAPH_ADMIN_ENABLED", "true")
    monkeypatch.setenv("GOVERNEDGRAPH_ADMIN_PUBLIC_ORIGIN", "https://admin.example")
    state = {"current": None, "last_good": None, "frozen": False, "suspensions": []}
    records, calls = [], []

    def journal(record):
        completed = "pending_signature" if kind == "catalogue" else "applied"
        if record["status"] == fail_at or fail_at == "applied" and record["status"] == completed:
            raise AdminSourceError("ILLUSTRATIVE - synthetic journal outage")
        records.append(record)

    def execute(decision, authorization):
        assert records[0]["status"] == "requested"
        assert authorization.startswith("Bearer ")
        calls.append(decision)
        if fail_at == "backend_conflict":
            request = httpx.Request("POST", "https://synthetic.example/owner-overrides")
            response = httpx.Response(409, request=request, json={"detail": "Refresh and confirm the changed state."})
            raise httpx.HTTPStatusError("synthetic revision conflict", request=request, response=response)
        return {"decision_ref": decision.decision_ref,
                "status": "pending_signature" if kind == "catalogue" else "applied"}

    class Client:
        def freeze(self) -> None:
            raise AssertionError("unexpected freeze")

        def revert_to_static(self) -> None:
            raise AssertionError("unexpected static revert")

        def suspend(self, event) -> None:
            raise AssertionError("unexpected suspension")

        def owner_decision(self, decision, authorization) -> dict:
            return execute(decision, authorization)

        def catalogue_decision(self, decision, authorization) -> dict:
            return execute(decision, authorization)

    empty = SimpleNamespace()
    source = AzureSources(http=empty, containers=Containers(empty, empty, empty, empty),
                          strategy_client_factory=Client)
    monkeypatch.setattr(source, "state", lambda: state)
    monkeypatch.setattr(source, "journal", journal)
    app = api.create_app(access=policy, sources=source, trace_reader=SyntheticTraceReader())
    with TestClient(app) as web:
        match = re.search(r'name="admin-session" content="([^"]+)"', web.get("/").text)
        assert match is not None
        payload = {"expected_revision": revision(state), "confirmed": True,
                   "reason": "ILLUSTRATIVE - synthetic owner choice"}
        if kind == "catalogue":
            payload["change"] = {"action": "add", "name": "ILLUSTRATIVE - new explanation", "pattern_id": "S3",
                                 "eligible_journey_states": ["first_contact"], "channels": ["voice"],
                                 "accessibility_modes": ["standard"]}
        else:
            payload.update(action="pause", strategy_id="S2", journey_state="*")
        response = web.post("/api/catalogue" if kind == "catalogue" else "/api/owner-actions",
                            headers={"X-Admin-Session": match[1], "Authorization": token([group])}, json=payload)
    assert bool(calls) is executed
    assert response.status_code == status
    assert not any("authorization" in str(record).lower() for record in records)
    if fail_at == "applied":
        assert "may have applied" in response.json()["detail"]
    if fail_at == "backend_conflict":
        assert records[-1]["status"] == "failed"


def test_b8_owner_transport_keeps_human_and_proxy_tokens_separate(monkeypatch):
    from dataclasses import asdict
    from governedgraph.strategy_service import client
    from governedgraph.strategy_service.overrides import OwnerDecision

    client_id = str(uuid4())
    monkeypatch.setattr(client, "settings", SimpleNamespace(
        strategy_api_client_id=client_id, strategy_admin_url="https://synthetic.example/strategy-admin"))
    decision = OwnerDecision.from_dict({
        "action": "pause", "expected_revision": "a" * 64, "decision_ref": "GB-" + "b" * 32,
        "reason": "ILLUSTRATIVE - synthetic owner decision", "confirmed": True,
        "strategy_id": "S2", "journey_state": "*",
    })
    scopes = []

    class Credential:
        def get_token(self, scope):
            scopes.append(scope)
            return SimpleNamespace(token="synthetic-proxy-token")

    def respond(request):
        import json
        from governedgraph.strategy_service.catalogue import Catalogue

        if request.method == "GET":
            assert request.url.path == "/strategy-admin/catalogue"
            return httpx.Response(200, json={"catalogue": Catalogue.load().to_dict(), "priors": {}})
        assert request.url.path == "/strategy-admin/owner-overrides"
        assert request.headers["Authorization"] == "Bearer synthetic-owner-token"
        assert request.headers["X-Admin-Authorization"] == "Bearer synthetic-proxy-token"
        assert json.loads(request.content) == asdict(decision)
        return httpx.Response(200, json={"decision_ref": decision.decision_ref, "status": "applied"})

    http_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **options: http_client(
        transport=httpx.MockTransport(respond), **options))
    proxy = client.StrategyAdminClient(protective_only=True, credential=Credential())
    try:
        assert proxy.owner_decision(decision, "Bearer synthetic-owner-token")["status"] == "applied"
    finally:
        proxy.http.close()
    assert scopes == [f"api://{client_id}/.default"]


def test_b8_invalid_owner_group_configuration_fails_closed(owners):
    policy, _, token = owners
    broken = access.AccessPolicy(client_id=policy.client_id, tenant_id=policy.tenant_id, owner_group="not-a-guid")
    assert broken.mode == "misconfigured"
    with pytest.raises(HTTPException) as error:
        broken.require(token([]), act=True)
    assert error.value.status_code == 503


def test_b7_recorded_usage_counts_answer_records_not_learning_seeds(owners, monkeypatch):
    policy, group, token = owners
    monkeypatch.setenv("GOVERNEDGRAPH_ADMIN_ENABLED", "true")
    monkeypatch.setenv("GOVERNEDGRAPH_ADMIN_PUBLIC_ORIGIN", "https://admin.example")
    records = [
        {"journey_id": "J1", "member_ref": "SYNTHETIC-member", "created_at": "t", "strategy_id": strategy}
        for strategy in ("S1", "S1", "S2", "")
    ]
    empty = SimpleNamespace()
    source = AzureSources(http=empty, containers=Containers(empty, empty, empty, empty),
                          strategy_client_factory=unused_strategy_client)
    monkeypatch.setattr(source, "audit_records", lambda: (records, True))
    app = api.create_app(access=policy, sources=source, trace_reader=SyntheticTraceReader())
    with TestClient(app) as web:
        match = re.search(r'name="admin-session" content="([^"]+)"', web.get("/").text)
        assert match is not None
        session = match[1]
        response = web.get("/api/journeys", headers={
            "X-Admin-Session": session, "Authorization": token([group]),
        })
    assert response.status_code == 200
    assert response.json()["strategy_usage"] == {"S1": 2, "S2": 1}
    assert response.json()["truncated"] is True
    assert response.json()["journeys"][0]["responses"] == 4
