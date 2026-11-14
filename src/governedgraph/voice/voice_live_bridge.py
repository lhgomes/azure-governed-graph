"""Governed voice channel: STT -> the chat HTTP endpoints -> bound-text TTS."""
from __future__ import annotations

import math
import asyncio
import re
import time
from dataclasses import asdict, dataclass, field
from typing import AsyncIterator, Callable, Protocol, runtime_checkable
from uuid import uuid4

import httpx

from governedgraph.agents.counsellor import DISCLOSURE, TEMPLATES
from governedgraph.common import domain
from governedgraph.common.contracts import (
    ContractError, OutcomeOffer, OutcomeSubmission, ResponseReference, _strict,
)
from governedgraph.observability import headers as journey_headers
from governedgraph.orchestrator.store import validate_id

ORCHESTRATOR_URL_ENV = "GOVERNEDGRAPH_ORCHESTRATOR_URL"
VOICE_LIVE_ENDPOINT_ENV = "AZURE_VOICE_LIVE_ENDPOINT"
CUSTOM_VOCABULARY = domain.vocabulary()["speech_custom_vocabulary"]
PERSONA_VOICES = domain.demo()["persona_voices"]
PCM_RATE = 24000
MAX_AUDIO_BYTES = PCM_RATE * 2 * 120
ACKNOWLEDGEMENT = TEMPLATES["acknowledgements"]["voice_checking"]


def checked_sentences(text: str) -> list[str]:
    """Partition, never rewrite, the already fully gated response; preserve decimals."""
    sentences = re.split(r"(?<=[.!?])(?=\s+[A-Z])", text)
    if not text.strip() or "{{" in text or len(sentences) > 128:
        raise AudioFailure("bounded, fully checked text required")
    return sentences


class VoiceError(RuntimeError):
    """Voice failed explicitly; never report text fallback as a voice acceptance."""


class AudioFailure(VoiceError):
    pass


@dataclass(frozen=True)
class JourneyStart:
    journey_id: str
    text: str
    state: str

    def __post_init__(self):
        validate_id(self.journey_id)
        if self.text != DISCLOSURE or not isinstance(self.state, str) or not self.state:
            raise ContractError("the authenticated orchestrator must provide the AI disclosure")

    @classmethod
    def from_dict(cls, body: dict) -> JourneyStart:
        return _strict(cls, body)


@dataclass(frozen=True)
class VoiceReply:
    text: str = ""
    state: str = ""
    strategy_id: str | None = None
    escalated: bool = False
    blocked_reason: str | None = None
    ranked: list[str] = field(default_factory=list)
    suspended: list[str] = field(default_factory=list)
    arm: str | None = None
    snapshot_id: str | None = None
    audit_status: str | None = None
    journey_id: str | None = None
    request_id: str | None = None
    duplicate: bool = False
    stage_ms: dict[str, float] = field(default_factory=dict)
    model_rounds: int = 0
    composition_source: str = "template"
    numeric_fidelity_passed: bool = True
    no_option_recommendation: bool = True
    workflow_steps: list[str] = field(default_factory=list)
    turn_trace: list[dict] = field(default_factory=list)
    learning_feedback: dict | None = None
    outcome_offer: dict | None = None
    response_id: str | None = None
    feedback_recorded: bool = False

    def __post_init__(self):
        if self.response_id is not None:
            if not isinstance(self.journey_id, str):
                raise ContractError("answer reference requires its journey")
            ResponseReference(self.journey_id, self.response_id)
            if (self.audit_status not in ("committed", "queued") or self.escalated or self.duplicate
                    or self.strategy_id is None):
                raise ContractError("answer reference requires an audited delivered explanation")
        if self.outcome_offer is not None:
            offer = OutcomeOffer.from_dict(self.outcome_offer)
            if (self.escalated or self.duplicate or self.feedback_recorded
                    or self.audit_status not in ("committed", "queued")
                    or not isinstance(self.text, str)
                    or not self.text.endswith(offer.question)):
                raise ContractError("outcome offer requires an audited explanation with approved wording")
            object.__setattr__(self, "outcome_offer", asdict(offer))
        if self.learning_feedback is not None:
            from governedgraph.learning.feedback import FeedbackManifest

            try:
                manifest = FeedbackManifest.from_dict(self.learning_feedback)
            except (TypeError, ValueError) as error:
                raise ContractError("invalid closed learning feedback metadata") from error
            object.__setattr__(self, "learning_feedback", manifest.to_dict())
        if (not isinstance(self.workflow_steps, list)
                or any(step not in ("screen-input", "intent-agent", "understanding-agent",
                                    "explain-with-approved-tools", "clarify-question",
                                    "requested-human-handoff", "personal-advice-handoff",
                                    "service-failure-handoff", "commit-checked-evidence")
                       for step in self.workflow_steps)):
            raise ContractError("invalid closed workflow evidence")
        self._validate_turn_trace()
        allowed_stages = {"screening", "intent_routing", "understanding_assessment", "values_lookup", "strategy_lookup",
                          "content_lookup", "document_screening", "foundry_draft", "foundry_requests",
                          "foundry_tools", "foundry_review", "post_processing", "audit_commit", "durable_load", "durable_commit"}
        if (not isinstance(self.stage_ms, dict) or set(self.stage_ms) - allowed_stages
                or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in self.stage_ms.values())
                or type(self.model_rounds) is not int or self.model_rounds < 0):
            raise ContractError("invalid closed voice timing metadata")
        if self.composition_source not in ("template", "foundry", "template_fallback", "unanswered"):
            raise ContractError("invalid composition metadata")
        if self.composition_source == "unanswered" and (
                self.strategy_id is not None or self.outcome_offer is not None or self.response_id is not None):
            raise ContractError("an unanswered turn cannot claim a strategy or offer answer feedback")
        if self.numeric_fidelity_passed is not True or self.no_option_recommendation is not True:
            raise ContractError("voice quality gates failed")
        if (type(self.duplicate) is not bool or type(self.escalated) is not bool
                or type(self.feedback_recorded) is not bool):
            raise ContractError("invalid voice response flags")
        for name in ("strategy_id", "blocked_reason", "arm", "snapshot_id", "audit_status", "journey_id", "request_id"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ContractError("voice metadata must contain only optional strings")
        if any(not isinstance(items, list) or any(not isinstance(item, str) for item in items)
               for items in (self.ranked, self.suspended)):
            raise ContractError("voice strategy references must be string lists")
        if not isinstance(self.text, str) or not isinstance(self.state, str) or not self.state:
            raise ContractError("invalid orchestrator voice response")
        if self.feedback_recorded and (self.text or self.escalated or self.outcome_offer is not None
                                       or self.response_id is not None):
            raise ContractError("a feedback receipt is not a spoken answer")
        if not self.duplicate and not self.feedback_recorded and (not self.text.strip() or "{{" in self.text):
            raise ContractError("voice requires final bound orchestrator text")
        if self.audit_status not in (None, "committed", "queued"):
            raise ContractError("an unaudited response must not be spoken")

    def _validate_turn_trace(self) -> None:
        code = re.compile(r"[a-z][a-z_]{0,39}")
        stages = {"intent", "answer_review", "answer_repair", "output_gate", "outcome", "rate_limit"}
        if (not isinstance(self.turn_trace, list) or len(self.turn_trace) > 16
                or any(not isinstance(item, dict) or set(item) != {"stage", "result", "issues"}
                       or item["stage"] not in stages
                       or not isinstance(item["result"], str) or not code.fullmatch(item["result"])
                       or not isinstance(item["issues"], list) or len(item["issues"]) > 6
                       or any(not isinstance(issue, str) or not code.fullmatch(issue) for issue in item["issues"])
                       for item in self.turn_trace)):
            raise ContractError("invalid closed turn evidence")

    @classmethod
    def from_dict(cls, body: dict) -> VoiceReply:
        if isinstance(body, dict) and body.get("feedback_recorded"):
            raise ContractError("the orchestrator cannot return an internal feedback receipt as an answer")
        return _strict(cls, body)


@dataclass(frozen=True)
class OutcomeReceipt:
    journey_id: str
    state: str
    duplicate: bool

    def __post_init__(self):
        validate_id(self.journey_id)
        if not isinstance(self.state, str) or not self.state or type(self.duplicate) is not bool:
            raise ContractError("invalid outcome receipt")

    @classmethod
    def from_dict(cls, body: dict) -> OutcomeReceipt:
        return _strict(cls, body)


class OrchestratorChannel:
    """Token is obtained from the authenticated channel host, never from the model or a file."""

    def __init__(self, client: httpx.AsyncClient, token: Callable[[], str]):
        if client.base_url.scheme != "https" or client.follow_redirects:
            raise ValueError("voice requires HTTPS and disabled redirects")
        self.client = client
        self.token = token

    def _headers(self) -> dict[str, str]:
        token = self.token()
        if not isinstance(token, str) or not token or any(c.isspace() for c in token):
            raise VoiceError("an authenticated channel token is required")
        return {"Authorization": "Bearer " + token}

    async def start(self, accessibility_mode: str = "standard", *, channel: str = "voice") -> JourneyStart:
        if channel not in ("voice", "chat"):
            raise ContractError("unsupported presenter channel")
        result = await self.client.post("journeys", headers=self._headers(),
                                        json={"channel": channel, "accessibility_mode": accessibility_mode})
        result.raise_for_status()
        return JourneyStart.from_dict(result.json())

    async def turn(self, journey_id: str, transcript: str, request_id: str, *,
                   related_answer: ResponseReference | None = None) -> VoiceReply:
        validate_id(journey_id)
        validate_id(request_id)
        headers = {**self._headers(), "Idempotency-Key": request_id, **journey_headers(journey_id)}
        body: dict[str, object] = {"utterance": transcript}
        if related_answer is not None:
            body["related_answer"] = asdict(related_answer)
        result = await self.client.post(f"journeys/{journey_id}/turns", headers=headers, json=body)
        result.raise_for_status()
        return VoiceReply.from_dict(result.json())

    async def record_outcome(self, journey_id: str, submission: OutcomeSubmission,
                             request_id: str) -> OutcomeReceipt:
        validate_id(journey_id)
        validate_id(request_id)
        headers = {**self._headers(), "Idempotency-Key": request_id, **journey_headers(journey_id)}
        result = await self.client.post(
            f"journeys/{journey_id}/turns", headers=headers,
            json={"utterance": "", "outcome": asdict(submission)},
        )
        result.raise_for_status()
        receipt = OutcomeReceipt.from_dict(result.json())
        if receipt.journey_id != journey_id:
            raise ContractError("outcome receipt journey mismatch")
        return receipt

    async def confirm_playback(self, journey_id: str, response_id: str, request_id: str) -> OutcomeReceipt:
        ResponseReference(journey_id, response_id)
        validate_id(request_id)
        headers = {**self._headers(), "Idempotency-Key": request_id, **journey_headers(journey_id)}
        result = await self.client.post(
            f"journeys/{journey_id}/turns", headers=headers,
            json={"utterance": "", "playback_response_id": response_id},
        )
        result.raise_for_status()
        receipt = OutcomeReceipt.from_dict(result.json())
        if receipt.journey_id != journey_id:
            raise ContractError("playback receipt journey mismatch")
        return receipt

class Speech(Protocol):
    async def synthesize(self, text: str) -> bytes: ...
    async def transcribe(self, pcm: bytes) -> str: ...


class AudioDevice(Protocol):
    async def play(self, pcm: bytes) -> None: ...
    async def capture(self) -> bytes: ...


@runtime_checkable
class OutcomeAwareAudioDevice(Protocol):
    async def outcome_recorded(self, response_id: str, duplicate: bool) -> None: ...


@runtime_checkable
class CaptionedAudioDevice(Protocol):
    async def play_text(self, text: str, pcm: bytes, *, final_sentence: bool = True) -> None: ...


@runtime_checkable
class QueuedAudioDevice(Protocol):
    def begin_reply(self) -> None: ...
    async def finish_reply(self) -> None: ...


@runtime_checkable
class MetadataAudioDevice(Protocol):
    async def response_metadata(self, reply: VoiceReply) -> None: ...


@runtime_checkable
class SentenceSpeech(Protocol):
    def synthesize_sentences(self, sentences: list[str]) -> AsyncIterator[tuple[str, bytes]]: ...


def validate_pcm(pcm: bytes) -> None:
    if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2 or len(pcm) > MAX_AUDIO_BYTES:
        raise AudioFailure("bounded nonempty PCM16 mono audio is required")


class VoiceBridge:
    def __init__(self, channel: OrchestratorChannel, speech: Speech, device: AudioDevice, *,
                 clock: Callable[[], float] = time.perf_counter, spoken_acknowledgement: bool = True):
        if type(spoken_acknowledgement) is not bool:
            raise ValueError("spoken acknowledgement must be a boolean")
        self.channel, self.speech, self.device = channel, speech, device
        self.spoken_acknowledgement = spoken_acknowledgement
        self.clock = clock
        self.journey_id = ""
        self.disclosed = False
        self.failed = False
        self.started = False
        self.inflight = False
        self.turn_seconds: list[float] = []
        self.first_audio_seconds: list[float] = []
        self.stage_samples: list[dict[str, float]] = []
        self.acknowledgement_audio: bytes | None = None
        self.substantive_first_seconds: list[float] = []
        self.acknowledgement_seconds: list[float] = []
        self.input_modes: set[str] = set()
        self.pending_outcome: OutcomeOffer | None = None
        self.confirmed_responses: set[str] = set()
        self.channel_kind = ""

    async def start(self, accessibility_mode: str = "standard", *, channel: str = "voice") -> None:
        if self.started or self.failed:
            raise VoiceError("voice session cannot be restarted or reused after failure")
        self.started = True
        try:
            started = await self.channel.start(accessibility_mode, channel=channel)
            self.journey_id = started.journey_id
            self.channel_kind = channel
            if self.spoken_acknowledgement and getattr(self.speech, "checked_sentence_audio", False):
                from governedgraph.guardrails.numeric_fidelity import check
                from governedgraph.guardrails.advice_intent import response_steers_choice

                if not check(ACKNOWLEDGEMENT, None, []).passed or response_steers_choice(ACKNOWLEDGEMENT):
                    raise AudioFailure("approved acknowledgement failed its gates")
                self.acknowledgement_audio = await self.speech.synthesize(ACKNOWLEDGEMENT)
                validate_pcm(self.acknowledgement_audio)
            await self._speak(started.text)
        except (VoiceError, httpx.HTTPError, ContractError, OSError, TimeoutError):
            self.failed = True
            raise
        self.disclosed = True

    async def _speak(self, text: str) -> None:
        try:
            pcm = await self.speech.synthesize(text)
            validate_pcm(pcm)
            await self._play(text, pcm)
        except (AudioFailure, OSError, TimeoutError):
            await self.fail_audio()
            raise

    async def _play(self, text: str, pcm: bytes, *, final_sentence: bool = True) -> None:
        if isinstance(self.device, CaptionedAudioDevice):
            await self.device.play_text(text, pcm, final_sentence=final_sentence)
        else:
            await self.device.play(pcm)

    async def _checked_reply(self, text: str, acknowledgement=None) -> tuple[float, float, float]:
        queue: asyncio.Queue[tuple[str, bytes, bool] | None] = asyncio.Queue()
        synthesis_seconds = 0.0
        ready = 0.0

        async def produce():
            nonlocal synthesis_seconds, ready
            size = 0
            before = time.monotonic()
            sentences = checked_sentences(text)

            async def chunks():
                if isinstance(self.speech, SentenceSpeech):
                    async for item in self.speech.synthesize_sentences(sentences):
                        yield item
                else:
                    for sentence in sentences:
                        yield sentence, await self.speech.synthesize(sentence)

            try:
                index = 0
                async for sentence, pcm in chunks():
                    validate_pcm(pcm)
                    size += len(pcm)
                    if size > MAX_AUDIO_BYTES:
                        raise AudioFailure("complete response exceeds audio bound")
                    queue.put_nowait((sentence, pcm, index == len(sentences) - 1))
                    index += 1
                synthesis_seconds = time.monotonic() - before
                ready = self.clock()
            finally:
                queue.put_nowait(None)

        producer = asyncio.create_task(produce())
        first = None
        try:
            if acknowledgement is not None:
                await acknowledgement
            if isinstance(self.device, QueuedAudioDevice):
                self.device.begin_reply()
            while (item := await queue.get()) is not None:
                sentence, pcm, final_sentence = item
                if first is None:
                    first = self.clock()
                await self._play(sentence, pcm, final_sentence=final_sentence)
            await producer
            if isinstance(self.device, QueuedAudioDevice):
                await self.device.finish_reply()
            if first is None:
                raise AudioFailure("no checked response audio")
            return ready, first, synthesis_seconds
        finally:
            if not producer.done():
                producer.cancel()
                await asyncio.gather(producer, return_exceptions=True)

    async def fail_audio(self) -> None:
        if not self.failed:
            self.failed = True
            if self.journey_id:
                await self._handover()

    async def _handover(self) -> None:
        result = await self.channel.turn(self.journey_id, "I want a human advisor", uuid4().hex)
        if not result.escalated:
            raise VoiceError("voice failure handover was not confirmed")

    async def submit_outcome(self, submission: OutcomeSubmission, request_id: str) -> OutcomeReceipt:
        if (not self.disclosed or self.failed or self.pending_outcome is None
                or submission.response_id != self.pending_outcome.response_id):
            raise ContractError("outcome must refer to the current offered answer")
        receipt = await self.channel.record_outcome(self.journey_id, submission, request_id)
        self.pending_outcome = None
        if isinstance(self.device, OutcomeAwareAudioDevice):
            await self.device.outcome_recorded(submission.response_id, receipt.duplicate)
        return receipt

    async def turn(self, pcm: bytes, *, request_id: str | None = None,
                   related_answer: ResponseReference | None = None) -> VoiceReply:
        return await self._turn(pcm=pcm, request_id=request_id, related_answer=related_answer)

    async def turn_text(self, text: str, *, request_id: str | None = None,
                        related_answer: ResponseReference | None = None) -> VoiceReply:
        """Narrate a text turn through the same authenticated, checked reply path."""
        return await self._turn(text=text, request_id=request_id, related_answer=related_answer)

    async def _turn(self, *, pcm: bytes | None = None, text: str | None = None,
                    request_id: str | None = None, related_answer: ResponseReference | None = None) -> VoiceReply:
        if not self.disclosed or self.failed or self.inflight:
            raise VoiceError("spoken disclosure and a healthy audio session are required")
        self.inflight = True
        turn_id = request_id or uuid4().hex
        started = self.clock()
        stage_started = time.monotonic()
        acknowledgement = None
        acknowledged = None

        async def acknowledge():
            nonlocal acknowledged
            acknowledged = self.clock()
            if self.acknowledgement_audio is not None:
                await self._play(ACKNOWLEDGEMENT, self.acknowledgement_audio)

        try:
            if pcm is None and text is None:
                raise AudioFailure("no audio or narrated text input")
            if pcm is not None:
                validate_pcm(pcm)
            elif not isinstance(text, str) or not text.strip() or len(text) > 4000:
                raise ContractError("bounded nonempty narrated text required")
            if self.acknowledgement_audio is not None and self.pending_outcome is None:
                acknowledgement = asyncio.create_task(acknowledge())
            transcript = await self.speech.transcribe(pcm) if pcm is not None else text
            transcribed = time.monotonic()
            if not isinstance(transcript, str) or not transcript.strip():
                raise AudioFailure("speech recognition did not produce a final transcript")
            if self.pending_outcome is not None:
                spoken = transcript.strip().rstrip(".!?").strip().casefold()
                choice = next((name for name in ("yes", "no", "skip")
                               if spoken == getattr(self.pending_outcome, name).casefold()), None)
                if choice is not None:
                    submission = OutcomeSubmission(self.pending_outcome.response_id, choice)
                    receipt = await self.submit_outcome(submission, turn_id)
                    return VoiceReply(state=receipt.state, feedback_recorded=True)
                self.pending_outcome = None
                if self.acknowledgement_audio is not None:
                    acknowledgement = asyncio.create_task(acknowledge())
            reply = await self.channel.turn(
                self.journey_id, transcript, turn_id,
                **({"related_answer": related_answer} if related_answer is not None else {}),
            )
            replied = time.monotonic()
            if reply.journey_id is not None and reply.journey_id != self.journey_id:
                raise ContractError("voice response journey mismatch")
            if reply.duplicate:
                raise VoiceError("turn was already committed; do not replay or invent its audio")
            self.pending_outcome = (OutcomeOffer.from_dict(reply.outcome_offer)
                                    if reply.outcome_offer is not None else None)
            if isinstance(self.device, MetadataAudioDevice):
                await self.device.response_metadata(reply)
            if getattr(self.speech, "checked_sentence_audio", False):
                ready, substantive_first, synthesis_seconds = await self._checked_reply(reply.text, acknowledgement)
                first_audio = acknowledged if acknowledged is not None else substantive_first
            else:
                audio = await self.speech.synthesize(reply.text)
                synthesized = time.monotonic()
                validate_pcm(audio)
                ready = self.clock()
                first_audio = self.clock()
                substantive_first = first_audio
                synthesis_seconds = synthesized - replied
                await self.device.play(audio)
            response_id = reply.response_id or (self.pending_outcome.response_id if self.pending_outcome else None)
            if (response_id is not None and response_id not in self.confirmed_responses and self.channel_kind == "voice"
                    and getattr(self.device, "confirms_playback", False) is True):
                await self.channel.confirm_playback(self.journey_id, response_id, f"playback-{response_id}")
                self.confirmed_responses.add(response_id)
        except (AudioFailure, OSError, TimeoutError):
            await self.fail_audio()
            raise
        except (VoiceError, httpx.HTTPError, ContractError):
            self.failed = True
            raise
        finally:
            if acknowledgement is not None and not acknowledgement.done():
                acknowledgement.cancel()
                await asyncio.gather(acknowledgement, return_exceptions=True)
            self.inflight = False
        self.turn_seconds.append(ready - started)
        self.input_modes.add("audio" if pcm is not None else "text")
        from governedgraph.observability import emit

        emit("voice_audio", event_id=f"{self.journey_id}:voice:{turn_id}",
             journey_id=self.journey_id, agent_name="gg-voice-bridge",
             substantive_ms=(substantive_first - started) * 1000,
             complete_audio_ms=(ready - started) * 1000,
             **({"acknowledgement_ms": (acknowledged - started) * 1000} if acknowledged is not None else {}))
        self.first_audio_seconds.append(first_audio - started)
        self.substantive_first_seconds.append(substantive_first - started)
        if acknowledged is not None:
            self.acknowledgement_seconds.append(acknowledged - started)
        self.stage_samples.append({**reply.stage_ms,
                                   "transcription": (transcribed - stage_started) * 1000 if pcm is not None else 0.0,
                                   "orchestration": (replied - transcribed) * 1000,
                                   "synthesis": synthesis_seconds * 1000,
                                   "model_rounds": reply.model_rounds})
        return reply

    def metrics(self) -> dict:
        samples = sorted(self.turn_seconds)
        p95_ms = samples[math.ceil(len(samples) * .95) - 1] * 1000 if samples else None
        first = sorted(self.first_audio_seconds)
        first_p95 = first[math.ceil(len(first) * .95) - 1] * 1000 if first else None
        return {
            "journey_id": self.journey_id, "turn_count": len(samples),
            "measurement": (
                "text_input_to_complete_audio_ready" if self.input_modes == {"text"}
                else "mixed_input_to_complete_audio_ready" if "text" in self.input_modes
                else "end_of_input_to_complete_audio_ready"),
            "voice_turn_p95_ms": p95_ms, "target_ms": 2000,
            "target_met": p95_ms is not None and p95_ms < 2000,
            "slo_certified": "text" not in self.input_modes and len(samples) >= 20
            and p95_ms is not None and p95_ms < 2000,
            "first_audio_measurement": "end_of_input_to_first_validated_audio_playback_dispatch",
            "first_audio_sample_count": len(first),
            "first_audio_p95_ms": first_p95,
            "first_audio_target_met": first_p95 is not None and first_p95 < 2000,
            "complete_audio_sample_count": len(samples),
            "first_audio_p50_ms": first[math.ceil(len(first) * .5) - 1] * 1000 if first else None,
            "complete_audio_p50_ms": samples[math.ceil(len(samples) * .5) - 1] * 1000 if samples else None,
            "first_audio_includes_approved_acknowledgement": bool(self.acknowledgement_seconds),
            "substantive_first_audio_sample_count": len(self.substantive_first_seconds),
            "substantive_first_audio_p95_ms": (
                sorted(self.substantive_first_seconds)[math.ceil(len(self.substantive_first_seconds) * .95) - 1] * 1000
                if self.substantive_first_seconds else None),
        }

