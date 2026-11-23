"""ILLUSTRATIVE - synthetic data. Gates precede every substantive audio sentence."""
import asyncio
from contextlib import nullcontext
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from counsellor_doubles import install, install_tracing, new_session
from governedgraph.common.config import settings
from governedgraph.common.contracts import ContractError
from governedgraph.guardrails.prompt_safety import ScreenResult
from strategy_doubles import run_cycle
from strategy_doubles import local_strategy_service
from governedgraph.voice.benchmark import QUESTIONS, distribution, summarize
from governedgraph.voice.voice_live_bridge import ACKNOWLEDGEMENT, AudioFailure, VoiceBridge, VoiceReply, checked_sentences


def test_checked_sentences_preserve_bound_text_and_decimal_figures():
    text = "Your official amount is EUR 1,412.50. It can go up or down. Would you like a person?"
    sentences = checked_sentences(text)
    assert "".join(sentences) == text
    assert len(sentences) == 3 and "1,412.50" in sentences[0]
    with pytest.raises(AudioFailure):
        checked_sentences("A draft {{value:unbound}}.")


def test_acknowledgement_is_approved_figure_free_and_versioned():
    from governedgraph.agents.counsellor import TEMPLATES
    from governedgraph.guardrails.numeric_fidelity import check
    from governedgraph.guardrails.advice_intent import response_steers_choice

    assert ACKNOWLEDGEMENT == "Thank you. I'm checking your official figures now."
    assert TEMPLATES["template_set_version"] == "1.5"
    assert TEMPLATES["acknowledgements"]["approval_ref"] == "IMP-35-user-approval-2026-10-27"
    assert check(ACKNOWLEDGEMENT, None, []).passed and not response_steers_choice(ACKNOWLEDGEMENT)


def test_ack_plays_during_checks_and_only_complete_checked_reply_is_partitioned():
    async def scenario():
        events = []
        ack_playing = asyncio.Event()
        reply = VoiceReply(text="Checked first sentence. Checked second sentence.", state="resolved",
                           audit_status="committed")

        class Speech:
            checked_sentence_audio = True
            async def synthesize(self, text):
                events.append(("synthesize", text))
                return b"\0\0"
            async def transcribe(self, pcm):
                await ack_playing.wait()
                events.append(("transcribe", ""))
                return "Explain my letter"

        class Device:
            async def play(self, pcm):
                pass
            async def play_text(self, text, pcm, **metadata):
                events.append(("play", text))
                if text == ACKNOWLEDGEMENT:
                    ack_playing.set()

        async def turn(*args):
            events.append(("gates_passed", ""))
            return reply
        bridge = VoiceBridge(SimpleNamespace(
            start=AsyncMock(return_value=SimpleNamespace(journey_id="J-synthetic", text="AI disclosure")),
            turn=turn), Speech(), Device())
        await bridge.start()
        assert events[-1] == ("play", "AI disclosure")
        await bridge.turn(b"\0\0")
        gate = events.index(("gates_passed", ""))
        substantive = [(i, text) for i, (kind, text) in enumerate(events)
                       if kind == "synthesize" and text.startswith(("Checked", " Checked"))]
        assert "".join(text for _, text in substantive) == reply.text
        assert all(i > gate for i, _ in substantive)
        assert events.index(("play", ACKNOWLEDGEMENT)) < events.index(("transcribe", ""))
        assert len(bridge.acknowledgement_seconds) == len(bridge.substantive_first_seconds) == 1
        assert bridge.first_audio_seconds[0] < bridge.substantive_first_seconds[0]
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["screening", "intent", "values", "strategy", "content", None])
def test_screening_and_agent_routing_precede_parallel_tools_and_all_join_before_draft(tmp_path, monkeypatch, failure):
    from governedgraph.common.contracts import IntentDecision

    monkeypatch.setattr(settings.__class__, "run_dir", property(lambda _: tmp_path))
    install(monkeypatch, tmp_path)
    service = local_strategy_service(tmp_path / "strategy", control_share=0)
    run_cycle(service)
    c = new_session("MBR-7Q2K", strategy_service=service)
    c.start()
    barrier = Barrier(3)
    completed = set()
    drafted = Event()

    def wrap(name, operation):
        def run(*args, **kwargs):
            if name in {"values", "strategy", "content"}:
                assert completed >= {"screening", "intent"}
                barrier.wait(timeout=5)
            if name == failure:
                raise TimeoutError("synthetic dependency failure")
            value = operation(*args, **kwargs)
            completed.add(name)
            return value
        return run

    c.screen.screen = wrap("screening", lambda *_, **__: ScreenResult(True))
    c.decision_agents["intent"] = SimpleNamespace(run=wrap("intent", lambda *_: IntentDecision(
        "explanation", "transition_impact_question", "official_transition_information",
    )))
    c.tools.get_member_values = wrap("values", c.tools.get_member_values)
    c.tools.get_ranked_strategies = wrap("strategy", c.tools.get_ranked_strategies)
    c.tools.search_approved_content = wrap("content", c.tools.search_approved_content)
    original = c._compose
    def compose(*args):
        assert completed == {"screening", "intent", "values", "strategy", "content"}
        drafted.set()
        return original(*args)
    c._compose = compose
    result = c._safe_handle("Please explain my transition letter")
    if failure:
        assert result.escalated and result.state == "escalation_requested"
        assert not drafted.is_set() and not any(character.isdigit() for character in result.text)
    else:
        assert drafted.is_set() and result.fidelity.passed and result.strategy_id == "S5"
        assert set(result.stage_ms) >= {"screening", "intent_routing", "values_lookup",
                                        "strategy_lookup", "content_lookup", "post_processing"}


def test_latency_statistics_have_twenty_warm_and_three_cold_without_filtering_failures():
    assert len(QUESTIONS) == len(set(QUESTIONS)) == 20
    assert distribution(list(range(1, 21))) == {"n": 20, "p50_ms": 10, "p95_ms": 19}
    rows = [{"temperature": temperature, "quality_passed": index != 2, "stages": {"screening": index},
             "first_audio_ms": index, "complete_audio_ms": index + 10, "model_rounds": 1,
             "substantive_first_audio_ms": index + 5, "composition_source": "foundry",
             "numeric_fidelity_passed": True, "no_option_recommendation": True}
            for temperature, count in (("cold", 3), ("warm", 20)) for index in range(count)]
    report = summarize(rows)
    assert report["warm"]["turns"] == report["warm"]["first_audio"]["n"] == 20
    assert report["warm"]["quality_passed"] == 19 and report["cold"]["turns"] == 3


@pytest.mark.parametrize("metadata", [{"age": 20}, {"screening": float("nan")}, {"screening": -1}])
def test_voice_timing_contract_cannot_carry_demographics_or_invalid_numbers(metadata):
    with pytest.raises(ContractError):
        VoiceReply(text="Approved.", state="resolved", stage_ms=metadata)


def test_parallel_synthesis_preserves_sentence_order_and_serializes_each_connection():
    from governedgraph.voice.azure_voice import CheckedSpeechPool

    async def scenario():
        active = set()
        all_started = asyncio.Event()
        class Speech:
            def __init__(self, slot):
                self.slot = slot
            async def synthesize(self, text):
                assert self.slot not in active
                active.add(self.slot)
                if len(active) == 4:
                    all_started.set()
                await asyncio.wait_for(all_started.wait(), 1)
                await asyncio.sleep(.001 if text != "First." else .005)
                active.remove(self.slot)
                return text.encode()
        pool = CheckedSpeechPool([Speech(slot) for slot in range(4)])
        sentences = ["First.", "Second.", "Third.", "Fourth.", "Fifth."]
        received = [item async for item in pool.synthesize_sentences(sentences)]
        assert [text for text, _ in received] == sentences
        assert [pcm for _, pcm in received] == [text.encode() for text in sentences]
        assert not active
    asyncio.run(scenario())


def test_cold_clients_are_isolated_closed_and_do_not_clear_warm_cache(monkeypatch):
    from governedgraph.agents import connections
    from unittest.mock import Mock

    warm, cold = Mock(), Mock()
    monkeypatch.setattr(connections, "_shared_http", lambda: warm)
    monkeypatch.setattr(connections, "_new_http", lambda: cold)
    assert connections.http_client() is warm
    with connections.cold_clients():
        assert connections.http_client() is cold
        assert connections.http_client() is cold
        assert not warm.close.called
    cold.close.assert_called_once()
    assert connections.http_client() is warm
    warm.close.assert_not_called()


def test_warmup_refuses_agent_failure_and_never_opens_member_values(monkeypatch):
    from governedgraph.voice import warmup
    from governedgraph.agents.foundry_runtime import FoundryUnavailable
    from governedgraph.voice.voice_live_bridge import VoiceError

    def unavailable(*_):
        raise FoundryUnavailable("synthetic agent outage")
    monkeypatch.setattr(warmup, "operation", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(warmup.FoundryComposer, "shared", unavailable)
    with pytest.raises(VoiceError, match="warm-up agent"):
        warmup.warm_runtime()


def test_warmup_uses_valid_shared_correlation_without_creating_a_member_journey(monkeypatch):
    import re
    from unittest.mock import Mock
    from governedgraph.agents import decision_model
    from governedgraph.voice import warmup

    screen = Mock()
    screen.screen.return_value.allowed = True
    choose = Mock(side_effect=lambda _tools, _state, questions: (
        {"route": "asks_information"} if "route" in questions
        else {"topic": "transition_information_gap"}))
    monkeypatch.setattr(warmup, "operation", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(decision_model, "choose", choose)
    monkeypatch.setattr(warmup.FoundryComposer, "shared", Mock())
    monkeypatch.setattr(warmup, "AzurePromptShields", lambda endpoint: screen)
    warmup.warm_runtime()
    assert re.fullmatch(r"J-[a-f0-9]{32}", screen.journey_id)
    screen.screen.assert_called_once()
    assert choose.call_count == 2
    assert all(call.args[0].journey.journey_id == screen.journey_id for call in choose.call_args_list)
    assert {call.args[1].splitlines()[-1].split(":")[0] for call in choose.call_args_list} == {"Customer message"}


def test_checked_sentences_are_transmitted_before_playback_finishes_but_all_acks_are_required():
    from governedgraph.voice.presenter import SocketAudio

    async def scenario():
        sent = []
        acknowledged = []
        class Socket:
            async def send_json(self, message):
                sent.append(message)
            async def send_bytes(self, pcm):
                sent.append(pcm)
            async def receive_json(self):
                message = sent[len(acknowledged) * 2]
                acknowledged.append(message["id"])
                return {"kind": "played", "id": message["id"]}
        device = SocketAudio(Socket())
        device.begin_reply()
        await device.play_text("First checked sentence.", b"\0\0", final_sentence=False)
        await device.play_text("Last checked sentence.", b"\0\0")
        assert len(sent) == 4 and not acknowledged
        assert sent[0]["final_sentence"] is False and sent[2]["final_sentence"] is True
        await device.finish_reply()
        assert len(acknowledged) == 2 and not device.queued and not device.pending_playback
    asyncio.run(scenario())


def test_durable_sender_reuses_confirmed_connection_and_failure_keeps_message_identity(monkeypatch):
    install_tracing(monkeypatch)
    from threading import Lock
    from unittest.mock import MagicMock, Mock
    from azure.servicebus.exceptions import ServiceBusError
    from governedgraph.orchestrator.messaging import ServiceBusJourneyPublisher
    from governedgraph.orchestrator.store import Delivery

    first, replacement = MagicMock(), MagicMock()
    publisher = ServiceBusJourneyPublisher.__new__(ServiceBusJourneyPublisher)
    publisher.client = Mock(get_queue_sender=Mock(side_effect=[first, replacement]))
    publisher.credential = Mock()
    publisher._lock, publisher._senders = Lock(), {}
    publisher._last_used = {}
    item = Delivery("journey-events", "J-synthetic:event-1", {"label": "ILLUSTRATIVE - synthetic data"})
    publisher.send("J-synthetic", item)
    publisher.send("J-synthetic", item)
    assert publisher.client.get_queue_sender.call_count == 1
    assert first.__enter__.call_count == 1
    first.send_messages.side_effect = ServiceBusError("synthetic failure")
    with pytest.raises(ServiceBusError):
        publisher.send("J-synthetic", item)
    assert not publisher._senders
    first.close.assert_called_once()
    publisher.send("J-synthetic", item)
    message = replacement.send_messages.call_args.args[0]
    assert message.message_id == item.message_id and message.session_id == "J-synthetic"
    publisher.close()
    replacement.close.assert_called_once()


@pytest.mark.parametrize("failure_point", ["open", "cached_send"])
def test_durable_sender_reconnects_once_without_losing_response_or_message_identity(failure_point, monkeypatch):
    install_tracing(monkeypatch)
    from threading import Lock
    from unittest.mock import MagicMock, Mock
    from azure.servicebus.exceptions import ServiceBusConnectionError
    from governedgraph.orchestrator.messaging import ServiceBusJourneyPublisher
    from governedgraph.orchestrator.store import Delivery

    first, replacement = MagicMock(), MagicMock()
    publisher = ServiceBusJourneyPublisher.__new__(ServiceBusJourneyPublisher)
    publisher.client = Mock(get_queue_sender=Mock(side_effect=[first, replacement]))
    publisher._lock, publisher._senders = Lock(), {}
    publisher._last_used = {}
    item = Delivery("journey-events", "J-synthetic:event-1", {"label": "ILLUSTRATIVE - synthetic data"})
    if failure_point == "cached_send":
        publisher.send("J-synthetic", item)
        first.send_messages.side_effect = ServiceBusConnectionError(message="synthetic idle disconnect")
    else:
        first.__enter__.side_effect = ServiceBusConnectionError(message="synthetic opening disconnect")
    publisher.send("J-synthetic", item)
    assert publisher.client.get_queue_sender.call_count == 2
    first.close.assert_called_once()
    message = replacement.send_messages.call_args.args[0]
    assert message.message_id == item.message_id and message.session_id == "J-synthetic"
    if failure_point == "cached_send":
        assert first.send_messages.call_args.args[0] is message
    assert publisher._senders == {"journey-events": replacement}


def test_durable_sender_repeated_connection_failure_is_not_silently_accepted(monkeypatch):
    install_tracing(monkeypatch)
    from threading import Lock
    from unittest.mock import MagicMock, Mock
    from azure.servicebus.exceptions import ServiceBusConnectionError
    from governedgraph.orchestrator.messaging import ServiceBusJourneyPublisher
    from governedgraph.orchestrator.store import Delivery

    first, replacement = MagicMock(), MagicMock()
    for sender in (first, replacement):
        sender.send_messages.side_effect = ServiceBusConnectionError(message="synthetic unavailable")
    publisher = ServiceBusJourneyPublisher.__new__(ServiceBusJourneyPublisher)
    publisher.client = Mock(get_queue_sender=Mock(side_effect=[first, replacement]))
    publisher._lock, publisher._senders = Lock(), {}
    publisher._last_used = {}
    with pytest.raises(ServiceBusConnectionError):
        publisher.send("J-synthetic", Delivery("journey-events", "J-synthetic:event-1", {}))
    assert publisher.client.get_queue_sender.call_count == 2
    assert not publisher._senders
    first.close.assert_called_once()
    replacement.close.assert_called_once()


def test_benchmark_warms_after_three_cold_controls_and_keeps_all_twenty_warm_turns(monkeypatch):
    from contextlib import asynccontextmanager
    from governedgraph.voice import benchmark, warmup

    messages = []
    events = []
    speech = SimpleNamespace(synthesize=AsyncMock(return_value=b"\0\0"))

    @asynccontextmanager
    async def open_speech(*args, **kwargs):
        yield speech

    reply = VoiceReply(text="Checked synthetic explanation.", state="resolved",
                       strategy_id="S5", ranked=["S5"], suspended=["S2"],
                       audit_status="committed", numeric_fidelity_passed=True,
                       no_option_recommendation=True)
    bridge = SimpleNamespace(
        journey_id="J-synthetic", start=AsyncMock(), turn=AsyncMock(return_value=reply),
        stage_samples=[{}], first_audio_seconds=[.001],
        substantive_first_seconds=[.05], turn_seconds=[.1])

    async def send(message):
        messages.append(message)

    def warm():
        assert bridge.turn.await_count == 3
        events.append("warm")

    monkeypatch.setattr(benchmark, "open_speech", open_speech)
    monkeypatch.setattr(benchmark, "VoiceBridge", lambda *args: bridge)
    monkeypatch.setattr(warmup, "warm_runtime", warm)
    client = SimpleNamespace(base_url=SimpleNamespace(scheme="https"), follow_redirects=False)
    asyncio.run(benchmark.run_benchmark(SimpleNamespace(send_json=send), None, client,
                                       "synthetic-placeholder", "", runtime=object()))
    assert events == ["warm"]
    result = messages[-1]
    assert result["kind"] == "benchmark_complete"
    assert result["summary"]["cold"]["turns"] == 3
    assert result["summary"]["warm"]["turns"] == 20
    assert bridge.turn.await_count == 23
