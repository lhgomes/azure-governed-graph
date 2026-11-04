"""Hosted speech boundary. Runtime calls Speech; issuer supplies only a member bearer."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx

from governedgraph.agents.persona_simulator import ScriptedPersona
from governedgraph.common import domain
from governedgraph.common.config import settings
from governedgraph.common.contracts import ContractError, _strict
from governedgraph.common.member_auth import MemberIdentity
from governedgraph.voice.voice_demo import PacedLoopback, run_simulator
from governedgraph.voice.azure_voice import open_counsellor_speech as open_speech
from governedgraph.voice.voice_live_bridge import OrchestratorChannel, PERSONA_VOICES, VoiceBridge


@dataclass(frozen=True)
class VoiceScriptRequest:
    opening: str
    teach_back_answer: str

    def __post_init__(self):
        if any(not isinstance(text, str) or not text.strip() or len(text) > 2000
               for text in (self.opening, self.teach_back_answer)):
            raise ContractError("bounded simulator utterances are required")

    @classmethod
    def from_dict(cls, body: dict) -> VoiceScriptRequest:
        return _strict(cls, body)


async def run_hosted(script: VoiceScriptRequest, token: str, identity: MemberIdentity) -> dict:
    if (not settings.is_azure or settings.environment_name != "gg-dev"
            or os.getenv("GOVERNEDGRAPH_ENABLE_VOICE_PROOF") != "true"):
        raise PermissionError("synthetic hosted voice proofs are disabled")
    if identity.member_ref != domain.demo()["member_ref"] or not identity.step_up_verified:
        raise PermissionError("a step-up verified synthetic demo member is required")
    from azure.identity.aio import ManagedIdentityCredential
    import asyncio
    from governedgraph.voice.warmup import warm_runtime

    started = time.perf_counter()
    await asyncio.to_thread(warm_runtime)
    endpoint = os.environ["AZURE_VOICE_LIVE_ENDPOINT"]
    orchestrator = os.environ["GOVERNEDGRAPH_ORCHESTRATOR_URL"]
    async with ManagedIdentityCredential(client_id=os.environ["AZURE_CLIENT_ID"]) as credential:
        async with open_speech(endpoint, PERSONA_VOICES["counsellor"], credential=credential) as speech:
            async with open_speech(endpoint, PERSONA_VOICES[domain.demo()["persona"]], credential=credential) as actor:
                async with httpx.AsyncClient(base_url=orchestrator.rstrip("/") + "/",
                                             timeout=120, follow_redirects=False) as client:
                    bridge = VoiceBridge(OrchestratorChannel(client, lambda: token), speech, PacedLoopback())
                    result = await run_simulator(
                        bridge, actor, script=ScriptedPersona({
                            "opening": script.opening, "teach_back_answer": script.teach_back_answer,
                        }),
                    )
                    elapsed = time.perf_counter() - started
                    report = await client.get(
                        f"members/{identity.member_ref}/explainability-report",
                        headers={"Authorization": "Bearer " + token},
                    )
                    report.raise_for_status()
                    audit = report.json()
    rows = [row for row in audit["records"] if row["journey_id"] == result.journey_id]
    explanation = [row for row in rows if row["response_kind"] == "explanation"]
    first = result.replies[0] if result.replies else None
    return {
        "label": result.label, "journey_id": result.journey_id,
        "runtime_principal_id": settings.strategy_runtime_id,
        "arm": first.arm if first else None,
        "strategy_id": first.strategy_id if first else None,
        "disclosure_first": bridge.disclosed,
        "s5_selected": first is not None and first.strategy_id == "S5",
        "s2_suspended": first is not None and "S2" in first.suspended,
        "teach_back_passed": bool(result.replies) and result.replies[-1].state == "resolved",
        "bound_text_only": bool(explanation) and all(row["numeric_fidelity_passed"] is True for row in explanation),
        "explainability_written": len(rows) == len(result.replies) + 1
        and all(row["response_id"] in audit["delivered_response_ids"] for row in rows)
        and all(reply.audit_status == "committed" for reply in result.replies),
        "response_ids": [row["response_id"] for row in rows],
        "agent_versions": sorted({row["agent_version"] for row in explanation}),
        "total_seconds": elapsed,
        "under_two_minutes": elapsed < 120,
        "metrics": result.metrics,
    }
