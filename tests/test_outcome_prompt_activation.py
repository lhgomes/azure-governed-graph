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
    assert vocabulary["version"] == "1.3"
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
    assert record.journey.events[-1].outcome_version == "1.3"


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
