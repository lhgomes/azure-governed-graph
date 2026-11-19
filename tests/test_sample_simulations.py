"""ILLUSTRATIVE - synthetic data. Sample scripts never substitute for member authorization."""
import asyncio
import json
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from scripts import deploy
from test_deploy import COMPOSITION
from test_voice_live import Device, Speech
from test_voice_live import host as host  # noqa: PLC0414 - pytest fixture re-export
from test_voice_presenter import presenter_auth as presenter_auth  # noqa: PLC0414 - pytest fixture re-export

from governedgraph.agents.counsellor import DISCLOSURE
from governedgraph.agents.live_persona import LivePersona, PersonaTurn
from governedgraph.agents.persona_simulator import ScriptedPersona
from governedgraph.common import domain
from governedgraph.common.config import DATA_DIR
from governedgraph.common.contracts import DEMOGRAPHIC_FIELD_NAMES, ContractError
from governedgraph.common.member_auth import MemberIdentity
from governedgraph.dev import member_issuer_api
from governedgraph.orchestrator import api
from governedgraph.voice import presenter, simulations
from governedgraph.voice.simulations import Simulation, load_simulations, select_simulation
from governedgraph.voice.voice_demo import run_live_simulator, run_simulator
from governedgraph.voice.voice_live_bridge import OrchestratorChannel, VoiceBridge, VoiceError, VoiceReply

ROOT = Path(__file__).resolve().parents[1]


FAREWELL = "Thank you, that is all I needed. Goodbye."


@pytest.mark.parametrize("simulation_id", ["annelies", "joost", "samira"])
@pytest.mark.parametrize("final_reply", [
    "I can only explain these figures, not change them.",
    "Is there anything else I can explain?",
])
def test_sample_goodbye_does_not_assume_the_counsellor_asked_a_question(simulation_id, final_reply):
    async def scenario():
        simulation = select_simulation(simulation_id)
        speech = SimpleNamespace(synthesize=AsyncMock(return_value=b"\0\0"),
                                 transcribe=AsyncMock(return_value=simulation.opening))
        channel = SimpleNamespace(
            start=AsyncMock(return_value=SimpleNamespace(journey_id="J-neutral-closing", text=DISCLOSURE)),
            turn=AsyncMock(return_value=VoiceReply(text=final_reply, state="resolved")),
        )
        actor = Speech()
        bridge = VoiceBridge(channel, speech, SimpleNamespace(play=AsyncMock()), spoken_acknowledgement=False)
        script = ScriptedPersona({"opening": simulation.opening, "teach_back_answer": "I understood.",
                                  "closing": "Thank you for explaining. Goodbye."})
        result = await run_simulator(bridge, actor, script=script, channel=simulation.channel)
        assert actor.spoken[-1] == "Thank you for explaining. Goodbye."
        assert result.session_ended is True
        assert result.replies[-1].text == final_reply
    asyncio.run(scenario())


@pytest.mark.parametrize("first_state", ["comprehension_check", "resolved"])
def test_narrated_chat_preserves_script_and_ends_only_after_follow_up_and_teach_back(first_state):
    async def scenario():
        speech = SimpleNamespace(
            checked_sentence_audio=True,
            synthesize=AsyncMock(return_value=b"\0\0"),
            transcribe=AsyncMock(side_effect=AssertionError("narrated chat must not use speech recognition")),
        )
        channel = SimpleNamespace(
            start=AsyncMock(return_value=SimpleNamespace(journey_id="J-samira-script", text=DISCLOSURE)),
            turn=AsyncMock(side_effect=[
                VoiceReply(text="I do not have your letter. Which section would you like explained?",
                           state=first_state),
                VoiceReply(text="Your accrued rights are converted into personal pension capital. What did you understand?",
                           state="comprehension_check"),
                VoiceReply(text="That is right. Is there anything else I can explain?", state="resolved"),
            ]),
        )
        actor = Speech()
        device = SimpleNamespace(play=AsyncMock())
        script = ScriptedPersona({
            "opening": "Can you explain the invaren letter in English please?",
            "follow_up_questions": ["What does personal pension capital after the transition actually mean for me?"],
            "teach_back_answer": "What I built up is converted into personal pension capital.",
            "closing": "Thank you for explaining. Goodbye.",
        })
        bridge = VoiceBridge(channel, speech, device, spoken_acknowledgement=False)
        result = await run_simulator(bridge, actor, script=script, channel="chat", accessibility_mode="english")
        assert [call.args[1] for call in channel.turn.await_args_list] == [
            script.persona["opening"], script.persona["follow_up_questions"][0], script.persona["teach_back_answer"],
        ]
        assert actor.spoken == [
            script.persona["opening"], script.persona["follow_up_questions"][0],
            script.persona["teach_back_answer"], script.persona["closing"],
        ]
        speech.transcribe.assert_not_called()
        assert result.session_ended is True
        assert result.metrics["turn_count"] == 3 and result.metrics["slo_certified"] is False
        assert result.metrics["measurement"] == "text_input_to_complete_audio_ready"
    asyncio.run(scenario())


def test_sample_script_exhaustion_never_reports_resolution_or_plays_a_successful_goodbye():
    async def scenario():
        speech = SimpleNamespace(synthesize=AsyncMock(return_value=b"\0\0"),
                                 transcribe=AsyncMock(return_value="I am not sure"))
        channel = SimpleNamespace(
            start=AsyncMock(return_value=SimpleNamespace(journey_id="J-incomplete-script", text=DISCLOSURE)),
            turn=AsyncMock(return_value=VoiceReply(text="Please explain what you understood.",
                                                 state="comprehension_check")),
        )
        actor = Speech()
        script = ScriptedPersona({"opening": "Explain the transition.", "teach_back_answer": "I am not sure",
                                  "closing": "Thank you for explaining. Goodbye."})
        bridge = VoiceBridge(channel, speech, SimpleNamespace(play=AsyncMock()), spoken_acknowledgement=False)
        with pytest.raises(VoiceError, match="before resolution"):
            await run_simulator(bridge, actor, script=script, channel="chat")
        assert channel.turn.await_count == 4
        assert script.persona["closing"] not in actor.spoken
    asyncio.run(scenario())


def test_honest_non_answer_ends_a_scripted_story_without_error_or_a_successful_goodbye():
    async def scenario():
        speech = SimpleNamespace(synthesize=AsyncMock(return_value=b"\0\0"),
                                 transcribe=AsyncMock(return_value="unused"))
        channel = SimpleNamespace(
            start=AsyncMock(return_value=SimpleNamespace(journey_id="J-honest-stop", text=DISCLOSURE)),
            turn=AsyncMock(side_effect=[
                VoiceReply(text="Your rights are converted.", state="explaining"),
                VoiceReply(text="What would you like me to explain?", state="explaining",
                           composition_source="unanswered"),
            ]),
        )
        actor = Speech()
        script = ScriptedPersona({"opening": "Explain the transition.",
                                  "follow_up_questions": ["What does it mean for me?"],
                                  "teach_back_answer": "Rights are converted.",
                                  "closing": "Thank you for explaining. Goodbye."})
        bridge = VoiceBridge(channel, speech, SimpleNamespace(play=AsyncMock()), spoken_acknowledgement=False)
        result = await run_simulator(bridge, actor, script=script, channel="chat")
        assert channel.turn.await_count == 2
        assert result.session_ended is False and script.persona["closing"] not in actor.spoken
    asyncio.run(scenario())


@pytest.mark.parametrize("changes", [
    {"manner": ""}, {"manner": None}, {"goal": "x" * 601}, {"language": "fr"},
    {"max_turns": 1}, {"max_turns": 9}, {"behaviours": "not a list"}, {"behaviours": ["a"] * 6},
    {"manner": "Aged 45 and polite."}, {"concern": "Has 3 questions."},
    {"age": 45},
])
def test_b7_persona_cards_are_closed_bounded_and_carry_no_demographics(changes):
    simulation = asdict(select_simulation("samira"))
    with pytest.raises(ContractError):
        Simulation.from_dict({**simulation, "persona": {**simulation["persona"], **changes}})


def test_b7_simulation_rejects_scripted_fields_and_a_foreign_card():
    simulation = asdict(select_simulation("samira"))
    with pytest.raises(ContractError):
        Simulation.from_dict({**simulation, "closing": "Goodbye."})
    with pytest.raises(ContractError):
        Simulation.from_dict({**simulation, "persona": {**simulation["persona"], "persona_id": "joost"}})


def test_real_microphone_transcription_still_emits_one_member_caption():
    async def scenario():
        socket = SimpleNamespace(send_json=AsyncMock())
        speech = SimpleNamespace(transcribe=AsyncMock(return_value="A real microphone message."))
        captioned = presenter.CaptionedSpeech(speech, presenter.SocketAudio(socket), "counsellor")
        assert await captioned.transcribe(b"\0\0") == "A real microphone message."
        socket.send_json.assert_awaited_once_with({
            "kind": "caption", "role": "member", "text": "A real microphone message.",
        })
    asyncio.run(scenario())


@pytest.mark.parametrize("input_mode", ["text", "audio", "mixed"])
def test_narrated_text_cannot_certify_voice_slo_even_with_twenty_fast_samples(input_mode):
    async def scenario():
        speech = SimpleNamespace(synthesize=AsyncMock(return_value=b"\0\0"),
                                 transcribe=AsyncMock(return_value="A member question."))
        channel = SimpleNamespace(
            start=AsyncMock(return_value=SimpleNamespace(journey_id="J-slo-script", text=DISCLOSURE)),
            turn=AsyncMock(return_value=VoiceReply(text="A checked explanation.", state="resolved")),
        )
        bridge = VoiceBridge(channel, speech, SimpleNamespace(play=AsyncMock()),
                             clock=lambda: 0.0, spoken_acknowledgement=False)
        await bridge.start()
        for index in range(20):
            if input_mode == "text" or (input_mode == "mixed" and index < 10):
                await bridge.turn_text("A member question.")
            else:
                await bridge.turn(b"\0\0")
        metrics = bridge.metrics()
        assert metrics["turn_count"] == 20 and metrics["voice_turn_p95_ms"] == 0.0
        assert metrics["target_met"] is True
        assert metrics["slo_certified"] is (input_mode == "audio")
        if input_mode == "text":
            speech.transcribe.assert_not_called()
    asyncio.run(scenario())


def test_b7_build_projection_contains_all_personas_but_no_profiles_or_cohorts(tmp_path, monkeypatch):
    output = tmp_path / "simulations.json"
    result = subprocess.run([
        sys.executable, str(ROOT / "examples/pension_wtp/demo/build_simulations.py"),
        "--output", str(output),
    ], env={**os.environ, "GOVERNEDGRAPH_MODE": "local",
            "PYTHONPATH": f"{ROOT / 'src'}:{ROOT / 'examples'}"}, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    body = json.loads(output.read_text())
    personas = json.loads((DATA_DIR / "personas/personas.json").read_text())["personas"]
    assert body["watermark"] == simulations.WATERMARK
    assert len(body["simulations"]) == len(personas) * 2
    for row, profile in zip(body["simulations"], [profile for profile in personas for _ in range(2)]):
        returning = row["contact_type"] == "second_contact"
        scenario = profile["second_contact"] if returning else profile
        assert set(row) == set(Simulation.__dataclass_fields__)
        assert not (set(row) & DEMOGRAPHIC_FIELD_NAMES)
        assert "profile" not in row and "goal" not in row and "tests" not in row
        assert row["simulation_id"] == profile["persona_id"] + ("-return" if returning else "")
        assert row["persona"]["opening"] == scenario["opening"]
        assert row["persona"]["goal"] == scenario["goal"]
        assert row["member_ref"] == profile["member_ref"]
        assert not (set(row["persona"]) & DEMOGRAPHIC_FIELD_NAMES)
    (tmp_path / "demo").mkdir()
    output.rename(tmp_path / "demo/simulations.json")
    monkeypatch.setattr(simulations, "DATA_DIR", tmp_path)
    monkeypatch.setattr(simulations, "settings", SimpleNamespace(is_azure=True))
    assert [asdict(item) for item in load_simulations()] == body["simulations"]


def test_b7_azure_never_falls_back_to_raw_persona_profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(simulations, "DATA_DIR", tmp_path)
    monkeypatch.setattr(simulations, "settings", SimpleNamespace(is_azure=True))
    with pytest.raises(FileNotFoundError):
        load_simulations()


@pytest.mark.parametrize("data_directory", [
    DATA_DIR.resolve(), DATA_DIR.resolve().relative_to(ROOT),
], ids=["absolute-data-directory", "relative-data-directory"])
def test_sample_build_hash_tracks_persona_and_projection_sources(monkeypatch, data_directory):
    builds = []

    def build(runner, registry, target, dockerfile, paths, args, *, subscription):
        assert subscription == "selected-subscription"
        builds.append((target, dockerfile, paths))
        return {"image": registry + "/" + target}

    monkeypatch.setattr(deploy, "build", build)
    deploy.image_artifacts(None, {"compute": {"registryName": "runtime"}}, COMPOSITION,
                           subscription="selected-subscription")
    checked_targets = set()
    for target, dockerfile, paths in builds:
        if dockerfile == COMPOSITION.dockerfile and target in ("runtime", "probe"):
            checked_targets.add(target)
            assert (data_directory / "personas/personas.json").resolve() in paths
            assert (data_directory.parent / "demo/build_simulations.py").resolve() in paths
    assert checked_targets == {"runtime", "probe"}
    dockerfile = (ROOT / COMPOSITION.dockerfile).read_text()
    runtime = dockerfile.split("FROM sample-base AS runtime", 1)[1].split("FROM runtime AS probe", 1)[0]
    assert "simulations.json" in runtime and "personas" not in runtime


def test_presenter_config_lists_every_sample_without_member_refs_or_profiles(monkeypatch):
    monkeypatch.setattr(presenter, "enabled", lambda: True)
    for key in ("VOICE_DEMO_CLIENT_ID", "STRATEGY_TENANT_ID", "VOICE_DEMO_ORIGIN"):
        monkeypatch.setenv(key, "synthetic")
    rows = json.loads(presenter.config().body)["simulations"]
    personas = json.loads((DATA_DIR / "personas/personas.json").read_text())["personas"]
    assert {row["simulation_id"] for row in rows} == {
        profile["persona_id"] + suffix for profile in personas for suffix in ("", "-return")
    }
    assert all(set(row) == {
        "simulation_id", "display_name", "channel", "accessibility_mode", "contact_type",
    } for row in rows)


@pytest.mark.parametrize("body", [
    {"mode": "simulator", "simulation_id": "unknown"},
    {"mode": "simulator", "simulation_id": "../private"},
    {"mode": "simulator", "simulation_id": 1},
    {"mode": "simulator", "simulation_id": "samira", "member_ref": "MBR-7Q2K"},
    {"mode": "simulator", "simulation_id": "samira", "age": 45},
    {"mode": "simulator", "simulation_id": "samira", "profile": "not permitted"},
    {"mode": "simulator", "simulation_id": "samira", "script": {
        "opening": "Changed", "teach_back_answer": "Changed",
    }},
    {"mode": "chat", "simulation_id": "samira"},
    {"mode": "presenter", "simulation_id": "samira"},
    {"mode": "benchmark", "simulation_id": "samira"},
    {"mode": "simulator", "simulation_id": "samira-return"},
    {"mode": "simulator", "simulation_id": "samira", "contact_type": "second_contact"},
    {"mode": "chat", "contact_type": "third_contact"},
    {"mode": "chat", "contact_type": {"age": 45}},
])
def test_b7_browser_cannot_override_server_side_simulation_mapping(body):
    with pytest.raises((ContractError, ValueError)):
        presenter.SessionRequest.from_dict(body)


def test_second_contact_uses_a_distinct_actor_but_the_same_authorized_member():
    for initial in (item for item in load_simulations() if item.contact_type == "first_contact"):
        returning = select_simulation(initial.simulation_id + "-return")
        request = presenter.SessionRequest.from_dict({
            "mode": "simulator", "simulation_id": returning.simulation_id, "contact_type": "second_contact",
        })
        assert request.contact_type == returning.contact_type == "second_contact"
        assert returning.member_ref == initial.member_ref
        assert returning.channel == initial.channel
        assert returning.card().opening != initial.card().opening
        assert returning.card().goal != initial.card().goal
        assert returning.card().behaviours != initial.card().behaviours


def test_b4_b7_second_contact_demo_sends_only_closed_context_and_still_delivers_disclosure():
    async def scenario():
        simulation = select_simulation("samira-return")
        requests = []

        def handle(request):
            requests.append(request)
            if request.url.path == "/journeys":
                return httpx.Response(200, json={
                    "journey_id": "J-returning", "text": DISCLOSURE, "state": "identified",
                })
            return httpx.Response(200, json={"text": "An approved explanation.", "state": "resolved"})

        speech = Speech()
        live = MagicMock(spec=LivePersona)
        live.card = simulation.card()
        live.next_turn = AsyncMock(side_effect=[
            PersonaTurn(simulation.opening), PersonaTurn(FAREWELL, True),
        ])
        async with httpx.AsyncClient(base_url="https://synthetic/", transport=httpx.MockTransport(handle)) as client:
            bridge = VoiceBridge(OrchestratorChannel(client, lambda: "memory-only-synthetic-bearer"),
                                 speech, Device(), spoken_acknowledgement=False)
            result = await run_live_simulator(bridge, Speech(), live, channel=simulation.channel,
                                              accessibility_mode=simulation.accessibility_mode,
                                              contact_type=simulation.contact_type)
        assert json.loads(requests[0].content) == {
            "channel": "chat", "accessibility_mode": "english", "contact_type": "second_contact",
        }
        assert json.loads(requests[1].content) == {"utterance": simulation.opening}
        assert speech.spoken[0] == DISCLOSURE
        assert all(request.headers["authorization"] == "Bearer memory-only-synthetic-bearer" for request in requests)
        assert result.session_ended and len(result.replies) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("member_ref", domain.demo()["trace_members"])
def test_issuer_mints_each_allowlisted_member_with_step_up_and_short_expiry(
        presenter_auth, monkeypatch, member_ref):
    import azure.identity

    monkeypatch.setattr(azure.identity, "ManagedIdentityCredential", MagicMock())
    monkeypatch.setattr(member_issuer_api, "KeyVaultTokenSigner",
                        lambda *a, **k: SimpleNamespace(sign=lambda _: "c2lnbmF0dXJl"))
    with TestClient(member_issuer_api.app) as client:
        result = client.post("/issue", json={"member_ref": member_ref},
                             headers={"Authorization": "Bearer " + presenter_auth(application=True)})
    assert result.status_code == 200
    claims = jwt.decode(result.json()["token"], options={"verify_signature": False})
    assert claims["member_ref"] == member_ref and claims["acr"] == "stepup"
    assert 0 < claims["exp"] - claims["iat"] <= 900
    assert result.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("body", [
    {}, [], {"member_ref": None}, {"member_ref": "MBR-OTHER"},
    {"member_ref": "MBR-9C1M", "age": 45},
    {"member_ref": "MBR-9C1M", "lifetime": 3600},
    {"member_ref": "MBR-9C1M", "step_up": False},
])
def test_b1_b7_issuer_rejects_unmapped_members_and_custom_claims(presenter_auth, monkeypatch, body):
    monkeypatch.setattr(member_issuer_api, "KeyVaultTokenSigner",
                        lambda *a, **k: pytest.fail("rejected selection reached signing key"))
    with TestClient(member_issuer_api.app) as client:
        response = client.post("/issue", json=body,
                               headers={"Authorization": "Bearer " + presenter_auth(application=True)})
    assert response.status_code == 400


def test_member_bearer_sends_mapping_only_to_internal_issuer(monkeypatch):
    monkeypatch.setenv("VOICE_DEMO_CLIENT_ID", "synthetic-app")
    monkeypatch.setenv("VOICE_DEMO_ISSUER_URL", "https://issuer/")
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"token": "memory-only-member-bearer"})

    async def scenario():
        async def get_token(scope):
            assert scope == "api://synthetic-app/.default"
            return SimpleNamespace(token="runtime-application-token")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            token = await presenter.member_bearer(SimpleNamespace(get_token=get_token), client,
                                                  member_ref="MBR-9C1M")
            assert token == "memory-only-member-bearer"

    asyncio.run(scenario())
    assert json.loads(seen[0].content) == {"member_ref": "MBR-9C1M"}
    assert seen[0].headers["authorization"] == "Bearer runtime-application-token"


@pytest.mark.parametrize("simulation_id", ["annelies", "joost", "samira", "kees", "adversarial"])
def test_live_simulation_binds_member_channel_accessibility_and_protective_outcomes(
        host, monkeypatch, simulation_id):
    import azure.identity.aio

    simulation = select_simulation(simulation_id)
    frames, requests, issued, playback_ids = [], [], [], []

    class Authenticator:
        def verify(self, token):
            prefix = "memory-only-"
            if not token.startswith(prefix) or token[len(prefix):] not in domain.demo()["trace_members"]:
                raise jwt.InvalidTokenError("unapproved synthetic token")
            return MemberIdentity(token[len(prefix):], True)

    monkeypatch.setattr(api, "member_authenticator", Authenticator())

    class Credential:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    async def bearer(credential, client, *, member_ref):
        issued.append(member_ref)
        return "memory-only-" + member_ref

    @asynccontextmanager
    async def speech(*args, **kwargs):
        class ActorSpeech(Speech):
            async def transcribe(self, pcm):
                if simulation.channel == "chat":
                    pytest.fail("narrated chat was incorrectly sent through speech recognition")
                return await super().transcribe(pcm)

        yield ActorSpeech([simulation.opening])

    original_client = httpx.AsyncClient

    async def record(request):
        requests.append(request)

    class FakeLive:
        def __init__(self, card):
            self.card, self.turns = card, 0

        async def next_turn(self, guide_text):
            self.turns += 1
            return PersonaTurn(self.card.opening) if self.turns == 1 else PersonaTurn(FAREWELL, True)

    monkeypatch.setattr(presenter, "connect_live_persona", FakeLive)
    monkeypatch.setattr(presenter, "member_bearer", bearer)
    monkeypatch.setattr(azure.identity.aio, "ManagedIdentityCredential", lambda **kwargs: Credential())
    monkeypatch.setattr(presenter, "open_speech", speech)
    monkeypatch.setattr("governedgraph.voice.warmup.warm_runtime", lambda: None)
    monkeypatch.setattr(presenter.httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=host.transport, event_hooks={"request": [record]}, **kwargs))
    for key in ("AZURE_CLIENT_ID", "AZURE_VOICE_LIVE_ENDPOINT", "GOVERNEDGRAPH_ORCHESTRATOR_URL"):
        monkeypatch.setenv(key, "https://synthetic/")

    class Socket:
        first = True

        async def receive_json(self):
            if self.first:
                self.first = False
                return {"mode": "simulator", "simulation_id": simulation_id}
            return {"kind": "played", "id": playback_ids.pop(0)}

        async def send_json(self, frame):
            frames.append(frame)
            if frame["kind"] == "audio":
                playback_ids.append(frame["id"])

        async def send_bytes(self, pcm):
            assert pcm == b"\0\0"

    asyncio.run(presenter.serve(Socket(), {"exp": time.time() + 300}))
    assert issued == [simulation.member_ref]
    assert json.loads(requests[0].content) == {
        "channel": simulation.channel, "accessibility_mode": simulation.accessibility_mode,
    }
    assert requests[-1].url.path == f"/members/{simulation.member_ref}/explainability-report"
    assert all(request.headers["authorization"] == "Bearer memory-only-" + simulation.member_ref
               for request in requests)
    for request in requests[1:-1]:
        assert set(json.loads(request.content)) == {"utterance"}
    audio_frames = [frame for frame in frames if frame["kind"] == "audio"]
    assert audio_frames[0]["role"] == "counsellor" and audio_frames[0]["text"] == DISCLOSURE
    assert audio_frames[1]["role"] == "persona" and audio_frames[1]["text"] == simulation.opening
    assert not any(frame["kind"] == "caption" and frame.get("role") == "member" for frame in frames)
    assert frames[-1]["audit_written"] is True and frames[-1]["numeric_fidelity"] is True
    assert "memory-only-" not in json.dumps(frames)
    if simulation_id in ("kees", "adversarial"):
        assert frames[-1]["state"] == "escalation_requested"
        assert frames[-1]["session_ended"] is False
        assert all(agent.tools.values_client.get.call_count == 0 for agent in host.created)
    else:
        assert audio_frames[-1]["role"] == "persona" and audio_frames[-1]["text"] == FAREWELL
        assert frames[-2] == {"kind": "session_ended"} and frames[-1]["session_ended"] is True
        assert frames[-1]["ended_by"] == "persona"
        assert [json.loads(request.content)["utterance"] for request in requests[1:-1]] == [simulation.opening]
