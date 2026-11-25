"""Closed synthetic outcome prompt validation; execute on Azure-hosted CI only."""
import json
from types import SimpleNamespace

import pytest

from governedgraph.common import domain
from governedgraph.common.config import DATA_DIR
from governedgraph.common.contracts import ContractError, OutcomeOffer
from governedgraph.orchestrator.service import OrchestratorService
from governedgraph.orchestrator.state_machine import Event, Journey, State
from governedgraph.orchestrator.store import JourneyRecord


def _synthetic_outcome_context():
    journey = Journey("J-outcome-prompt", "MBR-synthetic", channel="chat")
    journey.fire(Event.DISCLOSED_AND_AUTHENTICATED)
    journey.fire(Event.INTENT_IN_SCOPE)
    previous = len(journey.events)
    journey.fire(Event.NO_TEACH_BACK)
    journey.record_response("a" * 32, previous)
    audit = SimpleNamespace(response_id="a" * 32, response_kind="explanation")
    trace = SimpleNamespace(audit_record=audit, values=None, content_texts=[])
    counsellor = SimpleNamespace(tools=SimpleNamespace(trace=trace), journey=journey)
    result = SimpleNamespace(state=State.RESOLVED.value, escalated=False, audit_status="committed",
                             text="This is a checked, grounded explanation.", fidelity=None)
    return counsellor, result, JourneyRecord(journey)


def test_host_outcome_wording_is_closed_and_localized():
    vocabulary = domain.vocabulary()
    assert vocabulary["version"] == "1.6"
    assert set(vocabulary["outcome_check"]) == set(vocabulary["response_languages"]) == {"en", "nl"}
    assert vocabulary["outcome_check_approval_ref"] is None
    for language, wording in vocabulary["outcome_check"].items():
        offer = OutcomeOffer("a" * 32, language, vocabulary["version"], **wording)
        assert offer.question == wording["question"]
        assert offer.yes != offer.no
        with pytest.raises(ContractError):
            OutcomeOffer("a" * 32, language, vocabulary["version"], **{**wording, "no": wording["yes"]})
        with pytest.raises(ContractError):
            OutcomeOffer("a" * 32, language, "1.1", **wording)


def test_synthetic_dev_activation_is_required(monkeypatch):
    counsellor, result, record = _synthetic_outcome_context()
    engine = object.__new__(OrchestratorService)
    monkeypatch.setenv("AZURE_ENV_NAME", "gg-prod")
    monkeypatch.setenv("GOVERNEDGRAPH_ENABLE_OUTCOME_CHECK", "true")
    assert engine._offer_outcome(counsellor, result, record) is None
    assert not record.journey.events[-1].outcome_offered
    monkeypatch.setenv("AZURE_ENV_NAME", "gg-dev")
    monkeypatch.delenv("GOVERNEDGRAPH_ENABLE_OUTCOME_CHECK")
    assert engine._offer_outcome(counsellor, result, record) is None
    monkeypatch.setenv("GOVERNEDGRAPH_ENABLE_OUTCOME_CHECK", "true")
    offered = engine._offer_outcome(counsellor, result, record)
    assert offered is not None
    assert offered.response_id == "a" * 32
    assert result.text.endswith(offered.question)
    assert record.journey.events[-1].outcome_offered
    assert record.journey.events[-1].outcome_version == "1.6"


@pytest.mark.parametrize("text", [
    "Please share the sentence you'd like explained, leaving out personal details. Which part would you like help with?",
    "Your accrued entitlements are converted into the new contract. Would you like me to explain that term?",
])
def test_b5_feedback_does_not_compete_with_an_existing_question(monkeypatch, text):
    counsellor, result, record = _synthetic_outcome_context()
    result.text = text
    engine = object.__new__(OrchestratorService)
    monkeypatch.setenv("AZURE_ENV_NAME", "gg-dev")
    monkeypatch.setenv("GOVERNEDGRAPH_ENABLE_OUTCOME_CHECK", "true")

    assert engine._offer_outcome(counsellor, result, record) is None
    assert result.text == text
    assert not record.journey.events[-1].outcome_offered


@pytest.mark.parametrize("bad_wording", [
    {"en": {"question": "Did this answer 123?", "yes": "Yes", "no": "No", "skip": "Skip"}},
    {"en": {"question": "Did this answer your question?", "yes": "Yes", "no": "Yes", "skip": "Skip"}},
    {"en": {"question": "Did this answer your question?", "yes": "Yes", "no": "No", "skip": ""}},
])
def test_malformed_feedback_vocabularies_fail_closed(tmp_path, bad_wording):
    vocabulary = json.loads((DATA_DIR / "catalogue" / "vocabulary_v1.json").read_text())
    vocabulary["outcome_check"].update(bad_wording)
    path = tmp_path / "catalogue"
    path.mkdir()
    (path / "vocabulary_v1.json").write_text(json.dumps(vocabulary))
    with pytest.raises(domain.DomainPackError):
        domain.vocabulary(tmp_path)


def _confirmed_teach_back_context(channel="chat"):
    journey = Journey("J-outcome-bridge", "MBR-synthetic", channel=channel)
    journey.fire(Event.DISCLOSED_AND_AUTHENTICATED)
    journey.fire(Event.INTENT_IN_SCOPE)
    previous = len(journey.events)
    journey.fire(Event.RESPONSE_DELIVERED)
    journey.record_response("b" * 32, previous)
    journey.fire(Event.TEACH_BACK_PASSED)
    journey.record_response("c" * 32, len(journey.events) - 1)
    explanation = SimpleNamespace(response_id="b" * 32, response_kind="explanation")
    confirmation = SimpleNamespace(response_id="c" * 32, response_kind="acknowledgment")
    trace = SimpleNamespace(audit_record=confirmation, values=None, content_texts=[])
    counsellor = SimpleNamespace(tools=SimpleNamespace(trace=trace, auth_ref="x"), journey=journey, step_up_verified=True)
    result = SimpleNamespace(state=State.RESOLVED.value, escalated=False, audit_status="committed",
                             text="Thank you. If anything else comes to mind, you can ask a person at any time.", fidelity=None)
    return counsellor, result, JourneyRecord(journey), explanation


def test_b5_the_check_after_a_confirmed_teach_back_names_the_explanation_it_is_about(monkeypatch):
    counsellor, result, record, explanation = _confirmed_teach_back_context()
    engine = object.__new__(OrchestratorService)
    engine._explanation = lambda *_: explanation
    monkeypatch.setenv("AZURE_ENV_NAME", "gg-dev")
    monkeypatch.setenv("GOVERNEDGRAPH_ENABLE_OUTCOME_CHECK", "true")
    offer = engine._offer_outcome(counsellor, result, record)
    assert offer.response_id == "b" * 32  # the check belongs to the explanation, not the thank-you
    assert result.text.endswith(f"A quick question about my earlier explanation. {offer.question}")
    assert result.text.count("A quick question") == 1
    from dataclasses import asdict

    from governedgraph.voice.voice_live_bridge import VoiceReply

    VoiceReply(text=result.text, state=result.state, outcome_offer=asdict(offer), response_id="b" * 32,
               journey_id="J-outcome-bridge", strategy_id="S5", audit_status="committed")  # the spoken-reply contract needs the text to end with the approved question


def test_b5_a_check_on_an_ordinary_answer_gets_no_bridge(monkeypatch):
    counsellor, result, record = _synthetic_outcome_context()
    engine = object.__new__(OrchestratorService)
    monkeypatch.setenv("AZURE_ENV_NAME", "gg-dev")
    monkeypatch.setenv("GOVERNEDGRAPH_ENABLE_OUTCOME_CHECK", "true")
    offer = engine._offer_outcome(counsellor, result, record)
    assert result.text == f"This is a checked, grounded explanation.\n\n{offer.question}"
