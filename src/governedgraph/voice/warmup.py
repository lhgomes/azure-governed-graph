"""Warm approved adapters without creating a member journey or bypassing turn gates."""
from uuid import uuid4
from contextlib import nullcontext

from types import SimpleNamespace

from governedgraph.agents.azure_safety import AzurePromptShields
from governedgraph.agents.decision_agents import DecisionContext, DecisionUnavailable, run_decision
from governedgraph.common import domain
from governedgraph.common.contracts import IntentDecision
from governedgraph.agents.foundry_runtime import FoundryComposer, FoundryUnavailable
from governedgraph.agents.counsellor import INSTRUCTIONS
from governedgraph.common.config import settings
from governedgraph.observability import operation
from governedgraph.voice.voice_live_bridge import VoiceError


def warm_runtime() -> None:
    correlation_id = f"J-{uuid4().hex}"
    with operation(correlation_id, "governedgraph.voice.warmup", agent_name="gg-voice-warmup"):
        try:
            FoundryComposer.shared(INSTRUCTIONS)
        except FoundryUnavailable as error:
            raise VoiceError("warm-up pinned agent unavailable") from error
        screen = AzurePromptShields(settings.content_safety_endpoint)
        screen.journey_id = correlation_id
        demo = domain.demo()
        if not screen.screen(demo["warmup_utterance"], demo["member_ref"]).allowed:
            raise VoiceError("warm-up screening unavailable")
        try:
            tools = SimpleNamespace(journey=SimpleNamespace(journey_id=correlation_id), trace_parent="",
                                    trace=SimpleNamespace(decision_evidence=[], note=lambda *_: None,
                                                          measure=lambda _: nullcontext()),
                                    conversation_context=lambda: [])
            decision = run_decision("intent", tools, demo["warmup_utterance"],
                                    DecisionContext("identified", "unclassified", None))
        except DecisionUnavailable as error:
            raise VoiceError("warm-up decision agent unavailable") from error
        if not isinstance(decision, IntentDecision) or decision.route != "explanation":
            raise VoiceError("warm-up intent routing did not return an explanation")
