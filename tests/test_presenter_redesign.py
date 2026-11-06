"""ILLUSTRATIVE - synthetic data. Presenter UX preserves the governed channel."""
import asyncio
import base64
import hashlib
import html as html_parser
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from governedgraph.agents.counsellor import DISCLOSURE
from governedgraph.common.config import DATA_DIR
from governedgraph.common.contracts import ContractError
from governedgraph.voice import presenter
from governedgraph.voice.voice_live_bridge import ACKNOWLEDGEMENT, OrchestratorChannel, VoiceBridge, VoiceReply


@pytest.mark.parametrize("question", [
    "Will this change reduce my month checks?",
    "Will my monthly pension go down?",
    "What happens to my pension payments?",
    "Will my monthly cheque change?",
])
def test_pension_payment_agent_decision_uses_approved_transition_intent(question, tmp_path):
    from governedgraph.agents.counsellor import new_session
    from governedgraph.learning.run_cycle import run_cycle
    from governedgraph.strategy_service.service import StrategyService

    service = StrategyService(state_dir=tmp_path / "strategy", control_share=0)
    run_cycle(service)
    agent = new_session("MBR-7Q2K", strategy_service=service, journey_id="J-payment-fixture")
    agent.start()
    result = agent.handle(question)
    assert agent.journey.intent == "transition_impact_question"
    assert not result.escalated and "intent-agent" in result.workflow_steps


def test_general_information_agent_decision_is_distinct_from_transition_information():
    from governedgraph.common.contracts import IntentDecision

    decision = IntentDecision.from_dict({
        "route": "explanation", "intent": "general_question", "reason": "approved_fund_information",
    })
    assert decision.intent != "transition_impact_question"


def test_chat_upstream_failure_sends_visible_safe_error_frame_and_closes(monkeypatch):
    def handle(request):
        if request.url.path == "/journeys":
            return httpx.Response(200, json={"journey_id": "J-synthetic", "text": DISCLOSURE, "state": "disclosed"})
        return httpx.Response(503, json={"detail": "synthetic-private-upstream-detail"})

    async def serve(socket, claims):
        async with httpx.AsyncClient(base_url="https://synthetic/", transport=httpx.MockTransport(handle)) as client:
            await presenter.serve_chat(socket, OrchestratorChannel(client, lambda: "synthetic-private-member-token"))

    monkeypatch.setattr(presenter, "enabled", lambda: True)
    monkeypatch.setenv("VOICE_DEMO_ORIGIN", "https://demo.example")
    monkeypatch.setattr(presenter, "verify", lambda token: {"exp": time.time() + 300} if token == "operator"
                        else (_ for _ in ()).throw(HTTPException(403)))
    monkeypatch.setattr(presenter, "serve", serve)
    app = FastAPI()
    app.include_router(presenter.router)
    with TestClient(app, base_url="https://demo.example") as client:
        with client.websocket_connect("/demo-stream", headers={
            "Origin": "https://demo.example", "Cookie": f"{presenter.COOKIE}=operator",
        }) as socket:
            assert socket.receive_json()["text"] == DISCLOSURE
            assert socket.receive_json()["kind"] == "chat_ready"
            socket.send_json({"kind": "chat", "request_id": "synthetic-turn", "text": "Explain my letter"})
            assert socket.receive_json()["kind"] == "thinking"
            error = socket.receive_json()
            assert error["kind"] == "error" and "may already be saved" in error["message"]
            assert "synthetic-private" not in json.dumps(error)
            with pytest.raises(WebSocketDisconnect):
                socket.receive_json()


def _feedback_manifest():
    return {"outcome_digest": "a" * 64, "source_digest": "b" * 64, "baseline_digest": "c" * 64,
            "observed_decisions": 3, "eligible_decisions": 0, "pending_decisions": 3,
            "observed_resolutions": 1, "watermark": "ILLUSTRATIVE - synthetic data"}


@pytest.mark.parametrize("changes", [
    {"age_band": "75+"}, {"extra": "not permitted"}, {"outcome_digest": "A" * 64},
    {"source_digest": "not-a-digest"}, {"baseline_digest": None}, {"pending_decisions": -1},
    {"eligible_decisions": True}, {"observed_decisions": 4}, {"observed_resolutions": 4},
    {"watermark": "live member data"},
])
def test_b7_learning_feedback_uses_closed_manifest_validation(changes):
    with pytest.raises(ContractError, match="learning feedback"):
        VoiceReply.from_dict({"text": "Approved.", "state": "explained",
                              "learning_feedback": {**_feedback_manifest(), **changes}})


@pytest.mark.parametrize("manifest", [[], "not a manifest", {}, {"observed_decisions": 1}])
def test_learning_feedback_requires_a_complete_manifest(manifest):
    with pytest.raises(ContractError, match="learning feedback"):
        VoiceReply.from_dict({"text": "Approved.", "state": "explained", "learning_feedback": manifest})


def test_learning_feedback_absence_does_not_invent_counts_and_valid_payload_is_copied():
    reply = VoiceReply(text="Approved.", state="explained")
    assert reply.learning_feedback is None
    assert "learning_feedback" not in presenter.reply_metadata(reply)
    manifest = _feedback_manifest()
    reply = VoiceReply(text="Approved.", state="explained", learning_feedback=manifest)
    manifest["pending_decisions"] = 99
    assert presenter.reply_metadata(reply)["learning_feedback"] == _feedback_manifest()


def test_voice_progress_propagates_last_aggregate_projection_metadata():
    async def scenario():
        socket = SimpleNamespace(send_json=AsyncMock())
        reply = VoiceReply(text="Approved.", state="explained", learning_feedback=_feedback_manifest())
        await presenter.SocketAudio(socket).response_metadata(reply)
        assert socket.send_json.await_args.args[0]["learning_feedback"] == _feedback_manifest()
    asyncio.run(scenario())


@pytest.mark.parametrize("body", [
    {"kind": "chat", "request_id": "turn", "text": "Hello", "age_band": "75+"},
    {"kind": "chat", "request_id": "turn", "text": "Hello", "member_ref": "other"},
    {"kind": "chat", "request_id": "turn", "text": " "},
    {"kind": "chat", "request_id": "turn", "text": "x" * 4001},
    {"kind": "chat", "request_id": "turn", "text": None},
    {"kind": "chat", "request_id": "../bad", "text": "Hello"},
    {"kind": "handover", "request_id": "turn", "text": "extra"},
    {"kind": "end", "request_id": "turn", "text": "extra"},
    {"kind": "train", "request_id": "turn", "text": "Hello"},
])
def test_b7_chat_schema_is_closed_and_bounded(body):
    with pytest.raises((ContractError, ValueError)):
        presenter.ChatMessage.from_dict(body)


def test_chat_accepts_only_explicit_text_or_handover_and_session_has_no_member_fields():
    assert presenter.ChatMessage.from_dict({"kind": "chat", "request_id": "turn", "text": "Hello"}).text == "Hello"
    assert presenter.SessionRequest.from_dict({"mode": "chat"}).mode == "chat"
    with pytest.raises(ContractError):
        presenter.SessionRequest.from_dict({"mode": "chat", "member_ref": "other"})


def test_chat_disclosure_delivery_idempotency_metadata_and_handover_use_member_authorization(monkeypatch):
    calls = []

    def handle(request):
        assert request.headers["authorization"] == "Bearer synthetic-member-server-only"
        body = json.loads(request.content)
        calls.append((request.url.path, body, request.headers.get("idempotency-key")))
        if request.url.path == "/journeys":
            assert body == {"channel": "chat", "accessibility_mode": "standard"}
            return httpx.Response(200, json={"journey_id": "J-synthetic", "text": DISCLOSURE, "state": "disclosed"})
        duplicate = len(calls) == 3
        handover = body["utterance"] == "I want a human advisor"
        return httpx.Response(200, json={
            "text": "" if duplicate else "A person can help." if handover else "An approved explanation.",
            "state": "escalation_requested" if handover else "explained",
            "strategy_id": "S5", "snapshot_id": "signed-synthetic-snapshot", "ranked": ["S5"],
            "suspended": ["S2"], "audit_status": "committed", "arm": "graph",
            "journey_id": "J-synthetic", "request_id": request.headers["idempotency-key"],
            "duplicate": duplicate, "escalated": handover,
            "workflow_steps": ["screen-input", "intent-agent", "requested-human-handoff", "commit-checked-evidence"] if handover else
                              ["screen-input", "intent-agent", "explain-with-approved-tools", "commit-checked-evidence"],
            "learning_feedback": _feedback_manifest(),
        })

    async def serve(socket, claims):
        async with httpx.AsyncClient(base_url="https://synthetic/", transport=httpx.MockTransport(handle)) as client:
            await presenter.serve_chat(socket, OrchestratorChannel(client, lambda: "synthetic-member-server-only"))

    monkeypatch.setattr(presenter, "enabled", lambda: True)
    monkeypatch.setenv("VOICE_DEMO_ORIGIN", "https://demo.example")
    monkeypatch.setattr(presenter, "verify", lambda token: {"exp": 9999999999} if token == "operator" else
                        (_ for _ in ()).throw(HTTPException(403)))
    monkeypatch.setattr(presenter, "serve", serve)
    monkeypatch.setattr(presenter, "open_speech", lambda *a, **k: pytest.fail("chat must not open speech"))
    app = FastAPI()
    app.include_router(presenter.router)
    with TestClient(app, base_url="https://demo.example") as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/demo-stream", headers={"Origin": "https://demo.example"}):
                pytest.fail("unauthenticated chat accepted")
        response = client.post("/demo/authorize",
                               headers={"Origin": "https://demo.example", "Authorization": "Bearer operator"})
        assert "httponly" in response.headers["set-cookie"].lower()
        with client.websocket_connect("wss://demo.example/demo-stream", headers={"Origin": "https://demo.example"}) as socket:
            assert socket.receive_json() == {"kind": "caption", "role": "counsellor", "text": DISCLOSURE}
            assert socket.receive_json()["kind"] == "chat_ready"
            for index in range(3):
                socket.send_json({"kind": "handover", "request_id": "human"} if index == 2 else
                                 {"kind": "chat", "request_id": "same-turn", "text": "Explain my letter"})
                assert socket.receive_json()["kind"] == "thinking"
                metadata = socket.receive_json()
                assert metadata["snapshot_id"] == "signed-synthetic-snapshot" and metadata["audit_status"] == "committed"
                assert metadata["workflow_steps"][-1] == "commit-checked-evidence"
                assert metadata["learning_feedback"] == _feedback_manifest()
                if index != 1:
                    assert socket.receive_json()["kind"] == "caption"
                ready = socket.receive_json()
                assert ready["kind"] == "chat_ready" and ready["duplicate"] == (index == 1)
                assert "synthetic-member-server-only" not in json.dumps(metadata) + json.dumps(ready)
            assert socket.receive_json() == {"kind": "chat_complete", "state": "escalation_requested"}
    assert calls[1][2] == calls[2][2] == "same-turn"
    assert calls[-1][1] == {"utterance": "I want a human advisor"}


def test_chat_rejects_reuse_of_idempotency_key_for_different_text():
    async def scenario():
        socket = SimpleNamespace(send_json=AsyncMock(), receive_json=AsyncMock(side_effect=[
            {"kind": "chat", "request_id": "same", "text": "Hello"},
            {"kind": "chat", "request_id": "same", "text": "Different"},
        ]))
        channel = SimpleNamespace(start=AsyncMock(return_value=SimpleNamespace(journey_id="J-synthetic", text=DISCLOSURE)),
                                  turn=AsyncMock(return_value=VoiceReply(text="Approved.", state="explained")))
        with pytest.raises(ContractError, match="idempotency"):
            await presenter.serve_chat(socket, channel)
        assert channel.turn.await_count == 1
    asyncio.run(scenario())


def test_chat_host_never_opens_speech_or_warms_audio_and_keeps_token_in_server_memory(monkeypatch):
    import azure.identity.aio

    original_client = httpx.AsyncClient
    requests = []

    def handle(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer synthetic-member"
        if request.url.path == "/journeys":
            return httpx.Response(200, json={"journey_id": "J-synthetic", "text": DISCLOSURE, "state": "disclosed"})
        return httpx.Response(200, json={"text": "A person can help.", "state": "escalation_requested",
                                        "escalated": True, "audit_status": "committed"})

    monkeypatch.setattr(presenter.httpx, "AsyncClient",
                        lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs))
    credential = SimpleNamespace(__aenter__=AsyncMock(), __aexit__=AsyncMock())
    class Credential:
        async def __aenter__(self):
            return credential
        async def __aexit__(self, *args):
            pass
    monkeypatch.setattr(azure.identity.aio, "ManagedIdentityCredential", lambda **kwargs: Credential())
    monkeypatch.setattr(presenter, "member_bearer", AsyncMock(return_value="synthetic-member"))
    monkeypatch.setattr(presenter, "open_speech", lambda *a, **k: pytest.fail("chat opened speech"))
    monkeypatch.setenv("AZURE_CLIENT_ID", "synthetic-presenter")
    monkeypatch.setenv("GOVERNEDGRAPH_ORCHESTRATOR_URL", "https://synthetic/")
    socket = SimpleNamespace(receive_json=AsyncMock(side_effect=[
        {"mode": "chat"}, {"kind": "handover", "request_id": "human"},
    ]), send_json=AsyncMock())
    asyncio.run(presenter.serve(socket, {"exp": time.time() + 300}))
    assert len(requests) == 2
    sent = json.dumps([call.args[0] for call in socket.send_json.await_args_list])
    assert "synthetic-member" not in sent and DISCLOSURE in sent


def test_b9_voice_handover_during_playback_is_not_mistaken_for_audio_ack():
    async def scenario():
        socket = SimpleNamespace(send_json=AsyncMock(), send_bytes=AsyncMock())
        audio = presenter.SocketAudio(socket)
        async def received():
            return {"kind": "played", "id": socket.send_json.await_args.args[0]["id"]}
        socket.receive_json = AsyncMock(side_effect=[
            {"kind": "handover", "request_id": "human"},
        ])
        original_receive = socket.receive_json
        async def receive():
            if original_receive.await_count == 0:
                return await original_receive()
            return await received()
        socket.receive_json = receive
        await audio.play(b"\0\0")
        assert audio.handover_requested
        with pytest.raises(presenter.HandoverRequested):
            await audio.capture()
    asyncio.run(scenario())


@pytest.mark.parametrize("steps", [["age"], ["train-on-transcript"], "screen-and-route", [None]])
def test_b7_workflow_evidence_cannot_carry_unapproved_fields(steps):
    with pytest.raises(ContractError):
        VoiceReply(text="Approved.", state="explained", workflow_steps=steps)


def test_presenter_silent_checking_preserves_checked_sentence_audio_and_honest_latency():
    async def scenario():
        speech = SimpleNamespace(checked_sentence_audio=True, synthesize=AsyncMock(return_value=b"\0\0"),
                                 transcribe=AsyncMock(return_value="Explain my letter"))
        device = SimpleNamespace(play_text=AsyncMock(), response_metadata=AsyncMock())
        channel = SimpleNamespace(start=AsyncMock(return_value=SimpleNamespace(journey_id="J-synthetic", text=DISCLOSURE)),
                                  turn=AsyncMock(return_value=VoiceReply(text="First sentence. Second sentence.", state="explained")))
        bridge = VoiceBridge(channel, speech, device, spoken_acknowledgement=False)
        await bridge.start()
        await bridge.turn(b"\0\0")
        spoken = [call.args[0] for call in speech.synthesize.await_args_list]
        assert spoken[0] == DISCLOSURE and ACKNOWLEDGEMENT not in spoken
        assert "".join(spoken[1:]) == "First sentence. Second sentence."
        assert bridge.first_audio_seconds == bridge.substantive_first_seconds
        assert bridge.acknowledgement_seconds == []
        assert bridge.metrics()["slo_certified"] is False
    asyncio.run(scenario())


def test_ui_has_accessible_voice_states_real_metadata_secondary_video_and_csp(monkeypatch):
    monkeypatch.setattr(presenter, "enabled", lambda: True)
    monkeypatch.setenv("VOICE_DEMO_ORIGIN", "https://demo.example")
    response = presenter.index()
    html = response.body.decode()
    assert DISCLOSURE in html_parser.unescape(html) and "{{AI_DISCLOSURE}}" not in html
    assert 'role="log"' in html and 'aria-live="polite"' in html and 'maxlength="4000"' in html
    for state in ("listening", "thinking", "speaking"):
        assert state in html
    for field in ("meta-strategy", "meta-snapshot", "meta-audit", "meta-suspended"):
        assert f'id="{field}"' in html
    assert "<details><summary>Watch the synthetic Annelies" in html
    assert '<details id="diagnostics">' in html and "not met" in html and "prefers-reduced-motion" in html
    assert "ILLUSTRATIVE - synthetic data" in html and "not a live training feed" in html
    policy = response.headers["content-security-policy"]
    for tag in ("script", "style"):
        content = re.search(f"<{tag}>(.*?)</{tag}>", html, re.S)[1]
        digest = base64.b64encode(hashlib.sha256(content.encode()).digest()).decode()
        assert f"'sha256-{digest}'" in policy
    assert "'unsafe-inline'" not in policy and "<script src=" not in html


def test_channel_defaults_to_voice_and_rejects_unknown_channel():
    async def scenario():
        bodies = []
        def handle(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"journey_id": "J-synthetic", "text": DISCLOSURE, "state": "disclosed"})
        async with httpx.AsyncClient(base_url="https://synthetic/", transport=httpx.MockTransport(handle)) as client:
            channel = OrchestratorChannel(client, lambda: "synthetic")
            await channel.start()
            assert bodies[0]["channel"] == "voice"
            with pytest.raises(ContractError):
                await channel.start(channel="demographic")
            assert len(bodies) == 1
    asyncio.run(scenario())


def test_ui_chat_states_and_real_workflow_evidence_without_microphone():
    if not shutil.which("node"):
        pytest.skip("Node is needed for the dependency-free DOM harness")
    html = (DATA_DIR / "catalogue" / "ui" / "presenter.html").read_text()
    script = re.search(r"<script>(.*?)</script>", html, re.S)[1]
    harness = r"""
const assert = require("node:assert/strict");
const elements = new Map();
function element(){
  return {disabled:true,hidden:false,textContent:"",value:"",dataset:{},children:[],
    append(...items){this.children.push(...items)},replaceChildren(){this.children=[]},scrollIntoView(){},
    getContext(){return {fillRect(){},fillText(){},measureText(){return {width:0}}}}};
}
global.document={getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id)},
  createElement:element,createTextNode(text){return {textContent:text}}};
global.location={origin:"https://synthetic.example",hash:""};
global.window={addEventListener(){}};
global.requestAnimationFrame=()=>{};
global.fetch=async()=>({ok:true,json:async()=>({})});
global.crypto=require("node:crypto").webcrypto;
const timers=new Map();let timerId=0;
global.setTimeout=(callback,delay)=>{timers.set(++timerId,{callback,delay});return timerId};
global.clearTimeout=id=>timers.delete(id);
global.WebSocket=class {
  static OPEN=1;static CLOSED=3;
  constructor(){this.readyState=1;this.sent=[]}
  send(text){this.sent.push(JSON.parse(text))}
  close(){this.readyState=3}
};
"""
    assertions = r"""
(async()=>{
  await connect("chat");socket.onopen();
  assert.equal(audio,undefined,"chat must not create AudioContext");
  assert.equal(socket.sent[0].mode,"chat");
  await socket.onmessage({data:JSON.stringify({kind:"caption",role:"counsellor",text:"Canonical disclosure"})});
  await socket.onmessage({data:JSON.stringify({kind:"chat_ready"})});
  assert.equal($("voice-stage").dataset.state,"typing");
  $("chat-text").value="Explain my letter";$("chat-form").onsubmit({preventDefault(){}});
  assert.equal(socket.sent[1].kind,"chat");assert.ok(socket.sent[1].request_id);
  assert.equal($("voice-stage").dataset.state,"thinking");
  assert.equal(timers.size,1);
  assert.equal([...timers.values()][0].delay,150000);
  assert.equal($("conversation").children.length,2);
  assert.equal($("learning-feedback").hidden,true);
  assert.equal($("feedback-observed").textContent,"Unavailable");
  await socket.onmessage({data:JSON.stringify({kind:"progress",state:"explained",strategy:"S5",
    suspended:["S2"],ranked:["S5"],snapshot_id:"real-snapshot",audit_status:"committed",
    workflow_steps:["screen-input","intent-agent","commit-checked-evidence"],
    learning_feedback:{outcome_digest:"a".repeat(64),source_digest:"b".repeat(64),baseline_digest:"c".repeat(64),
      observed_decisions:3,eligible_decisions:0,pending_decisions:3,observed_resolutions:1,
      watermark:"ILLUSTRATIVE - synthetic data"}})});
  assert.equal($("meta-snapshot").textContent,"real-snapshot");
  assert.equal($("workflow-steps").children[1].textContent,"intent-agent");
  assert.equal($("learning-feedback").hidden,false);
  assert.equal($("feedback-observed").textContent,"3");
  assert.equal($("feedback-eligible").textContent,"0");
  assert.equal($("feedback-pending").textContent,"3");
  assert.equal($("feedback-baseline").textContent,"c".repeat(64));
  metadata({});
  assert.equal($("learning-feedback").hidden,true);
  assert.equal($("feedback-observed").textContent,"Unavailable","old counts must not persist when metadata is absent");
  await socket.onmessage({data:JSON.stringify({kind:"caption",role:"counsellor",text:"A checked explanation."})});
  await socket.onmessage({data:JSON.stringify({kind:"chat_ready"})});
  await socket.onmessage({data:JSON.stringify({kind:"chat_complete",state:"resolved"})});
  assert.equal($("chat-text").disabled,false,"a resolved answer is not the end of the session");
  assert.equal($("send").disabled,false);
  $("chat-text").value="And what does that mean?";$("chat-form").onsubmit({preventDefault(){}});
  assert.equal(socket.sent[2].kind,"chat","normal follow-up must reach the socket");
  await socket.onmessage({data:JSON.stringify({kind:"caption",role:"counsellor",text:"Another checked answer."})});
  requestHandover();assert.equal(handoverPending,true);
  await socket.onmessage({data:JSON.stringify({kind:"chat_ready"})});
  assert.equal(socket.sent[3].kind,"handover");
  assert.equal($("chat-text").disabled,true);
  socket.readyState=WebSocket.CLOSED;socket.onclose();
  assert.equal($("conversation-error").hidden,false);
  assert.match($("conversation-error").textContent,/Connection closed before a reply/);
  assert.equal(timers.size,0);
  await connect("chat");socket.onopen();
  assert.equal($("conversation-error").hidden,true);
  await socket.onmessage({data:JSON.stringify({kind:"chat_ready"})});
  $("chat-text").value="Explain my letter";$("chat-form").onsubmit({preventDefault(){}});
  [...timers.values()][0].callback();
  assert.equal($("conversation-error").hidden,false);
  assert.match($("conversation-error").textContent,/No reply arrived in time/);
  assert.equal(socket.readyState,WebSocket.CLOSED);
  assert.equal(timers.size,0);
  await connect("chat");socket.onopen();
  await socket.onmessage({data:JSON.stringify({kind:"error",message:"Synthetic delivery unavailable"})});
  assert.equal($("conversation-error").hidden,false);
  assert.equal($("conversation-error").textContent,"Synthetic delivery unavailable");
  await connect("chat");socket.onopen();
  await socket.onmessage({data:JSON.stringify({kind:"chat_ready"})});
  $("end-session").onclick();
  assert.equal(socket.sent[1].kind,"end","ending must not request a human");
  assert.equal($("send").disabled,true);
  await socket.onmessage({data:JSON.stringify({kind:"session_ended"})});
  assert.equal(waitingForInput,false);
  await socket.onmessage({data:JSON.stringify({kind:"chat_complete",state:"escalation_requested"})});
  assert.equal($("chat-text").disabled,true);
  assert.equal($("send").disabled,true);
  console.log("UX states and evidence passed");
})().catch(error=>{console.error(error);process.exitCode=1});
"""
    result = subprocess.run(["node", "-e", harness + script + assertions],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_b9_normal_chat_end_does_not_send_an_advisor_request_or_fabricate_resolution():
    async def scenario():
        socket = SimpleNamespace(send_json=AsyncMock(), receive_json=AsyncMock(return_value={
            "kind": "end", "request_id": "synthetic-end",
        }))
        channel = SimpleNamespace(start=AsyncMock(return_value=SimpleNamespace(
            journey_id="J-synthetic", text=DISCLOSURE,
        )), turn=AsyncMock())
        await presenter.serve_chat(socket, channel)
        assert socket.send_json.await_args.args[0] == {"kind": "session_ended"}
        channel.turn.assert_not_called()
    asyncio.run(scenario())


def test_b9_real_presenter_voice_loop_accepts_followup_after_resolved_then_ends_without_handoff(monkeypatch):
    from collections import deque
    from contextlib import asynccontextmanager
    import azure.identity.aio
    from governedgraph.voice import warmup

    turns, messages = [], []
    ids = ["a" * 32, "b" * 32, "c" * 32]
    def backend(request):
        if request.url.path == "/journeys":
            return httpx.Response(200, json={"journey_id": "J-synthetic", "text": DISCLOSURE, "state": "identified"})
        if request.url.path.endswith("/turns"):
            turns.append(json.loads(request.content)["utterance"])
            return httpx.Response(200, json={"text": "A checked answer.", "state": "resolved",
                                          "strategy_id": "S1", "audit_status": "committed"})
        return httpx.Response(200, json={
            "records": [{"journey_id": "J-synthetic", "response_id": key, "numeric_fidelity_passed": True}
                        for key in ids], "delivered_response_ids": ids,
        })
    real_client = httpx.AsyncClient
    monkeypatch.setattr(presenter.httpx, "AsyncClient",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(backend), **kwargs))
    @asynccontextmanager
    async def identity(**kwargs):
        yield SimpleNamespace()
    @asynccontextmanager
    async def speech(*args, **kwargs):
        yield SimpleNamespace(
            synthesize=AsyncMock(return_value=b"\0\0"),
            transcribe=AsyncMock(side_effect=["First factual question.", "A follow-up question."]),
        )
    monkeypatch.setattr(azure.identity.aio, "ManagedIdentityCredential", identity)
    monkeypatch.setattr(presenter, "open_speech", speech)
    monkeypatch.setattr(presenter, "member_bearer", AsyncMock(return_value="synthetic-in-memory-token"))
    monkeypatch.setattr(warmup, "warm_runtime", lambda: None)
    for key in ("AZURE_CLIENT_ID", "GOVERNEDGRAPH_ORCHESTRATOR_URL", "AZURE_VOICE_LIVE_ENDPOINT"):
        monkeypatch.setenv(key, "https://synthetic.invalid")
    class Socket:
        def __init__(self):
            self.started, self.captures, self.playback = False, 0, deque()
        async def receive_json(self):
            if not self.started:
                self.started = True
                return {"mode": "presenter"}
            return {"kind": "played", "id": self.playback.popleft()}
        async def send_json(self, message):
            messages.append(message)
            if message["kind"] == "audio":
                self.playback.append(message["id"])
        async def send_bytes(self, pcm):
            assert pcm == b"\0\0"
        async def receive(self):
            self.captures += 1
            if self.captures <= 2:
                return {"type": "websocket.receive", "bytes": b"\0\0"}
            return {"type": "websocket.receive", "text": json.dumps({"kind": "end", "request_id": "end"})}
    asyncio.run(presenter.serve(Socket(), {"exp": time.time() + 300}))
    assert turns == ["First factual question.", "A follow-up question."]
    assert messages[-1]["kind"] == "complete" and messages[-1]["state"] == "resolved"
    assert messages[-1]["audit_written"] is True
    assert sum(message["kind"] == "capture" for message in messages) == 3
