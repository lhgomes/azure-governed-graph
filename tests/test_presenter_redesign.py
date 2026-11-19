"""ILLUSTRATIVE - synthetic data. Presenter UX preserves the governed channel."""
import asyncio
import base64
import hashlib
import html as html_parser
from html.parser import HTMLParser
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
from governedgraph.orchestrator.state_machine import Journey
from governedgraph.voice import presenter
from governedgraph.voice.simulations import load_simulations
from governedgraph.voice.voice_live_bridge import ACKNOWLEDGEMENT, OrchestratorChannel, VoiceBridge, VoiceReply


@pytest.mark.parametrize("question", [
    "Will this change reduce my month checks?",
    "Will my monthly pension go down?",
    "What happens to my pension payments?",
    "Will my monthly cheque change?",
])
def test_pension_payment_agent_decision_uses_approved_transition_intent(question, tmp_path, monkeypatch):
    from counsellor_doubles import install, new_session
    from strategy_doubles import local_strategy_service, run_cycle

    install(monkeypatch, tmp_path)
    service = local_strategy_service(tmp_path / "strategy", control_share=0)
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


def test_chat_idle_expiry_ends_normally_without_claiming_a_saved_message(monkeypatch):
    original_wait_for = asyncio.wait_for

    async def idle(awaitable, timeout):
        if timeout == 300:
            awaitable.close()
            raise TimeoutError
        return await original_wait_for(awaitable, timeout)

    async def serve(socket, claims):
        channel = SimpleNamespace(start=AsyncMock(return_value=SimpleNamespace(
            text=DISCLOSURE, journey_id="J-synthetic")))
        await presenter.serve_chat(socket, channel)

    monkeypatch.setattr(presenter.asyncio, "wait_for", idle)
    monkeypatch.setattr(presenter, "enabled", lambda: True)
    monkeypatch.setenv("VOICE_DEMO_ORIGIN", "https://demo.example")
    monkeypatch.setattr(presenter, "verify", lambda _: {"exp": time.time() + 300})
    monkeypatch.setattr(presenter, "serve", serve)
    app = FastAPI()
    app.include_router(presenter.router)
    with TestClient(app, base_url="https://demo.example") as client:
        with client.websocket_connect("/demo-stream", headers={"Origin": "https://demo.example"}) as socket:
            assert socket.receive_json()["text"] == DISCLOSURE
            assert socket.receive_json()["kind"] == "chat_ready"
            assert socket.receive_json() == {"kind": "session_ended", "reason": "idle"}
            with pytest.raises(WebSocketDisconnect):
                socket.receive_json()


def _feedback_manifest():
    return {"outcome_digest": "a" * 64, "source_digest": "b" * 64, "baseline_digest": "c" * 64,
            "observed_decisions": 3, "eligible_decisions": 0, "pending_decisions": 3,
            "observed_resolutions": 1, "watermark": "ILLUSTRATIVE - synthetic data",
            "reported_yes": 0, "reported_no": 0, "reported_skip": 0, "unknown_after_window": 0,
            "attributed_repeats": 0, "ambiguous_contacts": 0}


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


@pytest.mark.parametrize("trace", [
    [{"stage": "intent", "result": "What does my pension mean for me", "issues": []}],
    [{"stage": "transcript", "result": "pass", "issues": []}],
    [{"stage": "answer_review", "result": "revise", "issues": [], "quote": "leaked text"}],
    [{"stage": "answer_review", "result": "revise", "issues": ["Your pension is 1,000"]}],
])
def test_b4_turn_evidence_carries_closed_codes_never_conversation_text(trace):
    with pytest.raises(ContractError):
        VoiceReply(text="Approved.", state="explaining", turn_trace=trace)


def test_turn_evidence_reaches_the_presenter_so_a_declined_turn_is_explained():
    trace = [{"stage": "answer_review", "result": "revise", "issues": ["grounding", "relevance"]},
             {"stage": "outcome", "result": "follow_up_question", "issues": []}]
    reply = VoiceReply(text="What would you like me to explain?", state="explaining",
                       composition_source="unanswered", turn_trace=trace)
    metadata = presenter.reply_metadata(reply)
    assert metadata["turn_trace"] == trace and metadata["composition_source"] == "unanswered"


def test_unanswered_streak_is_persisted_only_when_set():
    journey = Journey(journey_id="J-streak", member_ref="member")
    assert "unanswered_streak" not in journey.to_dict()
    journey.unanswered_streak = 1
    assert Journey.from_dict(journey.to_dict()).unanswered_streak == 1


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


def test_ui_has_accessible_voice_states_real_metadata_optional_video_and_csp(monkeypatch):
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
    assert '<label class="sr-only" for="simulation">Story</label><select id="simulation" disabled>' in html
    assert '<button id="present" class="primary" disabled>Voice</button>' in html
    assert '<button id="chat" disabled>Chat</button>' in html
    assert '<button id="simulate" disabled>Play</button>' in html
    assert "select{font:inherit}" in html and "select:focus-visible" in html
    assert ".demo-toolbar button,.story-picker select{min-height:48px;line-height:1.4}" in html
    assert "@media(max-width:600px)" in html and "prefers-reduced-motion" in html
    for removed in ("Latency &amp; delivery diagnostics", "Aggregate provenance", 'id="latency-summary"',
                    'id="result"', 'id="feedback-baseline"', 'class="voice-mark"', 'class="principles"'):
        assert removed not in html
    assert "ILLUSTRATIVE - synthetic data" in html and "not a live training feed" in html
    assert html.count('<p class="label">Synthetic data</p>') == 1
    policy = response.headers["content-security-policy"]
    for tag in ("script", "style"):
        content = re.search(f"<{tag}>(.*?)</{tag}>", html, re.S)[1]
        digest = base64.b64encode(hashlib.sha256(content.encode()).digest()).decode()
        assert f"'sha256-{digest}'" in policy
    assert "'unsafe-inline'" not in policy and "<script src=" not in html


def test_executive_demo_keeps_conversation_primary_and_technical_evidence_collapsed():
    class Layout(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []
            self.elements = {}

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            identity = attributes.get("id")
            if identity:
                self.elements[identity] = (attributes, [parent for _, parent in self.stack])
            if tag not in {"meta", "link", "input", "br", "hr"}:
                self.stack.append((tag, identity))

        def handle_endtag(self, tag):
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index][0] == tag:
                    del self.stack[index:]
                    break

    layout = Layout()
    html = (DATA_DIR / "catalogue" / "ui" / "presenter.html").read_text()
    layout.feed(html)
    assert "open" not in layout.elements["evidence"][0]
    for identity in ("meta-strategy", "meta-snapshot", "meta-arm", "workflow-steps", "learning-feedback", "download"):
        assert "evidence" in layout.elements[identity][1]
    for identity in ("present", "chat", "contact-type", "simulation", "simulate"):
        assert "demo-controls" in layout.elements[identity][1]
        assert "hidden" not in layout.elements[identity][0]
    for identity in ("stop", "chat-form", "conversation", "demo-controls"):
        assert "evidence" not in layout.elements[identity][1]
    for identity in ("speak", "finish", "end-session", "screen"):
        assert "hidden" in layout.elements[identity][0]
    assert "hidden" not in layout.elements["conversation-panel"][0]
    assert "max-height:36dvh;overflow:auto" in html
    assert 'id="disclosure">{{AI_DISCLOSURE}}' in html
    assert "<aside" not in html


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


@pytest.mark.usefixtures("sample_simulations")
def test_ui_chat_states_and_real_workflow_evidence_without_microphone():
    if not shutil.which("node"):
        pytest.skip("Node is needed for the dependency-free DOM harness")
    html = (DATA_DIR / "catalogue" / "ui" / "presenter.html").read_text()
    script = re.search(r"<script>(.*?)</script>", html, re.S)[1]
    harness = r"""
const assert = require("node:assert/strict");
const elements = new Map();
const channelErrors = [];
console.error = (...args) => channelErrors.push(args);
function element(){
  return {disabled:true,hidden:false,textContent:"",value:"",dataset:{},children:[],
    append(...items){this.children.push(...items)},replaceChildren(){this.children=[]},scrollIntoView(){},
    setAttribute(key,value){this.attrs={...this.attrs,[key]:value}},
    click(){this.clickCount=(this.clickCount||0)+1},
    getContext(){return {fillRect(){},fillText(){},measureText(){return {width:0}}}}};
}
global.document={getElementById(id){
    assert.ok(ids.has(id),"missing HTML element: "+id);
    if(!elements.has(id)){const item=element();item.hidden=hiddenIds.has(id);if(id==="contact-type")item.value="first_contact";elements.set(id,item)}
    return elements.get(id)},
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
    harness = (
        "const ids=new Set(" + json.dumps(re.findall(r'\bid="([^"]+)"', html)) + ");\n"
        "const hiddenIds=new Set(" + json.dumps(re.findall(r'<[^>]*\bid="([^"]+)"[^>]*\shidden(?:\s|>)', html)) + ");\n"
        + harness
    )
    harness += "global.fetch=async()=>({ok:true,json:async()=>({simulations:" + json.dumps(
        [item.summary() for item in load_simulations()]) + "})});\n"
    assertions = r"""
(async()=>{
  assert.equal($("present").disabled,true);
  assert.equal($("chat").disabled,true);
  await authorize("synthetic-presenter-token");
  assert.equal($("present").hidden,false);
  assert.equal($("chat").hidden,false);
  assert.equal($("speak").hidden,true);
  assert.equal($("finish").hidden,true);
  assert.equal($("stop").disabled,false,"human support must remain available");
  await connect("chat");socket.onopen();
  assert.equal($("present").hidden,false);
  assert.equal($("chat").hidden,false);
  assert.equal($("present").disabled,true);
  assert.equal($("chat").disabled,true);
  assert.equal($("speak").hidden,true);
  assert.equal($("end-session").hidden,false);
  assert.equal($("conversation-panel").hidden,false);
  assert.equal($("screen").hidden,true);
  assert.equal(audio,undefined,"chat must not create AudioContext");
  assert.equal(socket.sent[0].mode,"chat");
  await socket.onmessage({data:JSON.stringify({kind:"caption",role:"counsellor",text:"Canonical disclosure"})});
  assert.equal($("disclosure").hidden,true,"the spoken disclosure is now visible in the conversation");
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
  assert.equal($("feedback-status").textContent,"Aggregate outcomes available.");
  assert.equal(elements.has("feedback-baseline"),false,"provenance digests must not create a second display");
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
  assert.equal($("present").hidden,false);
  assert.equal($("chat").hidden,false);
  assert.equal($("speak").hidden,true);
  assert.equal($("finish").hidden,true);
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
  await socket.onmessage({data:JSON.stringify({kind:"error",message:"Synthetic delivery unavailable",
    error_type:"HTTPStatusError",location:"presenter.serve.L478"})});
  assert.equal($("conversation-error").hidden,false);
  assert.equal($("conversation-error").textContent,"Synthetic delivery unavailable");
  assert.equal($("status").hidden,true,"the actual error must not be duplicated by a generic failure status");
  assert.equal($("voice-stage").dataset.state,"error","failure must not become a completed conversation");
  assert.deepEqual(channelErrors.at(-1),["Channel operation failed",
    {error_type:"HTTPStatusError",location:"presenter.serve.L478"}]);
  socket.onclose();
  assert.equal($("conversation-error").textContent,"Synthetic delivery unavailable","closing must preserve the server's error");
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
  socket.readyState=WebSocket.CLOSED;socket.onclose();
  assert.equal($("conversation-error").hidden,true,"a confirmed ending must not become a connection failure");
  await load();
  assert.equal($("simulation").children.length,6);
  assert.ok($("simulation").children.some(option=>option.value==="samira"));
  assert.ok($("simulation").children.some(option=>option.value==="daan"),"the Dutch persona is offered");
  global.AudioContext=class {
    async resume(){}
    createMediaStreamDestination(){return {stream:{getAudioTracks:()=>[]}}}
  };
  global.MediaStream=class {constructor(tracks){this.tracks=tracks}};
  global.MediaRecorder=class {
    constructor(stream,options){this.mimeType=options.mimeType;this.state="inactive"}
    start(){this.state="recording"}
    stop(){this.state="inactive";this.onstop()}
  };
  canvas.captureStream=()=>({getVideoTracks:()=>[],getTracks:()=>[]});
  await connect("presenter");socket.onopen();
  assert.equal($("status").textContent,"Starting your conversation...");
  socket.readyState=WebSocket.CLOSED;socket.onclose();
  assert.equal($("voice-stage").dataset.state,"error","a startup crash must not leave the UI starting");
  assert.equal($("conversation-error").hidden,false);
  assert.match($("conversation-error").textContent,/Connection closed before the conversation finished/);
  assert.equal($("status").hidden,true);
  assert.equal($("present").disabled,false,"voice can be restarted after a failed startup");
  assert.equal($("stop").disabled,false,"human support must remain available after a startup failure");
  await connect("presenter");socket.onopen();
  await socket.onmessage({data:JSON.stringify({kind:"capture"})});
  assert.equal($("speak").hidden,false);
  assert.equal($("finish").hidden,true);
  inputStream={getTracks:()=>[]};syncControls();
  assert.equal($("speak").hidden,true);
  assert.equal($("finish").hidden,false);
  stopCapture(false);
  assert.equal($("speak").hidden,false);
  assert.equal($("finish").hidden,true);
  socket.readyState=WebSocket.CLOSED;socket.onclose();
  $("simulation").value="joost";
  await connect("simulator");socket.onopen();
  assert.equal($("screen").hidden,true,"example captions use the same conversation log, never a duplicate video");
  assert.equal($("speak").hidden,true);
  assert.deepEqual(socket.sent[0],{mode:"simulator",simulation_id:"joost",contact_type:"first_contact"});
  assert.equal($("simulation").disabled,true);
  assert.equal($("takeover").hidden,false,"the presenter can take over a persona simulation");
  assert.equal($("takeover").disabled,false);
  $("takeover").onclick();
  assert.deepEqual({...JSON.parse(JSON.stringify(socket.sent.at(-1))),request_id:undefined},{kind:"takeover",request_id:undefined});
  assert.equal($("takeover").disabled,true,"takeover is requested once");
  await socket.onmessage({data:JSON.stringify({kind:"takeover_ready",channel:"chat"})});
  assert.equal($("chat-form").hidden,false,"a taken-over chat persona becomes a typed conversation");
  assert.equal($("takeover").hidden,true);
  assert.equal(sessionMode,"chat");
  socket.onmessage({data:JSON.stringify({kind:"complete",ended_by:"persona_unavailable",state:"explaining",
    strategy:null,suspended:[],session_ended:false})});
  assert.match($("status").textContent,/simulated member could not continue/);
  addCaption("persona","Synthetic opening");
  assert.equal($("conversation").children[1].children[0].textContent,"Joost (synthetic)");
  recorder.ondataavailable({data:new Blob(["synthetic-video"])});
  $("simulation").value="samira";
  recorder.stop();
  assert.equal($("download").download,"joost-voice-fallback.webm","recording filename must capture the active persona");
  assert.equal($("download").hidden,false);
  assert.equal($("download").clickCount,undefined,"Play must not automatically download a video");
  socket.readyState=WebSocket.CLOSED;socket.onclose();
  await connect("simulator");socket.onopen();
  assert.deepEqual(socket.sent[0],{mode:"simulator",simulation_id:"samira",contact_type:"first_contact"});
  addCaption("persona","Synthetic opening");
  assert.equal($("conversation").children[0].children[0].textContent,"Samira (synthetic)");
  addCaption("persona","Thank you for explaining. Goodbye.");
  await socket.onmessage({data:JSON.stringify({kind:"session_ended"})});
  assert.equal($("conversation").children.length,3,"one entry per member utterance, plus the ending notification");
  assert.equal($("conversation").children[2].children[0].textContent,"Session","ending is not an AI-authored reply");
  assert.equal($("conversation").children[2].children[1].textContent,"Conversation ended. Human support remains available.");
  recorder.ondataavailable({data:new Blob(["synthetic-video"])});
  recorder.stop();
  assert.equal($("download").download,"samira-voice-fallback.webm");
  socket.readyState=WebSocket.CLOSED;socket.onclose();
  await connect("simulator");socket.onopen();
  const turn=(strategy,trace)=>socket.onmessage({data:JSON.stringify({kind:"progress",state:"explaining",strategy,suspended:[],ranked:[],
    audit_status:"committed",composition_source:"foundry",turn_trace:trace,
    workflow_steps:["screen-input","intent-agent","explain-with-approved-tools","commit-checked-evidence"]})});
  const text=node=>(node.textContent||"")+" "+(node.children||[]).map(text).join(" ");
  assert.equal($("conversation-summary").hidden,true,"the consolidated view waits for the end");
  await turn("S5",[{stage:"intent",result:"payment_continuity",issues:[]},{stage:"answer_review",result:"pass",issues:[]},
    {stage:"outcome",result:"answered",issues:[]}]);
  await socket.onmessage({data:JSON.stringify({kind:"caption",role:"counsellor",text:"Approved answer."})});
  const answered=$("conversation").children.at(-1),badge=answered.children.find(child=>child.className==="info-badge");
  assert.equal(badge.textContent,"i");assert.equal(badge.attrs["aria-expanded"],"false");
  assert.equal(answered.turnPanel.hidden,true,"the detail is collapsed until the i badge is selected");
  badge.onclick();
  assert.equal(answered.turnPanel.hidden,false);assert.equal(badge.attrs["aria-expanded"],"true");
  assert.match(text(answered.turnPanel),/Understood the question as: payment continuity\./);
  assert.match(text(answered.turnPanel),/Explanation style: S5\./);
  assert.match(text(answered.turnPanel),/Drafted an answer from approved content/);
  await turn("S5",[{stage:"answer_review",result:"revise",issues:["relevance"]},{stage:"outcome",result:"follow_up_question",issues:[]}]);
  await socket.onmessage({data:JSON.stringify({kind:"caption",role:"counsellor",text:"What would you like me to explain?"})});
  const followUp=$("conversation").children.at(-1);
  assert.match(text(followUp.turnPanel),/did not answer the question that was asked/);
  assert.equal(answered.turnPanel.hidden,false,"opening one reply never changes another");
  socket.onmessage({data:JSON.stringify({kind:"complete",ended_by:"persona",state:"explaining",strategy:"S5",suspended:[],
    session_ended:true})});
  assert.equal($("conversation-summary").hidden,false);
  assert.equal($("summary-counts").textContent,"2 guide replies: 1 answered, 1 asked a follow-up question.");
  assert.equal($("summary-turns").children.length,2);
  assert.match(text($("summary-turns").children[1]),/Reply 2: The guide asked a follow-up question instead of answering\./);
  socket.readyState=WebSocket.CLOSED;socket.onclose();
  await connect("simulator");socket.onopen();
  assert.equal($("conversation-summary").hidden,true,"a new conversation starts with a clean log");
  socket.readyState=WebSocket.CLOSED;socket.onclose();
  $("contact-type").value="second_contact";$("contact-type").onchange();
  assert.equal($("simulation").children.length,6);
  assert.ok($("simulation").children.every(option=>option.value.endsWith("-return")));
  assert.equal($("contact-context").hidden,false);
  $("simulation").value="samira-return";
  await connect("simulator");socket.onopen();
  assert.deepEqual(socket.sent[0],{mode:"simulator",simulation_id:"samira-return",contact_type:"second_contact"});
  assert.equal($("contact-type").disabled,true);
  socket.readyState=WebSocket.CLOSED;socket.onclose();
  $("contact-type").value="first_contact";$("contact-type").onchange();
  assert.ok($("simulation").children.every(option=>!option.value.endsWith("-return")));
  assert.equal($("contact-context").hidden,true);
  global.fetch=async()=>({ok:true,json:async()=>({})});
  await assert.rejects(load(),/Sample conversations unavailable/);
  console.log("UX states and evidence passed");
})().catch(error=>{process.stderr.write(error.stack+"\n");process.exitCode=1});
"""
    result = subprocess.run(["node", "-e", harness + script + assertions],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "UX states and evidence passed" in result.stdout, "the DOM harness stopped before its final assertion"


@pytest.mark.parametrize(("statuses", "detail", "delays"), [
    ((200,), "", []),
    ((409, 409, 200), "journey busy; retry the report", [1, 2]),
    ((409, 409, 409, 409), "journey busy; retry the report", [1, 2, 4]),
    ((403,), "step-up verified access to your own report required", []),
    ((409,), "another conflict", []),
])
def test_final_report_retries_only_explicit_transient_contention(monkeypatch, statuses, detail, delays):
    requests, remaining = [], iter(statuses)
    sleep = AsyncMock()
    monkeypatch.setattr(presenter.asyncio, "sleep", sleep)

    def backend(request):
        requests.append(request)
        code = next(remaining)
        return httpx.Response(code, json={"records": []} if code == 200 else {"detail": detail})

    async def scenario():
        async with httpx.AsyncClient(base_url="https://synthetic/", transport=httpx.MockTransport(backend)) as client:
            if statuses[-1] == 200:
                report = await presenter._final_report(client, "synthetic-member", "synthetic-token", "J-synthetic")
                assert report.json() == {"records": []}
            else:
                with pytest.raises(httpx.HTTPStatusError) as failure:
                    await presenter._final_report(client, "synthetic-member", "synthetic-token", "J-synthetic")
                assert failure.value.response.status_code == statuses[-1]

    asyncio.run(scenario())
    assert len(requests) == len(statuses)
    assert [call.args[0] for call in sleep.await_args_list] == delays
    assert all(request.method == "GET"
               and request.url.path == "/members/synthetic-member/explainability-report"
               and request.headers["Authorization"] == "Bearer synthetic-token" for request in requests)


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


def test_a_closing_acknowledgement_is_a_valid_checked_workflow_step():
    reply = VoiceReply(text="You're welcome.", state="resolved",
                       workflow_steps=["screen-input", "intent-agent", "acknowledge-explanation",
                                       "commit-checked-evidence"])
    assert "acknowledge-explanation" in reply.workflow_steps
    with pytest.raises(ContractError):
        VoiceReply(text="You're welcome.", state="resolved", workflow_steps=["unapproved-step"])
