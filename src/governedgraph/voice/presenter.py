"""Ephemeral presenter sessions. Member bearers and audio never leave server memory."""
from __future__ import annotations

import asyncio
import json
import hashlib
import html as html_escape
import logging
import os
import re
import time
import traceback
from dataclasses import dataclass
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response

from governedgraph.common import domain
from governedgraph.common.config import DATA_DIR
from governedgraph.common.contracts import CONTACT_TYPES, ContractError, OutcomeSubmission, ResponseReference, _strict
from governedgraph.voice.voice_demo import run_live_simulator, run_simulator
from governedgraph.agents.live_persona import connect_live_persona
from governedgraph.agents.persona_simulator import ScriptedPersona
from governedgraph.agents.counsellor import DISCLOSURE, TEMPLATES
from governedgraph.voice.access import COOKIE, enabled, verify
from governedgraph.voice.azure_voice import open_counsellor_speech as open_speech
from governedgraph.voice.hosted import VoiceScriptRequest
from governedgraph.voice.simulations import load_simulations, select_simulation
from governedgraph.voice.voice_live_bridge import (
    AudioFailure, OrchestratorChannel, PCM_RATE, PERSONA_VOICES, VoiceBridge, VoiceError, validate_pcm,
)

router = APIRouter()
log = logging.getLogger("governedgraph.voice")


@dataclass(frozen=True)
class SessionRequest:
    mode: str
    script: dict | None = None
    benchmark_model: str = "gpt-counsellor"
    simulation_id: str | None = None
    contact_type: str = "first_contact"

    def __post_init__(self):
        if not isinstance(self.contact_type, str) or self.contact_type not in CONTACT_TYPES:
            raise ContractError("unsupported contact type")
        if self.mode not in ("presenter", "simulator", "benchmark", "chat"):
            raise ContractError("unknown voice mode")
        if self.mode == "benchmark" and self.contact_type != "first_contact":
            raise ContractError("benchmark contact type is fixed")
        if self.benchmark_model not in ("gpt-counsellor", "gpt-compact"):
            raise ContractError("unknown pinned voice candidate")
        if self.mode != "benchmark" and self.benchmark_model != "gpt-counsellor":
            raise ContractError("model selection is restricted to the synthetic benchmark")
        if self.mode == "simulator":
            if self.simulation_id is not None:
                if not isinstance(self.simulation_id, str) or self.script is not None:
                    raise ContractError("select a synthetic simulation without custom utterances")
                if select_simulation(self.simulation_id).contact_type != self.contact_type:
                    raise ContractError("persona and selected contact type must match")
            else:
                if not isinstance(self.script, dict):
                    raise ContractError("simulator utterances required")
                VoiceScriptRequest.from_dict(self.script)
                if self.contact_type != "first_contact":
                    raise ContractError("second contact requires a selected synthetic persona")
        elif self.script is not None:
            raise ContractError("presenter sessions accept audio only")
        if self.mode != "simulator" and self.simulation_id is not None:
            raise ContractError("synthetic selection is restricted to simulations")

    @classmethod
    def from_dict(cls, body: dict):
        return _strict(cls, body)


@dataclass(frozen=True)
class ChatMessage:
    kind: str
    request_id: str
    text: str = ""
    outcome: OutcomeSubmission | dict | None = None
    related_answer: ResponseReference | dict | None = None

    def __post_init__(self):
        from governedgraph.orchestrator.store import validate_id

        validate_id(self.request_id)
        if self.kind not in ("chat", "handover", "end", "feedback", "related", "takeover"):
            raise ContractError("unknown chat action")
        if isinstance(self.outcome, dict):
            object.__setattr__(self, "outcome", OutcomeSubmission.from_dict(self.outcome))
        if isinstance(self.related_answer, dict):
            object.__setattr__(self, "related_answer", ResponseReference.from_dict(self.related_answer))
        if self.outcome is not None and not isinstance(self.outcome, OutcomeSubmission):
            raise ContractError("invalid closed outcome")
        if self.related_answer is not None and not isinstance(self.related_answer, ResponseReference):
            raise ContractError("invalid closed answer reference")
        if not isinstance(self.text, str) or len(self.text) > 4000:
            raise ContractError("bounded chat text required")
        if self.kind == "chat" and not self.text.strip():
            raise ContractError("chat text required")
        if self.kind != "chat" and self.text:
            raise ContractError("session actions accept no text")
        if ((self.kind == "feedback") != (self.outcome is not None)
                or (self.related_answer is not None and self.kind not in ("chat", "related"))):
            raise ContractError("feedback and related answers must match their session action")

    @classmethod
    def from_dict(cls, body: dict):
        return _strict(cls, body)


def reply_metadata(reply) -> dict:
    return {"kind": "progress", "state": reply.state, "strategy": reply.strategy_id,
            "suspended": reply.suspended, "ranked": reply.ranked, "snapshot_id": reply.snapshot_id,
            "audit_status": reply.audit_status, "request_id": reply.request_id, "arm": reply.arm,
            "journey_id": reply.journey_id, "workflow_steps": reply.workflow_steps,
            "composition_source": reply.composition_source, "turn_trace": reply.turn_trace,
            "escalated": reply.escalated,
            **({"response_id": reply.response_id} if reply.response_id is not None else {}),
            **({"outcome_offer": reply.outcome_offer} if reply.outcome_offer is not None else {}),
            **({"learning_feedback": reply.learning_feedback} if reply.learning_feedback is not None else {})}


async def serve_chat(socket: WebSocket, channel: OrchestratorChannel, *, contact_type: str = "first_contact"):
    """Same authorized endpoints, without opening a microphone or speech connection."""
    started = await channel.start(channel="chat",
                                  **({"contact_type": contact_type} if contact_type != "first_contact" else {}))
    await socket.send_json({"kind": "caption", "role": "counsellor", "text": started.text})
    await socket.send_json({"kind": "chat_ready", "journey_id": started.journey_id})
    seen: dict[str, ChatMessage] = {}
    for _ in range(32):
        message = ChatMessage.from_dict(await asyncio.wait_for(socket.receive_json(), 300))
        if message.kind == "end":
            await socket.send_json({"kind": "session_ended"})
            return
        if message.request_id in seen and seen[message.request_id] != message:
            raise ContractError("idempotency key cannot be reused for different text")
        seen[message.request_id] = message
        await socket.send_json({"kind": "thinking"})
        if message.kind == "feedback":
            if not isinstance(message.outcome, OutcomeSubmission):
                raise ContractError("feedback requires an outcome")
            receipt = await channel.record_outcome(started.journey_id, message.outcome, message.request_id)
            await socket.send_json({"kind": "outcome_recorded", "response_id": message.outcome.response_id,
                                    "duplicate": receipt.duplicate})
            await socket.send_json({"kind": "chat_ready", "journey_id": started.journey_id,
                                    "duplicate": receipt.duplicate, "request_id": message.request_id})
            continue
        if message.kind == "related":
            raise ContractError("chat references accompany the next question")
        reply = await channel.turn(started.journey_id,
                                   "I want a human advisor" if message.kind == "handover" else message.text,
                                   message.request_id,
                                   **({"related_answer": message.related_answer}
                                      if isinstance(message.related_answer, ResponseReference) else {}))
        if reply.journey_id is not None and reply.journey_id != started.journey_id:
            raise ContractError("chat response journey mismatch")
        await socket.send_json(reply_metadata(reply))
        if not reply.duplicate:
            await socket.send_json({"kind": "caption", "role": "counsellor", "text": reply.text})
        await socket.send_json({"kind": "chat_ready", "journey_id": started.journey_id,
                                "duplicate": reply.duplicate, "request_id": message.request_id})
        if reply.escalated or reply.state == "resolved":
            await socket.send_json({"kind": "chat_complete", "state": reply.state})
            if reply.escalated:
                return


def require_origin(origin: str) -> None:
    if not enabled():
        raise HTTPException(404, "dev voice demo disabled")
    if not os.getenv("VOICE_DEMO_ORIGIN") or origin != os.environ["VOICE_DEMO_ORIGIN"]:
        raise HTTPException(403, "demo origin required")


@router.get("/demo", response_class=HTMLResponse, include_in_schema=False)
@router.get("/demo/", response_class=HTMLResponse)
def index():
    if not enabled():
        raise HTTPException(404, "dev voice demo disabled")
    html = (DATA_DIR / "catalogue" / "ui" / "presenter.html").read_text().replace(
        "{{AI_DISCLOSURE}}", html_escape.escape(DISCLOSURE))
    import base64
    hashes = {}
    for tag in ("script", "style"):
        match = re.search(f"<{tag}>(.*?)</{tag}>", html, re.S)
        if match is None:
            log.error("voice_presenter_template_invalid", extra={"missing_tag": tag})
            raise HTTPException(503, "dev voice template unavailable")
        hashes[tag] = base64.b64encode(hashlib.sha256(match[1].encode()).digest()).decode()
    policy = (f"default-src 'none'; script-src 'sha256-{hashes['script']}'; "
              f"style-src 'sha256-{hashes['style']}'; connect-src 'self' "
              f"https://login.microsoftonline.com {os.environ['VOICE_DEMO_ORIGIN'].replace('https:', 'wss:')}; "
              "media-src blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
    return HTMLResponse(html,
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                 "X-Content-Type-Options": "nosniff", "Content-Security-Policy": policy})


@router.get("/demo/config")
def config():
    if not enabled():
        raise HTTPException(404, "dev voice demo disabled")
    return JSONResponse({"client_id": os.environ["VOICE_DEMO_CLIENT_ID"],
                         "tenant": os.environ["STRATEGY_TENANT_ID"],
                         "origin": os.environ["VOICE_DEMO_ORIGIN"],
                         "simulations": [item.summary() for item in load_simulations()]},
                        headers={"Cache-Control": "no-store"})


@router.post("/demo/authorize")
def authorize(request: Request, authorization: str = Header("")):
    require_origin(request.headers.get("origin", ""))
    if request.headers.get("content-length", "0") != "0" or request.headers.get("transfer-encoding"):
        raise HTTPException(400, "authorization accepts no payload")
    token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
    verify(token)
    response = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    response.set_cookie(COOKIE, token, secure=True, httponly=True, samesite="strict", path="/demo-stream")
    return response


@router.delete("/demo/authorize")
def logout(request: Request):
    require_origin(request.headers.get("origin", ""))
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    response.delete_cookie(COOKIE, path="/demo-stream", secure=True, httponly=True, samesite="strict")
    return response


class HandoverRequested(VoiceError):
    pass


class SessionEnded(Exception):
    pass


class OutcomeSelected(Exception):
    def __init__(self, submission: OutcomeSubmission, request_id: str):
        self.submission = submission
        self.request_id = request_id


class SocketAudio:
    confirms_playback = True

    def __init__(self, socket: WebSocket):
        self.socket = socket
        self.caption = {"role": "counsellor", "text": ""}
        self.queued = False
        self.pending_playback = []
        self.handover_requested = False
        self.takeover_requested = False
        self.pending_related: ResponseReference | None = None

    async def playback_ack(self, timeout: float):
        async with asyncio.timeout(timeout):
            while True:
                ack = await self.socket.receive_json()
                if isinstance(ack, dict) and ack.get("kind") == "handover":
                    ChatMessage.from_dict(ack)
                    self.handover_requested = True
                    continue
                if isinstance(ack, dict) and ack.get("kind") == "takeover":
                    ChatMessage.from_dict(ack)
                    self.takeover_requested = True
                    continue
                return ack

    async def play(self, pcm: bytes):
        validate_pcm(pcm)
        item = uuid4().hex
        await self.socket.send_json({"kind": "audio", "id": item, **self.caption})
        await self.socket.send_bytes(pcm)
        if self.queued:
            self.pending_playback.append((item, len(pcm) / (PCM_RATE * 2)))
            return
        ack = await self.playback_ack(len(pcm) / (PCM_RATE * 2) + 15)
        if ack != {"kind": "played", "id": item}:
            raise AudioFailure("playback acknowledgement required")

    def begin_reply(self):
        if self.queued or self.pending_playback:
            raise AudioFailure("overlapping response playback")
        self.queued = True

    async def finish_reply(self):
        deadline = asyncio.get_running_loop().time() + sum(duration for _, duration in self.pending_playback) + 15
        for item, _ in self.pending_playback:
            ack = await self.playback_ack(max(0, deadline - asyncio.get_running_loop().time()))
            if ack != {"kind": "played", "id": item}:
                raise AudioFailure("ordered playback acknowledgement required")
        self.pending_playback.clear()
        self.queued = False

    async def play_text(self, text: str, pcm: bytes, *, final_sentence: bool = True):
        self.caption = {"role": "counsellor", "text": text,
                        "final_sentence": final_sentence,
                        "acknowledgement": text == TEMPLATES["acknowledgements"]["voice_checking"]}
        await self.play(pcm)

    async def response_metadata(self, reply):
        await self.socket.send_json(reply_metadata(reply))

    async def outcome_recorded(self, response_id: str, duplicate: bool):
        await self.socket.send_json({"kind": "outcome_recorded", "response_id": response_id,
                                    "duplicate": duplicate})

    async def capture(self):
        if self.handover_requested:
            raise HandoverRequested("member requested a person")
        await self.socket.send_json({"kind": "capture"})
        if hasattr(self.socket, "receive"):
            while True:
                message = await asyncio.wait_for(self.socket.receive(), 300)
                if message["type"] == "websocket.disconnect":
                    raise WebSocketDisconnect(message.get("code", 1000))
                if message.get("text") is None:
                    pcm = message.get("bytes")
                    break
                action = ChatMessage.from_dict(json.loads(message["text"]))
                if action.kind == "end":
                    raise SessionEnded("member ended the session")
                if action.kind == "handover":
                    raise HandoverRequested("member requested a person")
                if action.kind == "feedback" and isinstance(action.outcome, OutcomeSubmission):
                    raise OutcomeSelected(action.outcome, action.request_id)
                if action.kind == "related":
                    self.pending_related = (action.related_answer
                                            if isinstance(action.related_answer, ResponseReference) else None)
                    await self.socket.send_json({"kind": "related_selected", "selected": self.pending_related is not None})
                    continue
                raise ContractError("unsupported voice action")
        else:
            pcm = await asyncio.wait_for(self.socket.receive_bytes(), 35)
        if not isinstance(pcm, bytes):
            raise AudioFailure("presenter input must be PCM audio")
        validate_pcm(pcm)
        if len(pcm) > PCM_RATE * 2 * 20:
            raise AudioFailure("presenter input exceeds twenty seconds")
        return pcm


class CaptionedSpeech:
    checked_sentence_audio = True
    def __init__(self, speech, audio: SocketAudio, role: str, *, transcribe_captions: bool = True):
        self.speech, self.audio, self.role = speech, audio, role
        self.transcribe_captions = transcribe_captions

    async def synthesize(self, text):
        pcm = await self.speech.synthesize(text)
        self.audio.caption = {"role": self.role, "text": text}
        return pcm

    async def transcribe(self, pcm):
        text = await self.speech.transcribe(pcm)
        if self.transcribe_captions:
            await self.audio.socket.send_json({"kind": "caption", "role": "member", "text": text})
        return text

    async def synthesize_sentences(self, sentences):
        if hasattr(self.speech, "synthesize_sentences"):
            async for item in self.speech.synthesize_sentences(sentences):
                yield item
        else:
            for sentence in sentences:
                yield sentence, await self.speech.synthesize(sentence)


async def member_bearer(credential, client: httpx.AsyncClient, *, member_ref: str | None = None) -> str:
    from azure.core.exceptions import AzureError

    audience = os.environ["VOICE_DEMO_CLIENT_ID"]
    try:
        auth = await credential.get_token(f"api://{audience}/.default")
    except AzureError as error:
        log.error("voice_issuer_identity_failed", extra={"error_type": type(error).__name__})
        raise VoiceError("internal issuer authorization unavailable") from error
    result = await client.post(os.environ["VOICE_DEMO_ISSUER_URL"].rstrip("/") + "/issue",
                               headers={"Authorization": "Bearer " + auth.token},
                               json={"member_ref": member_ref} if member_ref is not None else None)
    result.raise_for_status()
    payload = result.json()
    if set(payload) != {"token"} or not isinstance(payload["token"], str) or not payload["token"]:
        raise VoiceError("invalid internal issuer response")
    return payload["token"]


async def _final_report(client: httpx.AsyncClient, member_ref: str, token: str, journey_id: str) -> httpx.Response:
    path = f"members/{member_ref}/explainability-report"
    headers = {"Authorization": "Bearer " + token}
    response = await client.get(path, headers=headers)
    for delay in (1, 2, 4):
        if response.status_code != 409:
            break
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("detail") != "journey busy; retry the report":
            break
        log.warning("voice_presenter_report_busy", extra={"journey_id": journey_id})
        await asyncio.sleep(delay)
        response = await client.get(path, headers=headers)
    response.raise_for_status()
    return response


async def run_voice_turns(bridge: VoiceBridge, audio: SocketAudio, replies: list) -> None:
    for _ in range(8):
        try:
            pcm = await audio.capture()
        except HandoverRequested:
            reply = await bridge.channel.turn(bridge.journey_id, "I want a human advisor", uuid4().hex)
            if not reply.escalated:
                raise VoiceError("human handover was not confirmed")
            replies.append(reply)
            await audio.response_metadata(reply)
            await bridge._speak(reply.text)
            break
        except SessionEnded:
            break
        except OutcomeSelected as selection:
            await bridge.submit_outcome(selection.submission, selection.request_id)
            continue
        related = audio.pending_related
        audio.pending_related = None
        reply = await bridge.turn(pcm, related_answer=related)
        if reply.feedback_recorded:
            audio.pending_related = related
            continue
        replies.append(reply)
        if not audio.handover_requested and replies[-1].escalated:
            break


async def run_chat_turns(socket: WebSocket, bridge: VoiceBridge, audio: SocketAudio, replies: list) -> bool:
    """The presenter takes over a chat simulation as the member; returns whether the member ended it."""
    await socket.send_json({"kind": "chat_ready", "journey_id": bridge.journey_id})
    for _ in range(16):
        message = ChatMessage.from_dict(await asyncio.wait_for(socket.receive_json(), 300))
        if message.kind == "takeover":
            continue
        if message.kind == "end":
            return True
        if message.kind == "feedback":
            if not isinstance(message.outcome, OutcomeSubmission):
                raise ContractError("feedback requires an outcome")
            await bridge.submit_outcome(message.outcome, message.request_id)
            await socket.send_json({"kind": "chat_ready", "journey_id": bridge.journey_id})
            continue
        if message.kind == "related":
            raise ContractError("chat references accompany the next question")
        await socket.send_json({"kind": "thinking"})
        if message.kind == "handover":
            reply = await bridge.channel.turn(bridge.journey_id, "I want a human advisor", message.request_id)
            if not reply.escalated:
                raise VoiceError("human handover was not confirmed")
            replies.append(reply)
            await audio.response_metadata(reply)
            await bridge._speak(reply.text)
            return False
        reply = await bridge.turn_text(
            message.text, request_id=message.request_id,
            **({"related_answer": message.related_answer}
               if isinstance(message.related_answer, ResponseReference) else {}))
        if reply.feedback_recorded:
            await socket.send_json({"kind": "chat_ready", "journey_id": bridge.journey_id})
            continue
        replies.append(reply)
        if reply.escalated:
            await socket.send_json({"kind": "chat_complete", "state": reply.state})
            return False
        await socket.send_json({"kind": "chat_ready", "journey_id": bridge.journey_id})
    return False


async def serve(socket: WebSocket, claims: dict, *, request: SessionRequest | None = None,
                channel_factory: Callable[[httpx.AsyncClient, Callable[[], str]], OrchestratorChannel] | None = None):
    from azure.identity.aio import ManagedIdentityCredential

    if channel_factory is None:
        channel_factory = OrchestratorChannel
    async with asyncio.timeout(min(850, max(0, claims["exp"] - time.time()))):
        if request is None:
            request = SessionRequest.from_dict(await asyncio.wait_for(socket.receive_json(), 20))
        simulation = select_simulation(request.simulation_id) if request.simulation_id is not None else None
        member_ref = simulation.member_ref if simulation else domain.demo()["member_ref"]
        async with ManagedIdentityCredential(client_id=os.environ["AZURE_CLIENT_ID"]) as credential:
            async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
                token = await member_bearer(credential, client, **({"member_ref": member_ref} if simulation else {}))
                if request.mode == "chat":
                    async with httpx.AsyncClient(base_url=os.environ["GOVERNEDGRAPH_ORCHESTRATOR_URL"].rstrip("/") + "/",
                                                 timeout=120, follow_redirects=False) as channel:
                        await serve_chat(socket, channel_factory(channel, lambda: token),
                                         contact_type=request.contact_type)
                    return
                if request.mode == "benchmark":
                    from governedgraph.voice.benchmark import run_benchmark

                    async with httpx.AsyncClient(base_url=os.environ["GOVERNEDGRAPH_ORCHESTRATOR_URL"].rstrip("/") + "/",
                                                 timeout=120, follow_redirects=False) as channel:
                        await run_benchmark(socket, credential, channel, token,
                                            os.environ["AZURE_VOICE_LIVE_ENDPOINT"],
                                            runtime=socket.app.state.runtime, model=request.benchmark_model)
                    return
                live = (await asyncio.to_thread(connect_live_persona, simulation.card())
                        if request.mode == "simulator" and simulation else None)
                from governedgraph.voice.warmup import warm_runtime

                await asyncio.to_thread(warm_runtime)
                guide_voice = ((simulation.card().guide_voice if simulation else None)
                               or PERSONA_VOICES["counsellor"])
                async with open_speech(os.environ["AZURE_VOICE_LIVE_ENDPOINT"],
                                       guide_voice, credential=credential) as speech:
                    audio = SocketAudio(socket)
                    async with httpx.AsyncClient(base_url=os.environ["GOVERNEDGRAPH_ORCHESTRATOR_URL"].rstrip("/") + "/",
                                                 timeout=120, follow_redirects=False) as channel:
                        bridge = VoiceBridge(channel_factory(channel, lambda: token),
                                             CaptionedSpeech(speech, audio, "counsellor",
                                                             transcribe_captions=request.mode != "simulator"), audio,
                                             spoken_acknowledgement=False)
                        try:
                            session_ended = False
                            ended_by = "presenter"
                            if request.mode == "simulator":
                                actor_id = simulation.simulation_id if simulation else domain.demo()["persona"]
                                actor_voice = (simulation.card().voice if simulation else
                                               PERSONA_VOICES.get(actor_id, PERSONA_VOICES[domain.demo()["persona"]]))
                                async with open_speech(os.environ["AZURE_VOICE_LIVE_ENDPOINT"],
                                                       actor_voice, credential=credential) as actor:
                                    if simulation is not None and live is not None:
                                        result = await run_live_simulator(
                                            bridge, CaptionedSpeech(actor, audio, "persona"), live,
                                            channel=simulation.channel,
                                            accessibility_mode=simulation.accessibility_mode,
                                            contact_type=simulation.contact_type,
                                            takeover=lambda: audio.takeover_requested,
                                        )
                                    else:
                                        if request.script is None:
                                            raise ContractError("simulator utterances required")
                                        result = await run_simulator(
                                            bridge, CaptionedSpeech(actor, audio, "persona"),
                                            script=ScriptedPersona(request.script), channel="voice",
                                        )
                                    replies = result.replies
                                    session_ended = result.session_ended
                                    ended_by = result.ended_by
                                if ended_by == "takeover" and simulation is not None:
                                    await socket.send_json({"kind": "takeover_ready", "channel": simulation.channel})
                                    if simulation.channel == "chat":
                                        session_ended = await run_chat_turns(socket, bridge, audio, replies)
                                    else:
                                        await run_voice_turns(bridge, audio, replies)
                            else:
                                replies = []
                                await bridge.start(contact_type=request.contact_type)
                                await run_voice_turns(bridge, audio, replies)
                            if audio.handover_requested and not any(reply.escalated for reply in replies):
                                reply = await bridge.channel.turn(bridge.journey_id, "I want a human advisor",
                                                                  uuid4().hex)
                                if not reply.escalated:
                                    raise VoiceError("human handover was not confirmed")
                                replies.append(reply)
                                await audio.response_metadata(reply)
                                await bridge._speak(reply.text)
                            audit = await _final_report(channel, member_ref, token, bridge.journey_id)
                            rows = [row for row in audit.json()["records"] if row["journey_id"] == bridge.journey_id]
                            session_ended = session_ended and not audio.handover_requested
                            if session_ended:
                                await socket.send_json({"kind": "session_ended"})
                            await socket.send_json({
                                "kind": "complete", "journey_id": bridge.journey_id, "metrics": bridge.metrics(),
                                "session_ended": session_ended, "ended_by": ended_by,
                                "state": replies[-1].state if replies else "",
                                "strategy": replies[0].strategy_id if replies else None,
                                "suspended": replies[0].suspended if replies else [],
                                "response_ids": [row["response_id"] for row in rows],
                                "audit_written": len(rows) == len(replies) + 1
                                and all(row["response_id"] in audit.json()["delivered_response_ids"] for row in rows),
                                "numeric_fidelity": all(row["numeric_fidelity_passed"] for row in rows),
                            })
                        except asyncio.CancelledError:
                            await asyncio.shield(bridge.fail_audio())
                            raise
                        except (VoiceError, httpx.HTTPError, ContractError, TimeoutError, OSError, WebSocketDisconnect):
                            await bridge.fail_audio()
                            raise


@router.websocket("/demo-stream")
async def stream(socket: WebSocket):
    try:
        require_origin(socket.headers.get("origin", ""))
        if socket.query_params:
            raise HTTPException(400, "query credentials are forbidden")
        claims = await asyncio.to_thread(verify, socket.cookies.get(COOKIE, ""))
    except HTTPException:
        await socket.close(code=1008)
        return
    await socket.accept()
    try:
        await serve(socket, claims)
    except (VoiceError, httpx.HTTPError, ContractError, TimeoutError, OSError,
            WebSocketDisconnect, json.JSONDecodeError, KeyError, ValueError) as error:
        frame = traceback.extract_tb(error.__traceback__)[-1]
        location = f"{Path(frame.filename).stem}.{frame.name}.L{frame.lineno}"
        log.error("voice_presenter_failed", extra={"error_type": type(error).__name__,
                                                  "failure_domain": location})
        if socket.client_state.name == "CONNECTED":
            await socket.send_json({"kind": "error", "message": domain.message("conversation_unavailable"),
                                    "error_type": type(error).__name__, "location": location})
    finally:
        if socket.client_state.name == "CONNECTED":
            await socket.close()
