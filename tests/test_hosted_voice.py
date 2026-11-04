"""ILLUSTRATIVE - synthetic data. Hosted identity separation, closed script and audit proof."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from governedgraph.common.contracts import ContractError
from governedgraph.common.member_auth import MemberIdentity
from governedgraph.orchestrator import api
from governedgraph.voice import hosted
from governedgraph.voice.hosted import VoiceScriptRequest, run_hosted
from governedgraph.voice.voice_live_bridge import AudioFailure
from test_voice_live import Device, Speech, host


SCRIPT = {"opening": "Explain my transition letter",
          "teach_back_answer": "It stays the same and can go up or down each year."}


def test_b7_hosted_script_cannot_carry_a_persona_profile():
    for extra in ({"age": 75}, {"persona_id": "annelies"}, {"profile": {"age": 75}},
                  {"endpoint": "https://not-authorized.example"}):
        with pytest.raises(ContractError):
            VoiceScriptRequest.from_dict({**SCRIPT, **extra})


def test_hosted_voice_reuses_http_auth_and_forwards_token_only_in_memory(host, monkeypatch):
    seen = []

    async def simulated(script, token, identity):
        seen.append((script, token, identity))
        return {"journey_id": "J-voice-proof", "disclosure_first": True}

    monkeypatch.setattr(api, "run_hosted", simulated)

    async def scenario():
        async with httpx.AsyncClient(transport=host.transport, base_url="https://channel/") as client:
            assert (await client.post("/voice/simulations", json=SCRIPT)).status_code == 401
            response = await client.post("/voice/simulations", json=SCRIPT,
                                         headers={"Authorization": "Bearer synthetic"})
            assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
            assert seen[0][1] == "synthetic" and seen[0][2] == MemberIdentity("MBR-7Q2K", True)
            assert (await client.post("/voice/simulations", json={**SCRIPT, "age_band": "older"},
                                      headers={"Authorization": "Bearer synthetic"})).status_code == 400
    asyncio.run(scenario())


def test_hosted_voice_is_disabled_outside_dev_and_requires_step_up(monkeypatch):
    async def scenario():
        monkeypatch.setattr(hosted, "settings", SimpleNamespace(is_azure=False))
        with pytest.raises(PermissionError):
            await run_hosted(VoiceScriptRequest(**SCRIPT), "synthetic", MemberIdentity("MBR-7Q2K", True))
        monkeypatch.setattr(hosted, "settings", SimpleNamespace(is_azure=True, environment_name="gg-dev"))
        monkeypatch.setenv("GOVERNEDGRAPH_ENABLE_VOICE_PROOF", "true")
        for identity in (MemberIdentity("MBR-OTHER", True), MemberIdentity("MBR-7Q2K", False)):
            with pytest.raises(PermissionError):
                await run_hosted(VoiceScriptRequest(**SCRIPT), "synthetic", identity)
    asyncio.run(scenario())


def test_hosted_voice_errors_report_code_location_not_private_exception_content(host, monkeypatch, caplog):
    async def refused(*args):
        raise AudioFailure("PRIVATE figures and transcript")

    monkeypatch.setattr(api, "run_hosted", refused)

    async def scenario():
        async with httpx.AsyncClient(transport=host.transport, base_url="https://channel/") as client:
            response = await client.post("/voice/simulations", json=SCRIPT,
                                         headers={"Authorization": "Bearer synthetic"})
            assert response.status_code == 503 and "PRIVATE" not in response.text
    asyncio.run(scenario())
    assert "PRIVATE" not in caplog.text
    assert any(getattr(row, "failure_domain", "").startswith("voice.refused.L") for row in caplog.records)


@pytest.mark.parametrize("question,arm", [
    (None, "treatment"),
    ("Could you explain what happens on the transition date?", "treatment"),
    ("Tell me what you understood about the transition.", "treatment"),
    (None, "control"),
])
def test_runtime_identity_synthesizes_but_profiles_and_member_tokens_never_leave_http_headers(
        host, monkeypatch, question, arm):
    import azure.identity.aio

    created = []
    spoken = []

    class Credential:
        def __init__(self, *, client_id):
            created.append(client_id)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    @asynccontextmanager
    async def speech(endpoint, voice, *, credential):
        session = Speech([SCRIPT["opening"], SCRIPT["teach_back_answer"]])
        spoken.append(session.spoken)
        yield session

    original_turn = hosted.OrchestratorChannel.turn

    async def turn(channel, *args):
        reply = await original_turn(channel, *args)
        if question is not None and reply.state == "comprehension_check":
            return replace(reply, text=question)
        return reply

    original = httpx.AsyncClient
    monkeypatch.setattr("governedgraph.strategy_service.service.assign_arm", lambda *args: arm)
    monkeypatch.setattr(hosted.OrchestratorChannel, "turn", turn)
    monkeypatch.setattr(hosted, "settings", SimpleNamespace(
        is_azure=True, environment_name="gg-dev", strategy_runtime_id="runtime-principal"))
    monkeypatch.setattr(azure.identity.aio, "ManagedIdentityCredential", Credential)
    monkeypatch.setattr(hosted, "open_speech", speech)
    monkeypatch.setattr("governedgraph.voice.warmup.warm_runtime", lambda: None)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=host.transport, **kwargs))
    for key, value in {
        "GOVERNEDGRAPH_ENABLE_VOICE_PROOF": "true", "AZURE_CLIENT_ID": "runtime-client",
        "AZURE_VOICE_LIVE_ENDPOINT": "https://existing.services.ai.azure.com",
        "GOVERNEDGRAPH_ORCHESTRATOR_URL": "https://channel",
    }.items():
        monkeypatch.setenv(key, value)

    result = asyncio.run(run_hosted(VoiceScriptRequest(**SCRIPT), "synthetic", MemberIdentity("MBR-7Q2K", True)))
    assert created == ["runtime-client"]
    assert all(result[name] for name in (
        "disclosure_first", "s2_suspended", "teach_back_passed", "bound_text_only",
        "explainability_written", "under_two_minutes",
    ))
    assert result["arm"] == arm and result["strategy_id"] == ("S1" if arm == "control" else "S5")
    assert result["s5_selected"] is (arm == "treatment")
    turns = 1 if arm == "control" else 2
    assert len(result["response_ids"]) == turns + 1 and result["metrics"]["turn_count"] == turns
    assert spoken[1] == [SCRIPT["opening"], SCRIPT["teach_back_answer"]][:turns]
    assert "synthetic" not in str(result["response_ids"]) and "text" not in result
