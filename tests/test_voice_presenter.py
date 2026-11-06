"""ILLUSTRATIVE - synthetic data. Presenter auth never substitutes for member step-up."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import AsyncMock
from uuid import uuid4
import xml.etree.ElementTree as ET
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import jwt
import pytest
from starlette.websockets import WebSocketDisconnect

from governedgraph.common.config import settings
from governedgraph.common.contracts import ContractError
from governedgraph.dev import member_issuer_api
from governedgraph.voice import access, presenter
from governedgraph.voice.voice_live_bridge import AudioFailure, PCM_RATE
from governedgraph.voice.voice_live_bridge import VoiceBridge, VoiceReply
from scripts import configure_voice_demo


@pytest.fixture
def presenter_auth(monkeypatch):
    tenant, app_id, operator, runtime, runtime_client = (str(uuid4()) for _ in range(5))
    monkeypatch.setattr(settings.__class__, "is_azure", property(lambda _: True))
    monkeypatch.setattr(settings.__class__, "environment_name", property(lambda _: "gg-dev"))
    monkeypatch.setattr(settings.__class__, "strategy_tenant_id", property(lambda _: tenant))
    for key, value in {
        "GOVERNEDGRAPH_DEPLOY_VOICE_DEMO": "true", "VOICE_DEMO_CLIENT_ID": app_id,
        "VOICE_DEMO_OPERATOR_ID": operator, "VOICE_BRIDGE_PRINCIPAL_ID": runtime,
        "VOICE_BRIDGE_CLIENT_ID": runtime_client, "VOICE_DEMO_ORIGIN": "https://demo.example",
        "GOVERNEDGRAPH_ENABLE_DEV_MEMBER_ISSUER": "true", "MEMBER_TOKEN_ISSUER": "synthetic",
        "AZURE_CLIENT_ID": "synthetic-issuer", "AZURE_KEY_VAULT_URL": "https://synthetic.vault.azure.net",
    }.items():
        monkeypatch.setenv(key, value)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(access, "signing_keys", lambda _: SimpleNamespace(
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key=private.public_key())))

    def token(*, application=False, **updates):
        claims = {"iss": f"https://login.microsoftonline.com/{tenant}/v2.0", "tid": tenant,
                  "aud": app_id, "iat": int(time.time()), "exp": int(time.time()) + 300,
                  "oid": runtime if application else operator,
                  "azp": runtime_client if application else app_id,
                  "roles": ["VoiceDemo.Issue" if application else "VoiceDemo.Presenter"]}
        if application:
            claims["idtyp"] = "app"
        else:
            claims["scp"] = "VoiceDemo.Access"
        claims.update(updates)
        return jwt.encode(claims, private, algorithm="RS256")
    return token


@pytest.mark.parametrize("updates", [
    {"oid": "other-user"}, {"scp": ""}, {"roles": []}, {"idtyp": "app"},
    {"azp": "unapproved-client"}, {"tid": "other-tenant"}, {"aud": "other-api"},
    {"exp": 0}, {"roles": "VoiceDemo.Presenter"}, {"scp": ["VoiceDemo.Access"]},
])
def test_presenter_requires_assigned_user_scope_role_tenant_audience_and_client(presenter_auth, updates):
    with pytest.raises(HTTPException):
        access.verify(presenter_auth(**updates))


def test_presenter_and_issuer_permissions_are_not_interchangeable(presenter_auth):
    assert access.verify(presenter_auth())["scp"] == "VoiceDemo.Access"
    assert access.verify(presenter_auth(application=True), application=True)["idtyp"] == "app"
    for token, application in ((presenter_auth(), True), (presenter_auth(application=True), False)):
        with pytest.raises(HTTPException) as error:
            access.verify(token, application=application)
        assert error.value.status_code == 403


@pytest.mark.parametrize("updates", [
    {"oid": "dev-issuer"}, {"azp": "other-client"}, {"roles": []},
    {"scp": "VoiceDemo.Access"}, {"idtyp": "user"},
])
def test_only_runtime_bridge_can_invoke_issuer(presenter_auth, updates):
    with pytest.raises(HTTPException) as error:
        access.verify(presenter_auth(application=True, **updates), application=True)
    assert error.value.status_code == 403


def test_presenter_is_fail_closed_outside_dev_or_without_flag(presenter_auth, monkeypatch):
    token = presenter_auth()
    monkeypatch.setenv("GOVERNEDGRAPH_DEPLOY_VOICE_DEMO", "false")
    with pytest.raises(HTTPException) as error:
        access.verify(token)
    assert error.value.status_code == 404
    monkeypatch.setenv("GOVERNEDGRAPH_DEPLOY_VOICE_DEMO", "true")
    monkeypatch.setattr(settings.__class__, "environment_name", property(lambda _: "production"))
    with pytest.raises(HTTPException):
        access.verify(token)


def test_authorization_sets_only_delegated_httponly_session_cookie(presenter_auth, monkeypatch):
    app = FastAPI()
    app.include_router(presenter.router)
    with TestClient(app, base_url="https://demo.example") as client:
        token = presenter_auth()
        response = client.post("/demo/authorize", headers={
            "Authorization": "Bearer " + token, "Origin": "https://demo.example"})
        assert response.json() == {"ok": True}
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie
        assert "max-age" not in cookie and "expires" not in cookie
        assert response.headers["cache-control"] == "no-store"
        assert client.post("/demo/authorize", headers={
            "Authorization": "Bearer " + token, "Origin": "https://evil.example"}).status_code == 403
        assert client.post("/demo/authorize", json={"age_band": "75+"}, headers={
            "Authorization": "Bearer " + token, "Origin": "https://demo.example"}).status_code == 400
        page = client.get("/demo/")
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert "'unsafe-inline'" not in page.headers["content-security-policy"]
        canonical = client.get("/demo", follow_redirects=False)
        assert canonical.status_code == 200 and "location" not in canonical.headers


def test_websocket_denies_bad_origin_missing_auth_and_query_credentials(presenter_auth):
    app = FastAPI()
    app.include_router(presenter.router)
    with TestClient(app, base_url="https://demo.example") as client:
        for path, origin in (("/demo-stream", "https://evil.example"),
                             ("/demo-stream", "https://demo.example"),
                             ("/demo-stream?token=forbidden", "https://demo.example")):
            with pytest.raises(WebSocketDisconnect) as error:
                with client.websocket_connect(path, headers={"Origin": origin}):
                    pytest.fail("unauthorized socket accepted")
            assert error.value.code == 1008


def test_issuer_mints_fixed_short_lived_synthetic_member_only(presenter_auth, monkeypatch):
    import azure.identity
    monkeypatch.setattr(azure.identity, "ManagedIdentityCredential", MagicMock())
    monkeypatch.setattr(member_issuer_api, "KeyVaultTokenSigner", lambda *a, **k: SimpleNamespace(sign=lambda _: "c2lnbmF0dXJl"))
    with TestClient(member_issuer_api.app) as client:
        headers = {"Authorization": "Bearer " + presenter_auth(application=True)}
        result = client.post("/issue", headers=headers)
        assert result.status_code == 200
        claims = jwt.decode(result.json()["token"], options={"verify_signature": False})
        assert claims["member_ref"] == "MBR-7Q2K" and claims["acr"] == "stepup"
        assert 0 < claims["exp"] - claims["iat"] <= 900
        assert result.headers["cache-control"] == "no-store"
        assert client.post("/issue", json={"member_ref": "MBR-OTHER"}, headers=headers).status_code == 400
        assert client.post("/issue", headers={"Authorization": "Bearer " + presenter_auth()}).status_code == 403


@pytest.mark.parametrize("body", [
    {"mode": "presenter", "age_band": "75+"},
    {"mode": "simulator", "script": {"opening": "Hello", "teach_back_answer": "Thanks", "age_band": "75+"}},
    {"mode": "presenter", "script": {}}, {"mode": "unknown"}, {"mode": "simulator"},
])
def test_presenter_b7_closed_contracts_reject_unknown_demographic_and_invalid_fields(body):
    with pytest.raises(ContractError):
        presenter.SessionRequest.from_dict(body)


def test_presenter_audio_requires_exact_ack_and_bounded_input():
    class Socket:
        async def send_json(self, body): self.last = body
        async def send_bytes(self, pcm): self.pcm = pcm
        async def receive_json(self): return {"kind": "played", "id": self.last["id"]}
        async def receive_bytes(self): return bytes(PCM_RATE * 2 * 21)
    async def scenario():
        audio = presenter.SocketAudio(Socket())
        await audio.play(b"\0\0")
        assert audio.socket.pcm == b"\0\0"
        with pytest.raises(AudioFailure):
            await audio.capture()
    asyncio.run(scenario())


def test_registration_flag_and_environment_prevent_directory_calls(monkeypatch):
    monkeypatch.setattr(configure_voice_demo, "graph", lambda *a: pytest.fail("unexpected directory mutation"))
    assert configure_voice_demo.configure("gg-dev", "", "", "", deploy=False)["enabled"] is False
    with pytest.raises(ValueError):
        configure_voice_demo.configure("production", "", "", "", deploy=True)


def test_presenter_policies_enforce_exact_origin_and_auth_without_audio_logging():
    root = Path(__file__).resolve().parents[1]
    for filename in ("voice-demo.xml", "voice-demo-stream.xml"):
        policy = ET.parse(root / "infra" / "policies" / filename)
        assert policy.find(".//validate-jwt") is not None
        assert policy.find(".//rate-limit-by-key") is not None
        assert policy.find(".//check-header[@name='Origin']/value").text == "__ORIGIN__"
        assert not policy.findall(".//log-to-eventhub")
    stream = ET.parse(root / "infra/policies/voice-demo-stream.xml")
    assert stream.find(".//cors") is None
    assert "Cookie" in stream.find(".//validate-jwt").attrib["token-value"]


def test_voice_reports_first_playback_and_complete_audio_p95_with_independent_counts():
    async def scenario():
        clock = iter([0, 1, 1.1, 2, 2.5, 2.6])
        speech = SimpleNamespace(transcribe=AsyncMock(return_value="Explain my letter"),
                                 synthesize=AsyncMock(return_value=b"\0\0"))
        channel = SimpleNamespace(turn=AsyncMock(return_value=VoiceReply(text="Approved text", state="explained")))
        bridge = VoiceBridge(channel, speech, SimpleNamespace(play=AsyncMock()), clock=lambda: next(clock))
        bridge.journey_id, bridge.disclosed = "J-synthetic", True
        await bridge.turn(b"\0\0")
        await bridge.turn(b"\0\0")
        result = bridge.metrics()
        assert result["first_audio_p95_ms"] == pytest.approx(1100)
        assert result["voice_turn_p95_ms"] == pytest.approx(1000)
        assert result["first_audio_sample_count"] == result["complete_audio_sample_count"] == 2
        assert result["slo_certified"] is False
    asyncio.run(scenario())


def test_registration_disabling_flag_disables_existing_principal_without_deleting_data(monkeypatch):
    calls = []
    def graph(method, path, body=None):
        calls.append((method, path, body))
        if method == "GET":
            return {"value": [{"appId": "app"}] if path.startswith("applications?") else [{"id": "principal"}]}
        return {}
    monkeypatch.setattr(configure_voice_demo, "graph", graph)
    result = configure_voice_demo.configure("gg-dev", "", "", "", deploy=False, apply=True)
    assert result["enabled"] is False
    assert calls[-1] == ("PATCH", "servicePrincipals/principal", {"accountEnabled": False})
    assert all(call[0] != "DELETE" for call in calls)


def test_fresh_presenter_disable_does_not_touch_the_old_deployments_registration(monkeypatch):
    from urllib.parse import unquote

    calls = []

    def graph(method, path, body=None):
        calls.append((method, unquote(path), body))
        return {"value": []}

    monkeypatch.setattr(configure_voice_demo, "graph", graph)
    seed = "a" * 32
    result = configure_voice_demo.configure("gg-dev", "", "", "", apply=True, naming_seed=seed)
    assert result["enabled"] is False
    assert calls == [("GET", f"applications?$filter=displayName eq 'GovernedGraph gg-dev Voice Demo-{seed}'", None)]


def test_authenticated_presenter_socket_is_bound_to_assigned_operator(presenter_auth, monkeypatch):
    seen = []
    async def serve(socket, claims):
        seen.append(claims["oid"])
        await socket.send_json({"kind": "test", "ok": True})
    monkeypatch.setattr(presenter, "serve", serve)
    app = FastAPI()
    app.include_router(presenter.router)
    with TestClient(app, base_url="https://demo.example") as client:
        client.post("/demo/authorize", headers={
            "Authorization": "Bearer " + presenter_auth(), "Origin": "https://demo.example"})
        with client.websocket_connect("wss://demo.example/demo-stream",
                                      headers={"Origin": "https://demo.example"}) as socket:
            assert socket.receive_json() == {"kind": "test", "ok": True}
    assert len(seen) == 1
