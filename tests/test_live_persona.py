"""Live persona agent: closed cards, member-visible-only prompts, and the agent-to-agent simulator loop."""
import asyncio
import json
import sys
from dataclasses import asdict
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from governedgraph.agents.live_persona import (
    LIVE_PERSONA_INSTRUCTIONS, LIVE_PERSONA_TEMPERATURE, LivePersona, PersonaBlocked, PersonaCard, PersonaTurn,
    verify_live_persona_version,
)
from governedgraph.common.config import DATA_DIR
from governedgraph.common.contracts import DEMOGRAPHIC_FIELD_NAMES, ContractError
from governedgraph.common.io import load_json
from governedgraph.voice.presenter import ChatMessage, SocketAudio
from governedgraph.voice.voice_demo import run_live_simulator
from governedgraph.voice.voice_live_bridge import VoiceReply


@pytest.fixture
def fake_openai(monkeypatch):
    module = ModuleType("openai")

    class BadRequestError(Exception):
        def __init__(self, code):
            self.code = code

    module.BadRequestError = BadRequestError
    monkeypatch.setitem(sys.modules, "openai", module)
    return BadRequestError


def card(**changes):
    body = {"persona_id": "fictional", "goal": "Understand the letter.", "opening": "Explain the letter.",
            "manner": "Brief and polite.", "concern": "Wants clarity.", "knowledge": "Knows little jargon.",
            "voice": "en-GB-AlfieNeural",
            "behaviours": ["Asks for plainer words."], "max_turns": 4}
    return PersonaCard.from_dict({**body, **changes})


def answer(utterance, done=False):
    return SimpleNamespace(status="completed", output_text=json.dumps({"utterance": utterance, "done": done}),
                           output=[SimpleNamespace(type="message")])


@pytest.mark.parametrize("changes", [
    {"manner": "Aged 45 and polite."}, {"behaviours": ["Has 3 questions."]}, {"manner": ""}, {"language": "fr"},
    {"max_turns": 1}, {"max_turns": 9}, {"behaviours": ["a"] * 6}, {"goal": "x" * 601}, {"age": 45},
    {"voice": ""}, {"voice": None}, {"voice": "Alfie"}, {"voice": "en-GB-Ryan:DragonHDLatestNeural"},
])
def test_b7_persona_card_is_closed_bounded_and_describes_behaviour_not_demographics(changes):
    with pytest.raises(ContractError):
        card(**changes)


def test_every_sample_persona_has_a_valid_live_card_without_demographic_attributes():
    personas = load_json(DATA_DIR / "personas" / "personas.json")["personas"]
    assert personas
    for profile in personas:
        built = PersonaCard.from_dict({"persona_id": profile["persona_id"], "goal": profile["goal"],
                                       "opening": profile["opening"], **profile["live"]})
        assert not (set(asdict(built)) & DEMOGRAPHIC_FIELD_NAMES)


def test_b3_the_opening_is_fixed_and_only_later_turns_call_the_agent():
    client = Mock()
    client.responses.create.return_value = answer("Could you say that more simply?")
    actor = LivePersona(card(), client=client, agent_version="1")

    first = asyncio.run(actor.next_turn(""))
    assert first == PersonaTurn("Explain the letter.") and not client.responses.create.called
    second = asyncio.run(actor.next_turn("The letter explains the conversion of your rights."))
    assert second == PersonaTurn("Could you say that more simply?")
    inputs = client.responses.create.call_args.kwargs
    assert inputs["store"] is False and inputs["extra_body"]["agent_reference"]["version"] == "1"
    payload = json.loads(inputs["input"][0]["content"])
    assert set(payload) == {"persona_card", "you_opened_with", "guide_replied", "turns_remaining"}
    assert payload["guide_replied"] == "The letter explains the conversion of your rights."

    asyncio.run(actor.next_turn("Rights are converted into the new contract."))
    user_turns = [item for item in client.responses.create.call_args.kwargs["input"] if item["role"] == "user"]
    assert len(user_turns) == 2 and set(json.loads(user_turns[-1]["content"])) == {"guide_replied", "turns_remaining"}


def test_persona_always_concludes_at_the_turn_limit_even_if_the_agent_does_not():
    client = Mock()
    client.responses.create.return_value = answer("One more question.", done=False)
    actor = LivePersona(card(max_turns=3), client=client, agent_version="1")
    turns = [asyncio.run(actor.next_turn("Reply.")) for _ in range(3)]
    assert [turn.done for turn in turns] == [False, False, True]


@pytest.mark.parametrize("output_text", [
    '{"utterance": "Hi"}', '{"utterance": "Hi", "done": "yes"}', '{"utterance": "", "done": false}',
    '{"utterance": "Hi", "done": false, "strategy": "S5"}', '{"utterance": "' + "x" * 601 + '", "done": false}',
])
def test_persona_output_contract_is_closed(output_text):
    client = Mock()
    client.responses.create.return_value = SimpleNamespace(
        status="completed", output_text=output_text, output=[SimpleNamespace(type="message")])
    actor = LivePersona(card(), client=client, agent_version="1")
    asyncio.run(actor.next_turn(""))
    with pytest.raises(ContractError):
        asyncio.run(actor.next_turn("Reply."))


def test_persona_failures_are_reported_not_substituted_with_invented_replies(fake_openai):
    client = Mock()
    actor = LivePersona(card(), client=client, agent_version="1")
    asyncio.run(actor.next_turn(""))
    client.responses.create.side_effect = fake_openai("content_filter")
    with pytest.raises(PersonaBlocked):
        asyncio.run(actor.next_turn("Reply."))
    client.responses.create.side_effect = None
    client.responses.create.return_value = SimpleNamespace(status="failed", output_text="", output=[])
    with pytest.raises(RuntimeError):
        asyncio.run(actor.next_turn("Reply."))


@pytest.mark.parametrize("attribute,value", [
    ("kind", "hosted"), ("instructions", "different"), ("model", "different"),
    ("tools", [SimpleNamespace(type="function")]), ("temperature", 0),
])
def test_live_persona_rejects_any_definition_with_tools_or_drift(attribute, value):
    definition = SimpleNamespace(kind="prompt", model="gpt-compact", temperature=LIVE_PERSONA_TEMPERATURE,
                                 instructions=LIVE_PERSONA_INSTRUCTIONS, tools=[])
    setattr(definition, attribute, value)
    with pytest.raises(ValueError):
        verify_live_persona_version(SimpleNamespace(definition=definition), "gpt-compact")


class Persona:
    def __init__(self, turns):
        self.card = card(max_turns=len(turns))
        self.turns = list(turns)

    async def next_turn(self, guide_text):
        item = self.turns.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def bridge_with(replies):
    bridge = SimpleNamespace(
        journey_id="J-live", device=SimpleNamespace(play=AsyncMock()),
        start=AsyncMock(), turn=AsyncMock(side_effect=list(replies)), turn_text=AsyncMock(side_effect=list(replies)),
        fail_audio=AsyncMock(), metrics=lambda: {})
    return bridge


def speech():
    spoken = []

    async def synthesize(text):
        spoken.append(text)
        return b"\0\0"

    return SimpleNamespace(synthesize=synthesize), spoken


def test_persona_answers_the_guides_follow_up_so_the_conversation_continues():
    async def scenario():
        bridge = bridge_with([
            VoiceReply(text="What would you like me to explain?", state="explaining",
                       composition_source="unanswered"),
            VoiceReply(text="Personal capital is how your built-up rights are shown.", state="comprehension_check"),
        ])
        actor, spoken = speech()
        persona = Persona([PersonaTurn("What does personal capital mean for me?"),
                           PersonaTurn("I mean the personal pension capital figure."),
                           PersonaTurn("Thanks, that is clear. Goodbye.", True)])
        result = await run_live_simulator(bridge, actor, persona, channel="chat")
        assert bridge.turn_text.await_count == 2 and not bridge.fail_audio.await_count
        assert spoken[-1] == "Thanks, that is clear. Goodbye." and result.ended_by == "persona"
        assert result.session_ended is True and len(result.replies) == 2

    asyncio.run(scenario())


def test_the_farewell_is_spoken_but_never_sent_to_the_guide():
    async def scenario():
        bridge = bridge_with([VoiceReply(text="Answer.", state="resolved")])
        actor, spoken = speech()
        result = await run_live_simulator(bridge, actor, Persona([PersonaTurn("Question?"),
                                                                  PersonaTurn("Bye.", True)]), channel="voice")
        assert bridge.turn.await_count == 1 and spoken == ["Question?", "Bye."] and result.session_ended is True

    asyncio.run(scenario())


def test_b9_an_escalation_ends_the_run_without_a_goodbye_or_extra_turns():
    async def scenario():
        bridge = bridge_with([VoiceReply(text="Arranging an advisor.", state="escalation_requested",
                                         escalated=True)])
        actor, spoken = speech()
        result = await run_live_simulator(bridge, actor, Persona([PersonaTurn("Which option?"),
                                                                  PersonaTurn("Again?"),
                                                                  PersonaTurn("Bye.", True)]), channel="chat")
        assert result.ended_by == "escalation" and result.session_ended is False and spoken == ["Which option?"]

    asyncio.run(scenario())


def test_a_failed_persona_stops_the_run_without_escalating_the_member_journey():
    async def scenario():
        bridge = bridge_with([VoiceReply(text="Answer.", state="explaining")])
        actor, _ = speech()
        result = await run_live_simulator(bridge, actor, Persona([PersonaTurn("Question?"),
                                                                  RuntimeError("model failed")]), channel="chat")
        assert result.ended_by == "persona_unavailable" and not bridge.fail_audio.await_count
        assert result.session_ended is False

    asyncio.run(scenario())


def test_takeover_stops_the_persona_after_the_current_exchange():
    async def scenario():
        bridge = bridge_with([VoiceReply(text="Answer.", state="explaining")])
        actor, spoken = speech()
        requested = iter([False, True])
        result = await run_live_simulator(bridge, actor, Persona([PersonaTurn("Question?"),
                                                                  PersonaTurn("Never spoken.")]),
                                          channel="chat", takeover=lambda: next(requested))
        assert result.ended_by == "takeover" and spoken == ["Question?"] and bridge.turn_text.await_count == 1

    asyncio.run(scenario())


def test_takeover_is_a_closed_session_action_that_carries_no_text():
    assert ChatMessage.from_dict({"kind": "takeover", "request_id": "request-1"}).kind == "takeover"
    with pytest.raises(ContractError):
        ChatMessage.from_dict({"kind": "takeover", "request_id": "request-1", "text": "speak for me"})


def test_takeover_during_playback_is_recorded_without_consuming_the_acknowledgement():
    async def scenario():
        frames = iter([{"kind": "takeover", "request_id": "request-1"}, {"kind": "played", "id": "a"}])
        socket = SimpleNamespace(receive_json=AsyncMock(side_effect=lambda: next(frames)))
        audio = SocketAudio(socket)
        assert await audio.playback_ack(1) == {"kind": "played", "id": "a"}
        assert audio.takeover_requested is True

    asyncio.run(scenario())


def test_sample_workload_template_passes_the_persona_settings_to_the_framework_template():
    from pathlib import Path

    wrapper = (Path(__file__).resolve().parents[1] / "examples/pension_wtp/infra/workloads.bicep").read_text()
    for name in ("deployVoicePersona", "personaAgentVersion"):
        assert f"{name}: configuration.{name}" in wrapper


@pytest.fixture
def azure_clients(monkeypatch):
    import azure.ai.projects
    import azure.identity
    from governedgraph.agents import live_persona

    seen = {}

    class Project:
        def __init__(self, **kwargs):
            seen["project"] = kwargs

        @property
        def agents(self):
            raise AssertionError("the persona identity must never read agent definitions")

        def get_openai_client(self, **kwargs):
            seen["openai"] = kwargs
            if seen.get("fail"):
                raise RuntimeError("endpoint unavailable")
            return Mock()

    monkeypatch.setattr(azure.ai.projects, "AIProjectClient", Project)
    monkeypatch.setattr(azure.identity, "ManagedIdentityCredential", lambda **kwargs: seen.setdefault("cred", kwargs))
    monkeypatch.setattr(live_persona, "settings", SimpleNamespace(project_endpoint="https://project.invalid",
                                                                  compact_deployment="gpt-compact"))
    monkeypatch.setenv("AZURE_PERSONA_CLIENT_ID", "persona-client")
    monkeypatch.setenv("AZURE_AI_PERSONA_VERSION", "1")
    return seen


def test_persona_uses_only_its_own_agent_endpoint_under_its_own_identity(azure_clients):
    from governedgraph.agents.live_persona import connect_live_persona

    actor = connect_live_persona(card())
    assert azure_clients["cred"] == {"client_id": "persona-client"}
    assert azure_clients["openai"]["agent_name"] == "gg-persona-demo" and azure_clients["project"]["allow_preview"]
    assert actor.agent_version == "1"


@pytest.mark.parametrize("missing", ["AZURE_PERSONA_CLIENT_ID", "AZURE_AI_PERSONA_VERSION"])
def test_unconfigured_persona_fails_closed(azure_clients, monkeypatch, missing):
    from governedgraph.agents.live_persona import connect_live_persona

    monkeypatch.setenv(missing, "")
    with pytest.raises(ContractError, match="not configured"):
        connect_live_persona(card())


def test_a_persona_connection_failure_is_a_closed_error_not_a_voice_transport_error(azure_clients):
    from governedgraph.agents.live_persona import connect_live_persona

    azure_clients["fail"] = True
    with pytest.raises(ContractError, match="unavailable"):
        connect_live_persona(card())


def test_thanks_after_an_answer_with_no_meaning_check_pending_is_a_follow_up_not_a_service_failure(monkeypatch):
    from contextlib import contextmanager

    from governedgraph.agents.tools import Trace
    from governedgraph.common.contracts import IntentDecision
    from governedgraph.orchestrator.state_machine import Journey, State
    from governedgraph.orchestrator import workflow
    from governedgraph.orchestrator.workflow import member_turn

    @contextmanager
    def no_tracing(*args, **kwargs):
        yield None

    monkeypatch.setattr(workflow, "operation", no_tracing)
    journey = Journey(journey_id="J-thanks", member_ref="member", state=State.RESOLVED)
    counsellor = SimpleNamespace(
        journey=journey,
        tools=SimpleNamespace(agent_name="gg-counsellor", trace_parent=None, auth_ref="member", trace=Trace()),
        screen=SimpleNamespace(screen=lambda text, ref: SimpleNamespace(allowed=True, reason="")),
        decide=lambda kind, text: IntentDecision("comprehension", "unclassified", "restating_previous_explanation",
                                                 "en"),
        _complete_turn=lambda result, count: result,
        _escalate=lambda *args, **kwargs: pytest.fail("a thank-you must not escalate"),
    )
    result = asyncio.run(member_turn(counsellor, "Yes, that helps a lot. Thank you."))
    assert result.workflow_steps == ["screen-input", "intent-agent", "clarify-question", "commit-checked-evidence"]
    assert not result.escalated and counsellor.tools.trace.turn_trace[-1]["result"] == "follow_up_question"


class Throttled(Exception):
    def __init__(self, status=429, **headers):
        self.status_code = status
        self.response = SimpleNamespace(headers=headers)


def throttled_tools():
    from governedgraph.agents.tools import Trace

    return SimpleNamespace(trace=Trace(), journey=SimpleNamespace(journey_id="J-rate"))


def test_a_pacing_429_waits_the_recommended_time_once_and_is_recorded_in_the_turn():
    from governedgraph.agents.rate_limit import create_with_retry

    create = Mock(side_effect=[Throttled(**{"retry-after": "3"}), "answer"])
    waits, tools = [], throttled_tools()
    assert create_with_retry(create, tools, sleep=waits.append, input="x") == "answer"
    assert waits == [3.5] and create.call_count == 2
    assert tools.trace.turn_trace == [{"stage": "rate_limit", "result": "retried", "issues": []}]


def test_the_azure_openai_millisecond_hint_is_honoured():
    from governedgraph.agents.rate_limit import create_with_retry

    waits = []
    create_with_retry(Mock(side_effect=[Throttled(**{"retry-after-ms": "2000"}), "ok"]), throttled_tools(),
                      sleep=waits.append)
    assert waits == [2.5]


@pytest.mark.parametrize("error", [
    Throttled(status=403, **{"x-gg-limit": "conversation-token-quota"}), Throttled(status=500),
    Throttled(**{"retry-after": "120"}), RuntimeError("boom"),
])
def test_quota_exhaustion_outages_and_long_waits_are_never_retried(error):
    from governedgraph.agents.rate_limit import create_with_retry

    create, waits = Mock(side_effect=error), []
    with pytest.raises(type(error)):
        create_with_retry(create, throttled_tools(), sleep=waits.append)
    assert create.call_count == 1 and waits == []


def test_a_second_rate_limit_fails_the_call_and_the_turn_wait_budget_is_bounded():
    from governedgraph.agents.rate_limit import TURN_WAIT_BUDGET_SECONDS, create_with_retry

    waits, tools = [], throttled_tools()
    again = Mock(side_effect=[Throttled(**{"retry-after": "3"}), Throttled(**{"retry-after": "3"})])
    with pytest.raises(Throttled):
        create_with_retry(again, tools, sleep=waits.append)
    assert waits == [3.5] and again.call_count == 2
    tools.trace.rate_limit_wait = TURN_WAIT_BUDGET_SECONDS - 1
    spent = Mock(side_effect=Throttled(**{"retry-after": "3"}))
    with pytest.raises(Throttled):
        create_with_retry(spent, tools, sleep=waits.append)
    assert spent.call_count == 1 and waits == [3.5]


def test_every_demo_persona_has_its_own_voice_distinct_from_the_counsellor():
    from governedgraph.common import domain

    cards = [PersonaCard.from_dict({"persona_id": item["persona_id"], "goal": item["goal"], "opening": item["opening"],
                                    **item["live"]})
             for item in load_json(DATA_DIR / "personas" / "personas.json")["personas"]]
    voices = [item.voice for item in cards]
    assert len(set(voices)) == len(voices)
    assert domain.demo()["persona_voices"]["counsellor"] not in voices


@pytest.mark.parametrize("changes", [
    {"guide_voice": "not-a-voice"}, {"guide_voice": "en-GB-AlfieNeural"}, {"guide_voice": 5},
])
def test_a_persona_card_rejects_a_malformed_guide_voice_or_one_shared_with_the_persona(changes):
    with pytest.raises(ContractError):
        card(**changes)


def test_the_dutch_persona_speaks_dutch_in_chat_with_a_dutch_guide_voice():
    from dataclasses import asdict as as_dict

    from governedgraph.voice.simulations import Simulation, load_simulations

    daan = next(item for item in load_simulations() if item.simulation_id == "daan")
    built = daan.card()
    assert built.language == "nl" and built.voice.startswith("nl-NL-") and built.guide_voice.startswith("nl-NL-")
    assert daan.channel == "chat" and built.opening.startswith("Goedemiddag")
    # Speech recognition is configured for English, so Dutch must not be offered as a voice conversation.
    with pytest.raises(ContractError):
        Simulation.from_dict({**as_dict(daan), "channel": "voice"})


def test_demo_only_personas_stay_out_of_the_frozen_evaluation_cases_until_they_are_evaluated():
    from pension_wtp.evaluation import run_evals

    profiles = load_json(DATA_DIR / "personas" / "personas.json")["personas"]
    demo_only = {item["persona_id"] for item in profiles if item.get("demo_only")}
    assert "daan" in demo_only
    assert not demo_only & {case["persona"] for case in run_evals.persona_cases()}
