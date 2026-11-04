"""GA Voice Live speech adapter. No agent attachment and no model-generated text."""
from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
from collections.abc import Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from governedgraph.voice.voice_live_bridge import (
    AudioFailure, CUSTOM_VOCABULARY, MAX_AUDIO_BYTES, validate_pcm,
)

API_VERSION = "2026-04-10"
MODEL = "gpt-4.1-mini"
log = logging.getLogger("governedgraph.voice")
_LIFECYCLE = frozenset({
    "session.created", "conversation.item.created", "conversation.item.deleted",
    "input_audio_buffer.cleared", "input_audio_buffer.committed",
    "conversation.item.input_audio_transcription.delta",
})


class VoiceAuthorizationFailure(AudioFailure):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__("Voice Live authorization denied")


class Connection(Protocol):
    async def send(self, event: Mapping) -> None: ...
    async def recv(self) -> Mapping: ...


class VoiceLiveSpeech:
    checked_sentence_audio = True

    def __init__(self, connection: Connection, voice: str, *, timeout: float = 60):
        self.connection, self.voice, self.timeout = connection, voice, timeout
        self.items: set[str] = set()
        self.operation = "configure"

    async def _receive(self, deadline: float) -> Mapping:
        event = await asyncio.wait_for(self.connection.recv(), max(0, deadline - time.monotonic()))
        if event.get("type") == "error":
            error = event.get("error", {})
            code = error.get("code", "") if isinstance(error, Mapping) else ""
            param = error.get("param", "") if isinstance(error, Mapping) else ""
            safe_code = code if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,64}", code) else "unclassified"
            safe_param = param if isinstance(param, str) and re.fullmatch(r"[a-z_.]{1,96}", param) else "unspecified"
            log.error("voice_service_rejected", extra={"failure_domain": f"{self.operation}.{safe_code}.{safe_param}"})
            raise AudioFailure("Voice Live rejected the speech operation")
        if event.get("type") == "conversation.item.created":
            item = event.get("item")
            if isinstance(item, Mapping) and isinstance(item.get("id"), str):
                self.items.add(item["id"])
        if event.get("type") == "conversation.item.deleted":
            self.items.discard(event.get("item_id"))
        return event

    async def configure(self) -> None:
        deadline = time.monotonic() + self.timeout
        await self.connection.send({"type": "session.update", "session": {
            "modalities": ["text", "audio"], "input_audio_format": "pcm16",
            "output_audio_format": "pcm16", "turn_detection": None, "tools": [],
            "input_audio_transcription": {"model": "azure-speech", "language": "en-US",
                                          "phrase_list": list(CUSTOM_VOCABULARY)},
            "voice": {"type": "azure-standard", "name": self.voice},
        }})
        for _ in range(100):
            event = await self._receive(deadline)
            if event.get("type") == "session.updated":
                session = event.get("session")
                if (not isinstance(session, Mapping) or "turn_detection" not in session
                        or session["turn_detection"] is not None):
                    raise AudioFailure("Voice Live must acknowledge disabled automatic responses")
                return
            if event.get("type") not in _LIFECYCLE:
                raise AudioFailure("unexpected Voice Live configuration event")
        raise AudioFailure("Voice Live did not acknowledge configuration")

    async def transcribe(self, pcm: bytes) -> str:
        self.operation = "transcribe"
        validate_pcm(pcm)
        deadline = time.monotonic() + self.timeout
        await self.connection.send({"type": "input_audio_buffer.clear"})
        for offset in range(0, len(pcm), 24000):
            await self.connection.send({"type": "input_audio_buffer.append",
                                        "audio": base64.b64encode(pcm[offset:offset + 24000]).decode("ascii")})
        await self.connection.send({"type": "input_audio_buffer.commit"})
        for _ in range(1000):
            event = await self._receive(deadline)
            if event.get("type") == "conversation.item.input_audio_transcription.completed":
                transcript = event.get("transcript")
                if not isinstance(transcript, str) or not transcript.strip():
                    raise AudioFailure("empty Voice Live transcription")
                if isinstance(event.get("item_id"), str):
                    await self.connection.send({"type": "conversation.item.delete", "item_id": event["item_id"]})
                return transcript
            if event.get("type") not in _LIFECYCLE:
                raise AudioFailure("unexpected response during transcription; no audio may be played")
        raise AudioFailure("Voice Live transcription did not complete")

    async def synthesize(self, text: str) -> bytes:
        self.operation = "synthesize"
        if not isinstance(text, str) or not text.strip() or "{{" in text or len(text) > 16000:
            raise AudioFailure("only bounded final orchestrator text may be synthesized")
        speech_id = uuid4().hex
        deadline = time.monotonic() + self.timeout
        await self.connection.send({"type": "response.create", "response": {
            "modalities": ["text", "audio"], "metadata": {"speech_id": speech_id},
            "pre_generated_assistant_message": {
                "id": speech_id, "type": "message", "role": "assistant",
                "content": [{"type": "text", "text": text}],
            },
        }})
        audio = bytearray()
        response_id = None
        for _ in range(4000):
            event = await self._receive(deadline)
            kind = event.get("type")
            if kind in _LIFECYCLE:
                continue
            if kind == "response.created":
                response = event.get("response")
                metadata = response.get("metadata") if isinstance(response, Mapping) else None
                if (response_id is not None or not isinstance(response, Mapping)
                        or not isinstance(metadata, Mapping) or metadata.get("speech_id") != speech_id
                        or not isinstance(response.get("id"), str)):
                    raise AudioFailure("unbound Voice Live response")
                response_id = response["id"]
                continue
            if response_id is None:
                raise AudioFailure("audio arrived without a bound pre-generated response")
            if event.get("response_id", response_id) != response_id:
                raise AudioFailure("Voice Live response scope mismatch")
            if kind in {"response.output_item.added", "response.output_item.done"}:
                self._check_item(event.get("item"), text)
            if kind in {"response.text.done", "response.audio_transcript.done"}:
                if event.get("text", event.get("transcript")) != text:
                    raise AudioFailure("Voice Live changed the supplied bound text")
            if kind == "response.audio.delta":
                try:
                    audio.extend(base64.b64decode(event.get("delta", ""), validate=True))
                except (ValueError, TypeError) as error:
                    raise AudioFailure("invalid Voice Live audio") from error
                if len(audio) > MAX_AUDIO_BYTES:
                    raise AudioFailure("Voice Live audio exceeds the session bound")
            elif kind == "response.done":
                response = event.get("response")
                if (not isinstance(response, Mapping) or response.get("id") != response_id
                        or response.get("status") != "completed"):
                    raise AudioFailure("Voice Live synthesis did not complete successfully")
                output = response.get("output", [])
                if not isinstance(output, list):
                    raise AudioFailure("invalid Voice Live output list")
                for item in output:
                    self._check_item(item, text)
                if speech_id in self.items:
                    await self.connection.send({"type": "conversation.item.delete", "item_id": speech_id})
                result = bytes(audio)
                validate_pcm(result)
                return result
            elif kind not in {
                "response.output_item.added", "response.output_item.done",
                "response.content_part.added", "response.content_part.done", "response.audio.done",
                "response.audio_transcript.delta", "response.audio_transcript.done",
                "response.text.delta", "response.text.done",
            }:
                raise AudioFailure("unexpected Voice Live generation event")
        raise AudioFailure("Voice Live synthesis exceeded its event bound")

    @staticmethod
    def _check_item(item, text: str) -> None:
        if not isinstance(item, Mapping) or item.get("type") != "message" or item.get("role") != "assistant":
            raise AudioFailure("Voice Live must not invoke tools")
        content = item.get("content", [])
        if not isinstance(content, list):
            raise AudioFailure("invalid Voice Live message content")
        for part in content:
            if not isinstance(part, Mapping) or part.get("type") not in ("audio", "text"):
                raise AudioFailure("unexpected Voice Live output content")
            supplied = part.get("text", part.get("transcript"))
            if supplied is not None and supplied != text:
                raise AudioFailure("Voice Live changed the supplied bound text")


@asynccontextmanager
async def open_speech(endpoint: str, voice: str, *, credential=None):
    target = urlsplit(endpoint)
    if (target.scheme != "https" or not target.hostname or target.netloc != target.hostname
            or not target.hostname.endswith((".services.ai.azure.com", ".cognitiveservices.azure.com"))
            or target.path not in ("", "/") or target.query or target.fragment):
        raise ValueError("a trusted Azure AI Services HTTPS endpoint is required")
    from azure.ai.voicelive.aio import ConnectionError, connect
    from azure.core.exceptions import AzureError
    from azure.identity.aio import DefaultAzureCredential

    owned = credential is None
    if owned:
        credential = DefaultAzureCredential()
    try:
        async with connect(endpoint=endpoint, credential=credential, api_version=API_VERSION,
                           model=MODEL, credential_scopes="https://ai.azure.com/.default") as connection:
            speech = VoiceLiveSpeech(connection, voice)
            await speech.configure()
            yield speech
    except ConnectionError as error:
        status = getattr(error.__cause__, "status", None)
        if isinstance(status, int) and status in (401, 403):
            raise VoiceAuthorizationFailure(status) from error
        raise AudioFailure("Voice Live transport unavailable") from error
    except (AzureError, OSError, TimeoutError) as error:
        raise AudioFailure("Voice Live transport unavailable") from error
    finally:
        if owned:
            await credential.close()


class CheckedSpeechPool:
    """Independent checked sentences may synthesize concurrently, never out of order."""
    checked_sentence_audio = True

    def __init__(self, sessions):
        self.sessions = sessions

    async def synthesize(self, text: str) -> bytes:
        return await self.sessions[0].synthesize(text)

    async def transcribe(self, pcm: bytes) -> str:
        return await self.sessions[0].transcribe(pcm)

    async def synthesize_sentences(self, sentences: list[str]):
        locks = [asyncio.Lock() for _ in self.sessions]

        async def synthesize(index, text):
            slot = index % len(self.sessions)
            async with locks[slot]:
                return await self.sessions[slot].synthesize(text)

        tasks = [asyncio.create_task(synthesize(index, text)) for index, text in enumerate(sentences)]
        try:
            for sentence, task in zip(sentences, tasks):
                yield sentence, await task
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


@asynccontextmanager
async def open_counsellor_speech(endpoint: str, voice: str, *, credential=None):
    async with AsyncExitStack() as stack:
        primary = await stack.enter_async_context(open_speech(endpoint, voice, credential=credential))
        if voice != "en-GB-SoniaNeural":
            yield primary
            return
        sessions = [primary]
        for _ in range(3):
            sessions.append(await stack.enter_async_context(open_speech(endpoint, voice, credential=credential)))
        yield CheckedSpeechPool(sessions)
