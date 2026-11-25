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
    actor = LivePersona(card(), client=client)

    first = asyncio.run(actor.next_turn(""))
    assert first == PersonaTurn("Explain the letter.") and not client.responses.create.called
    second = asyncio.run(actor.next_turn("The letter explains the conversion of your rights."))
    assert second == PersonaTurn("Could you say that more simply?")
    inputs = client.responses.create.call_args.kwargs
    assert inputs["store"] is False and inputs["extra_body"]["agent_reference"] == {"type": "agent_reference", "name": "gg-persona-demo"}
    payload = json.loads(inputs["input"][0]["content"])
    assert set(payload) == {"persona_card", "you_opened_with", "guide_replied", "turns_remaining"}
    assert payload["guide_replied"] == "The letter explains the conversion of your rights."

    asyncio.run(actor.next_turn("Rights are converted into the new contract."))
    user_turns = [item for item in client.responses.create.call_args.kwargs["input"] if item["role"] == "user"]
    assert len(user_turns) == 2 and set(json.loads(user_turns[-1]["content"])) == {"guide_replied", "turns_remaining"}


def test_persona_always_concludes_at_the_turn_limit_even_if_the_agent_does_not():
    client = Mock()
    client.responses.create.return_value = answer("One more question.", done=False)
    actor = LivePersona(card(max_turns=3), client=client)
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
    actor = LivePersona(card(), client=client)
    asyncio.run(actor.next_turn(""))
    with pytest.raises(ContractError):
        asyncio.run(actor.next_turn("Reply."))


def test_persona_response_with_two_messages_uses_the_first_instead_of_failing_to_parse():
    # Seen in gg-dev: the agent answered twice in one response; the SDK's output_text joined both into invalid JSON.
    def message(utterance):
        return SimpleNamespace(type="message", content=[SimpleNamespace(
            type="output_text", text=json.dumps({"utterance": utterance, "done": False}))])

    first, second = message("Yes, that is right."), message("Yes, I understand it.")
    client = Mock()
    client.responses.create.return_value = SimpleNamespace(
        status="completed", output_text=first.content[0].text + second.content[0].text, output=[first, second])
    actor = LivePersona(card(), client=client)
    asyncio.run(actor.next_turn(""))
    turn = asyncio.run(actor.next_turn("Reply."))
    assert turn.utterance == "Yes, that is right."
    assert actor.messages[-1] == {"role": "assistant", "content": first.content[0].text}  # the duplicate is dropped


def test_persona_failures_are_reported_not_substituted_with_invented_replies(fake_openai):
    client = Mock()
    actor = LivePersona(card(), client=client)
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
    for name in ("deployVoicePersona", "personaAgentPublished"):
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
    return seen


def test_persona_uses_only_its_own_agent_endpoint_under_its_own_identity(azure_clients):
    from governedgraph.agents.live_persona import connect_live_persona

    actor = connect_live_persona(card())
    assert azure_clients["cred"] == {"client_id": "persona-client"}
    assert azure_clients["openai"]["agent_name"] == "gg-persona-demo" and azure_clients["project"]["allow_preview"]
    assert actor.agent_name == "gg-persona-demo"


@pytest.mark.parametrize("missing", ["AZURE_PERSONA_CLIENT_ID"])
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


@pytest.fixture
def confirmation_workflow(monkeypatch):
    from contextlib import contextmanager

    from governedgraph.agents.counsellor import TurnResult
    from governedgraph.agents.tools import Trace
    from governedgraph.common.contracts import ExplanationContext, IntentDecision, UnderstandingDecision
    from governedgraph.common.domain import message
    from governedgraph.orchestrator.state_machine import Event, Journey, State
    from governedgraph.orchestrator import workflow

    @contextmanager
    def no_tracing(*args, **kwargs):
        yield None

    monkeypatch.setattr(workflow, "operation", no_tracing)
    journey = Journey(
        journey_id="J-thanks", member_ref="member", state=State.RESOLVED,
        last_explanation=ExplanationContext(
            ["official_after_transition_amount", "future_adjustments"], []),
    )
    decisions = {
        "intent": IntentDecision("comprehension", "unclassified", "restating_previous_explanation", "en"),
        "understanding": UnderstandingDecision("uncertain", "insufficient_evidence"),
    }

    def explain(*args, **kwargs):
        journey.fire(Event.NO_TEACH_BACK)
        return TurnResult("An approved explanation.", journey.state.value)

    def escalate(*args, **kwargs):
        journey.fire(Event.ESCALATE)
        return TurnResult("A person can help.", journey.state.value, "S6", escalated=True)

    counsellor = SimpleNamespace(
        journey=journey,
        tools=SimpleNamespace(agent_name="gg-counsellor", trace_parent=None, auth_ref="member", trace=Trace(),
                              conversation_history=(), conversation_screened=False),
        screen=SimpleNamespace(screen=lambda text, ref: SimpleNamespace(allowed=True, reason="")),
        decide=Mock(side_effect=lambda kind, text: decisions[kind]),
        decisions=decisions,
        catalogue=SimpleNamespace(get=lambda _: SimpleNamespace(strategy_id="S5", version="1.1")),
        _current_strategy="S5",
        _turn_exclusions=set(),
        _complete_turn=Mock(side_effect=lambda result, count: result),
        _explain=Mock(side_effect=explain),
        _clarify=Mock(side_effect=lambda text: TurnResult(message("clarify_question"), journey.state.value)),
        _escalate=Mock(side_effect=escalate),
    )
    return counsellor


@pytest.mark.parametrize("allowed", [True, False])
def test_customer_history_is_screened_before_semantic_routing(confirmation_workflow, allowed):
    from governedgraph.agents.conversation_context import ConversationMessage
    from governedgraph.orchestrator.workflow import run_member_turn

    c = confirmation_workflow
    c.tools.conversation_history = (
        ConversationMessage("user", "Please explain my synthetic letter."),
        ConversationMessage("assistant", "An earlier checked synthetic explanation."),
    )
    c.screen.screen = Mock(side_effect=[
        SimpleNamespace(allowed=True, reason=""),
        SimpleNamespace(allowed=allowed, reason="" if allowed else "prompt_shields_document_attack"),
    ])
    result = run_member_turn(c, "Thank you, that helps.")
    assert c.screen.screen.call_count == 2
    assert c.screen.screen.call_args.args[2] == [item.text for item in c.tools.conversation_history]
    assert c.tools.conversation_screened is allowed
    assert result.escalated is (not allowed)
    assert c.decide.call_count == (1 if allowed else 0)


@pytest.mark.parametrize("language,utterance,closing", [
    ("en", "Yes, that helps a lot. Thank you.",
     "Thank you. We can leave it here for now. You can ask another question or request a person at any time."),
    ("en", "Thank you, that helps. So the monthly amount won't suddenly change because of the transition itself, "
     "but it could go up or down later depending on how well the fund does. That makes me feel a bit better.",
     "Thank you. We can leave it here for now. You can ask another question or request a person at any time."),
    ("nl", "Dank je, dat helpt. Ik begrijp het nu.",
     "Dank je. We kunnen het hier voor nu bij laten. Je kunt een andere vraag stellen of op elk moment om een medewerker vragen."),
])
def test_confirmation_after_a_resolved_answer_closes_naturally_without_claiming_comprehension(
        confirmation_workflow, language, utterance, closing):
    from governedgraph.guardrails.advice_intent import response_steers_choice
    from governedgraph.guardrails.numeric_fidelity import check
    from governedgraph.orchestrator.workflow import member_turn

    c = confirmation_workflow
    c.journey.response_language = language  # chosen at session start
    result = asyncio.run(member_turn(c, utterance))
    assert result.text == closing and result.state == "resolved" and not result.escalated
    assert check(result.text, None, []).passed and not response_steers_choice(result.text)
    assert result.workflow_steps == [
        "screen-input", "intent-agent", "acknowledge-explanation", "commit-checked-evidence",
    ]
    assert [call.args[0] for call in c.decide.call_args_list] == ["intent"]
    assert c.tools.trace.turn_trace[-1]["result"] == "conversation_acknowledged"
    assert not c.journey.events and not c._explain.called and not c._escalate.called
    assert c._complete_turn.call_count == 1


@pytest.mark.parametrize("language,utterance", [
    ("en", "Thanks for explaining. I understand that right after the transition, I'll get the same amount they "
     "calculated for me, and it won't be less just because of the change. Later, it might go up or down depending "
     "on how the fund does. Is that right?"),
    ("en", "So it stays the same on the transition date, right?"),
    ("nl", "Dank je voor de uitleg. Later kan het omhoog of omlaag gaan. Klopt dat?"),
])
def test_b2_b5_b9_resolved_confirmation_questions_get_an_answer_not_a_closing(
        confirmation_workflow, language, utterance):
    from governedgraph.common.contracts import IntentDecision
    from governedgraph.orchestrator.workflow import member_turn

    c = confirmation_workflow
    # The router reads a confirmation question as a request for information, so it is answered, not closed.
    c.decisions["intent"] = IntentDecision("explanation", "transition_impact_question", "payment_continuity", language)
    result = asyncio.run(member_turn(c, utterance))
    assert result.text == "An approved explanation." and result.state == "resolved" and not result.escalated
    assert result.workflow_steps == [
        "screen-input", "intent-agent", "explain-with-approved-tools", "commit-checked-evidence",
    ]
    assert [call.args[0] for call in c.decide.call_args_list] == ["intent"]
    assert c.journey.intent == "transition_impact_question"
    c._explain.assert_called_once_with(utterance, exclude=set())
    assert not any(event.event in {"teach_back_passed", "teach_back_failed"} for event in c.journey.events)
    assert not c._escalate.called and c._complete_turn.call_count == 1


@pytest.mark.parametrize("outcome,reason", [
    ("understood", "meaning_correct"),
    ("uncertain", "insufficient_evidence"),
    ("needs_explanation", "meaning_not_yet_understood"),
])
def test_b5_pending_meaning_checks_still_require_the_understanding_verdict(confirmation_workflow, outcome, reason):
    from governedgraph.common.contracts import ExplanationContext, UnderstandingDecision
    from governedgraph.agents.counsellor import TEMPLATES
    from governedgraph.orchestrator.state_machine import State
    from governedgraph.orchestrator.workflow import member_turn

    c = confirmation_workflow
    c.journey.state = State.COMPREHENSION_CHECK
    c.journey.last_explanation = ExplanationContext(
        ["official_after_transition_amount", "future_adjustments"], ["official_after_transition_amount"])
    c.decisions["understanding"] = UnderstandingDecision(outcome, reason)
    utterance = ("The amount immediately after the transition is the official starting amount. Is that right?"
                 if outcome == "understood"
                 else "Thank you." if outcome == "uncertain" else "The transition means the starting amount is gone.")
    result = asyncio.run(member_turn(c, utterance))
    assert [call.args[0] for call in c.decide.call_args_list] == ["intent", "understanding"]
    passed = [event for event in c.journey.events if event.event == "teach_back_passed"]
    assert bool(passed) is (outcome == "understood")
    if outcome == "understood":
        assert result.state == "resolved" and result.text == TEMPLATES["acknowledgements"]["comprehension_confirmed"]["en"]
        assert "leave it here" not in result.text
        assert "clarify-question" not in result.workflow_steps and not c._explain.called
    elif outcome == "uncertain":
        assert result.state == "comprehension_check" and "clarify-question" in result.workflow_steps
        assert result.text == TEMPLATES["acknowledgements"]["comprehension_retry"][c.journey.response_language]
        assert c.journey.teach_back_failures == 0
    else:
        assert "explain-with-approved-tools" in result.workflow_steps and c.journey.teach_back_failures == 1
    assert c._complete_turn.call_count == 1


def test_a_confirmation_without_a_previous_answer_does_not_close_the_journey(confirmation_workflow):
    from governedgraph.common.domain import message
    from governedgraph.orchestrator.state_machine import State
    from governedgraph.orchestrator.workflow import member_turn

    c = confirmation_workflow
    c.journey.state, c.journey.last_explanation = State.IDENTIFIED, None
    result = asyncio.run(member_turn(c, "Thank you."))
    assert result.state == "identified" and "clarify-question" in result.workflow_steps
    assert result.text == message("clarify_question")
    assert "acknowledge-explanation" not in result.workflow_steps
    assert [call.args[0] for call in c.decide.call_args_list] == ["intent"]


@pytest.mark.parametrize("route,intent,reason,utterance,branch", [
    ("explanation", "transition_impact_question", "payment_continuity",
     "Thanks, but please explain what happens to my payment again.", "explain-with-approved-tools"),
    ("human", "unclassified", "explicit_human_request",
     "Thank you. I'd like to speak to a person.", "requested-human-handoff"),
    ("advice", "option_level_question", "personal_choice_or_comparison",
     "Thanks. Should I object or not?", "personal-advice-handoff"),
])
def test_b9_natural_closure_preserves_new_questions_and_human_or_advice_handover(
        confirmation_workflow, route, intent, reason, utterance, branch):
    from governedgraph.common.contracts import IntentDecision
    from governedgraph.orchestrator.workflow import member_turn

    c = confirmation_workflow
    first = asyncio.run(member_turn(c, "Thanks, that helps."))
    assert first.state == "resolved" and "acknowledge-explanation" in first.workflow_steps
    c.decisions["intent"] = IntentDecision(route, intent, reason)
    result = asyncio.run(member_turn(c, utterance))
    assert branch in result.workflow_steps and "acknowledge-explanation" not in result.workflow_steps
    assert result.escalated is (route != "explanation")
    assert c._complete_turn.call_count == 2


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


@pytest.mark.usefixtures("sample_simulations")
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
