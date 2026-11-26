"""Real STT/HTTP/TTS, non-acoustic dispatch sink."""
from __future__ import annotations

import asyncio
import math
import time

from governedgraph.common import domain
from governedgraph.voice.azure_voice import open_counsellor_speech as open_speech
from governedgraph.voice.voice_live_bridge import OrchestratorChannel, PERSONA_VOICES, VoiceBridge, validate_pcm
from governedgraph.voice.voice_live_bridge import VoiceReply

QUESTIONS = domain.vocabulary()["benchmark_questions"]


def distribution(values: list[float]) -> dict:
    ordered = sorted(values)
    return {"n": len(ordered),
            "p50_ms": ordered[math.ceil(len(ordered) * .5) - 1] if ordered else None,
            "p95_ms": ordered[math.ceil(len(ordered) * .95) - 1] if ordered else None}


def summarize(rows: list[dict]) -> dict:
    return {group: {
        "turns": len(selected := [row for row in rows if row["temperature"] == group]),
        "quality_passed": sum(row["quality_passed"] for row in selected),
        "stages": {stage: distribution([row["stages"][stage] for row in selected
                                        if stage in row["stages"]])
                   for stage in sorted({key for row in selected for key in row["stages"]})},
        "first_audio": distribution([row["first_audio_ms"] for row in selected]),
        "complete_audio": distribution([row["complete_audio_ms"] for row in selected]),
        "substantive_first_audio": distribution([row["substantive_first_audio_ms"] for row in selected]),
        "template_fallbacks": sum(row["composition_source"] == "template_fallback" for row in selected),
        "numeric_fidelity_passed": all(row["numeric_fidelity_passed"] for row in selected),
        "zero_option_recommendations": all(row["no_option_recommendation"] for row in selected),
        "model_rounds": [row["model_rounds"] for row in selected],
    } for group in ("cold", "warm")}


class DispatchSink:
    async def play(self, pcm: bytes) -> None:
        validate_pcm(pcm)


async def run_benchmark(socket, credential, client, token: str, endpoint: str, *,
                        runtime=None, model: str = "gpt-counsellor") -> None:
    """Fresh Speech connections for cold controls; reused Speech for twenty warm turns."""
    rows = []
    started = time.perf_counter()
    async with open_speech(endpoint, PERSONA_VOICES[domain.demo()["persona"]], credential=credential) as actor:
        inputs = []
        for index, question in enumerate(QUESTIONS):
            inputs.append(await actor.synthesize(question))
            await socket.send_json({"kind": "benchmark_progress", "turn": 0, "input": index + 1})

    class CandidateChannel(OrchestratorChannel):
        cold = False
        async def turn(self, journey_id: str, transcript: str, request_id: str) -> VoiceReply:
            if runtime is None:
                return await super().turn(journey_id, transcript, request_id)
            reply = await self.client.post(f"voice/benchmark-turns/{journey_id}",
                                           headers={**self._headers(), "Idempotency-Key": request_id},
                                           json={"utterance": transcript, "model": model, "cold": self.cold})
            reply.raise_for_status()
            return VoiceReply.from_dict(reply.json())

    channel = CandidateChannel(client, lambda: token)

    async def measure(speech, index: int, temperature: str):
        channel.cold = temperature == "cold"
        bridge = VoiceBridge(channel, speech, DispatchSink())
        await bridge.start()
        reply = await bridge.turn(inputs[index])
        rows.append({
            "case_id": f"voice-{index + 1:02}", "journey_id": bridge.journey_id,
            "temperature": temperature, "strategy": reply.strategy_id, "arm": reply.arm,
            "quality_passed": not reply.escalated and reply.audit_status == "committed"
            and "S2" in reply.suspended and reply.strategy_id in reply.ranked,
            "stages": {k: v for k, v in bridge.stage_samples[-1].items() if k != "model_rounds"},
            "model_rounds": reply.model_rounds,
            "composition_source": reply.composition_source,
            "numeric_fidelity_passed": reply.numeric_fidelity_passed,
            "no_option_recommendation": reply.no_option_recommendation,
            "first_audio_ms": bridge.first_audio_seconds[-1] * 1000,
            "substantive_first_audio_ms": bridge.substantive_first_seconds[-1] * 1000,
            "complete_audio_ms": bridge.turn_seconds[-1] * 1000,
        })
        await socket.send_json({"kind": "benchmark_progress", "turn": len(rows),
                                "journey_id": bridge.journey_id})

    for index in range(3):
        async with open_speech(endpoint, PERSONA_VOICES["counsellor"], credential=credential) as speech:
            await measure(speech, index, "cold")
    if runtime is not None:
        from governedgraph.voice.warmup import warm_runtime

        await asyncio.to_thread(warm_runtime)
    async with open_speech(endpoint, PERSONA_VOICES["counsellor"], credential=credential) as speech:
        for index in range(20):
            await measure(speech, index, "warm")
    await socket.send_json({
        "kind": "benchmark_complete",
        "measurement": "real STT/HTTP/TTS, validated dispatch sink; not acoustic playback",
        "cold_definition": "fresh Speech connections and isolated SDK/HTTP clients including agent read; "
                           "orchestrator replica intentionally remains warm",
        "total_seconds": time.perf_counter() - started, "summary": summarize(rows), "rows": rows,
        "model": model,
    })
