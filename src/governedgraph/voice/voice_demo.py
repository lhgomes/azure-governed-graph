"""Azure-hosted, synthetic voice simulation helpers (no desktop audio paths)."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from governedgraph.agents.live_persona import LivePersona
from governedgraph.agents.persona_simulator import ScriptedPersona, persona
from governedgraph.common import domain
from governedgraph.voice.voice_live_bridge import (
    AudioFailure, PCM_RATE, Speech, VoiceBridge, VoiceError, VoiceReply, validate_pcm,
)


@dataclass(frozen=True)
class VoiceDemoResult:
    journey_id: str
    replies: list[VoiceReply]
    elapsed_seconds: float
    metrics: dict
    label: str = "Audio loopback, not acoustic latency"
    session_ended: bool = False
    # persona | escalation | takeover | persona_unavailable | scripted
    ended_by: str = "scripted"


class PacedLoopback:
    """Consume audio at its real duration; never call this acoustic playback."""

    async def play(self, pcm: bytes) -> None:
        validate_pcm(pcm)
        await asyncio.sleep(len(pcm) / (2 * PCM_RATE))

    async def capture(self) -> bytes:
        raise AudioFailure("loopback mode has no presenter microphone")


log = logging.getLogger("governedgraph.voice")


def _honest_stop(reply: VoiceReply) -> bool:
    """An advisor handover or an honest non-answer ends a scripted story without being a failure."""
    return reply.escalated or reply.composition_source == "unanswered"


async def run_simulator(bridge: VoiceBridge, persona_speech: Speech, *, persona_id: str | None = None,
                        deadline_seconds: float = 120, script: ScriptedPersona | None = None,
                        channel: str = "voice", accessibility_mode: str = "standard") -> VoiceDemoResult:
    actor = script if script is not None else persona(persona_id or domain.demo()["persona"])
    started = time.perf_counter()
    replies = []
    previous = ""
    questions = list(actor.persona.get("follow_up_questions", [])) if script is not None else []
    closing = actor.persona.get("closing", "") if script is not None else ""
    session_ended = False
    try:
        async with asyncio.timeout(deadline_seconds):
            await bridge.start(accessibility_mode, channel=channel)
            for _ in range(4):
                if replies and questions:
                    utterance = questions.pop(0)
                elif replies and closing:
                    if _honest_stop(replies[-1]):
                        break
                    if replies[-1].state != "comprehension_check" or not actor.persona.get("teach_back_answer"):
                        raise VoiceError("synthetic script ended before resolution")
                    utterance = actor.persona["teach_back_answer"]
                else:
                    utterance = actor.next_utterance(previous)
                if utterance is None:
                    break
                pcm = await persona_speech.synthesize(utterance)
                validate_pcm(pcm)
                await bridge.device.play(pcm)
                reply = await bridge.turn_text(utterance) if channel == "chat" else await bridge.turn(pcm)
                replies.append(reply)
                previous = reply.text
                if reply.escalated or (reply.state == "resolved" and not questions):
                    break
            if closing and not (replies and _honest_stop(replies[-1])):
                if not replies or replies[-1].state != "resolved" or questions:
                    raise VoiceError("synthetic script ended before resolution")
                pcm = await persona_speech.synthesize(closing)
                validate_pcm(pcm)
                await bridge.device.play(pcm)
                session_ended = True
    except (AudioFailure, OSError, TimeoutError):
        await bridge.fail_audio()
        raise
    return VoiceDemoResult(bridge.journey_id, replies, time.perf_counter() - started, bridge.metrics(),
                           session_ended=session_ended)


async def run_live_simulator(bridge: VoiceBridge, persona_speech: Speech, live: LivePersona, *,
                             channel: str = "voice", accessibility_mode: str = "standard",
                             contact_type: str = "first_contact",
                             takeover: Callable[[], bool] | None = None,
                             deadline_seconds: float = 300) -> VoiceDemoResult:
    """Agent-to-agent conversation: the persona agent speaks, the governed guide answers, both over audio."""
    started = time.perf_counter()
    replies: list[VoiceReply] = []
    previous = ""
    session_ended = False
    ended_by = "persona"
    try:
        async with asyncio.timeout(deadline_seconds):
            # The persona card declares the language its member speaks; the guide answers in it.
            await bridge.start(accessibility_mode, channel=channel, language=live.card.language,
                               **({"contact_type": contact_type} if contact_type != "first_contact" else {}))
            for _ in range(live.card.max_turns):
                if takeover is not None and takeover():
                    ended_by = "takeover"
                    break
                try:
                    turn = await live.next_turn(previous)
                except Exception as error:
                    # A failed actor is not a failed member journey: stop without escalating it.
                    log.error("live_persona_failed", extra={
                        "journey_id": bridge.journey_id, "error_type": type(error).__name__,
                        "status": getattr(error, "status_code", None)})
                    ended_by = "persona_unavailable"
                    break
                pcm = await persona_speech.synthesize(turn.utterance)
                validate_pcm(pcm)
                await bridge.device.play(pcm)
                if turn.done and replies:
                    session_ended = not replies[-1].escalated
                    break
                reply = await bridge.turn_text(turn.utterance) if channel == "chat" else await bridge.turn(pcm)
                replies.append(reply)
                previous = reply.text
                if reply.escalated:
                    ended_by = "escalation"
                    break
    except (AudioFailure, OSError, TimeoutError):
        await bridge.fail_audio()
        raise
    return VoiceDemoResult(bridge.journey_id, replies, time.perf_counter() - started, bridge.metrics(),
                           session_ended=session_ended, ended_by=ended_by)
