"""ILLUSTRATIVE - synthetic data. Voice I/O for an already authenticated channel host."""
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time
from dataclasses import dataclass

from governedgraph.agents.persona_simulator import ScriptedPersona, persona
from governedgraph.common import domain
from governedgraph.common.config import TEXT_DEMO_MODULE
from governedgraph.voice.voice_live_bridge import (
    AudioFailure, PCM_RATE, Speech, VoiceBridge, VoiceError, VoiceReply, validate_pcm,
)


@dataclass(frozen=True)
class VoiceDemoResult:
    journey_id: str
    replies: list[VoiceReply]
    elapsed_seconds: float
    metrics: dict
    label: str = "ILLUSTRATIVE - synthetic data; audio loopback, not acoustic latency"
    session_ended: bool = False


class PacedLoopback:
    """Consume audio at its real duration; never call this acoustic playback."""

    async def play(self, pcm: bytes) -> None:
        validate_pcm(pcm)
        await asyncio.sleep(len(pcm) / (2 * PCM_RATE))

    async def capture(self) -> bytes:
        raise AudioFailure("loopback mode has no presenter microphone")


class PresenterAudio:
    """Optional channel-host microphone/speaker adapter; nothing is recorded to disk."""

    def __init__(self, *, capture_seconds: float = 8):
        if not 0 < capture_seconds <= 20:
            raise ValueError("presenter capture must be bounded to twenty seconds")
        self.capture_seconds = capture_seconds

    async def play(self, pcm: bytes) -> None:
        validate_pcm(pcm)
        await asyncio.to_thread(self._play, pcm)

    @staticmethod
    def _play(pcm: bytes) -> None:
        try:
            import sounddevice as sd
        except (ImportError, OSError) as error:
            raise AudioFailure("install requirements-voice.txt and the system PortAudio library") from error
        try:
            with sd.RawOutputStream(samplerate=PCM_RATE, channels=1, dtype="int16") as stream:
                if stream.write(pcm):
                    raise AudioFailure("presenter speaker underrun")
        except sd.PortAudioError as error:
            raise AudioFailure("presenter speaker unavailable") from error

    async def capture(self) -> bytes:
        return await asyncio.to_thread(self._capture)

    def _capture(self) -> bytes:
        try:
            import sounddevice as sd
        except (ImportError, OSError) as error:
            raise AudioFailure("install requirements-voice.txt and the system PortAudio library") from error
        input("Press Enter, then speak during the next capture window: ")
        try:
            with sd.RawInputStream(samplerate=PCM_RATE, channels=1, dtype="int16") as stream:
                data, overflowed = stream.read(int(PCM_RATE * self.capture_seconds))
                if overflowed:
                    raise AudioFailure("presenter microphone overflow")
                return bytes(data)
        except sd.PortAudioError as error:
            raise AudioFailure("presenter microphone unavailable") from error


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
            if closing and not (replies and replies[-1].escalated):
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


async def run_presenter(bridge: VoiceBridge, *, max_turns: int = 4) -> VoiceDemoResult:
    """Use the same authenticated bridge, replacing only the simulator's audio input."""
    if not 1 <= max_turns <= 10:
        raise ValueError("presenter turn count must be bounded")
    started = time.perf_counter()
    replies = []
    await bridge.start()
    for _ in range(max_turns):
        try:
            pcm = await bridge.device.capture()
        except (AudioFailure, OSError, TimeoutError):
            await bridge.fail_audio()
            raise
        result = await bridge.turn(pcm)
        replies.append(result)
        if result.escalated or result.state == "resolved":
            break
    return VoiceDemoResult(
        bridge.journey_id, replies, time.perf_counter() - started, bridge.metrics(),
        label="ILLUSTRATIVE - synthetic data; presenter microphone/speaker",
    )


def text_fallback() -> int:
    # Do not inherit Azure mode and accidentally enter the legacy token-file demo.
    environment = {**os.environ, "GOVERNEDGRAPH_MODE": "local"}
    return subprocess.run([sys.executable, "-m", TEXT_DEMO_MODULE], env=environment, check=False).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", action="store_true", help="explicit offline text fallback (not voice acceptance)")
    args = parser.parse_args()
    if args.text:
        return text_fallback()
    print("Voice mode requires an authenticated channel host. Use run_presenter/run_simulator with "
          "VoiceBridge; synthetic live proofs run only in the existing dev-issuer job (--probe-voice).",
          file=sys.stderr)
    print("Voice unavailable; running the labelled offline text fallback. Voice acceptance has NOT passed.",
          file=sys.stderr)
    result = text_fallback()
    return result or 1


if __name__ == "__main__":
    raise SystemExit(main())
