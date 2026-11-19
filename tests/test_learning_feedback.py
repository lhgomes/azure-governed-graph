"""ILLUSTRATIVE - synthetic data: decisions -> outcomes -> bounded snapshots."""
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from counsellor_doubles import install, new_session
from governedgraph.common.config import DATA_DIR, settings
from governedgraph.common import domain
from governedgraph.common.contracts import (
    ContractError, ExplainabilityRecord, OutcomeSubmission, ResponseReference,
)
from governedgraph.common.io import load_json
from governedgraph.strategy_service.catalogue import Catalogue
from governedgraph.strategy_service.catalogue_release import CatalogueDecision, prepare_bundle
from governedgraph.explainability.store import LocalExplainabilityStore
from governedgraph.learning.feedback import FeedbackManifest, project_feedback
from governedgraph.learning.feedback_store import FileFeedbackStore, publish_feedback
from governedgraph.learning.ranking_pipeline import load_outcome_aggregates, load_thresholds, max_shift
from strategy_doubles import OUTCOMES, SIGNER, pinned_to_active, run_cycle
from governedgraph.orchestrator.state_machine import Event, Journey
from governedgraph.orchestrator.store import FileJourneyStore, JourneyRecord
from strategy_doubles import local_strategy_service
from governedgraph.strategy_service.snapshot import verify_snapshot


def explanation_id(agent):
    return next(record["response_id"] for record in agent.tools.store.read_all()
                if record["response_kind"] == "explanation")


def confirm_playback(agent, response_id):
    """The voice bridge confirms playback of a delivered answer before the member's next turn."""
    agent.journey.record_playback(response_id, "playback-" + response_id)


def offer_outcome(agent, response_id):
    """The orchestrator offers the explicit outcome check once the answer is settled."""
    agent.journey.offer_outcome(response_id, domain.vocabulary()["version"])


def report_outcome(agent, response_id, choice, *, received_at=None):
    agent.journey.record_outcome(OutcomeSubmission(response_id, choice), "outcome-" + response_id,
                                 received_at=received_at)


@pytest.fixture
def observed(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.__class__, "run_dir", property(lambda self: tmp_path))
    install(monkeypatch, tmp_path)
    strategies = local_strategy_service(tmp_path / "strategy", control_share=0)
    run_cycle(strategies)
    agent = new_session("MBR-7Q2K", strategy_service=strategies, journey_id="J-feedback")
    agent.tools.store = LocalExplainabilityStore(tmp_path / "audit.jsonl")
    agent.start()
    agent.handle("Explain my transition letter")
    confirm_playback(agent, explanation_id(agent))
    agent.handle("It stays the same and can go up or down each year.")
    offer_outcome(agent, explanation_id(agent))
    records = [ExplainabilityRecord.from_dict(record) for record in agent.tools.store.read_all()]
    return agent, records, strategies


def test_b4_pending_outcomes_are_observed_but_not_invented_as_completed_windows(observed, tmp_path):
    agent, records, _ = observed
    snapshot = project_feedback([agent.journey], records)
    assert snapshot.manifest.observed_decisions == snapshot.manifest.pending_decisions == 1
    # A passed teach-back is not an observed resolution; only the member's explicit "yes" is.
    assert snapshot.manifest.observed_resolutions == 0
    assert snapshot.manifest.eligible_decisions == 0
    path = tmp_path / "outcomes.csv"
    path.write_bytes(snapshot.outcomes)
    assert load_outcome_aggregates(path) == {}
    assert all(value not in snapshot.outcomes.decode() for value in ("MBR-", "J-feedback", "€", "letter"))


def test_b4_mature_outcome_joins_audited_strategy_and_deduplicates(observed, tmp_path):
    agent, records, _ = observed
    report_outcome(agent, explanation_id(agent), "yes")
    now = datetime.now(timezone.utc) + timedelta(days=15)
    snapshot = project_feedback([agent.journey], records + records, now=now)
    assert snapshot.manifest.observed_decisions == snapshot.manifest.eligible_decisions == 1
    assert snapshot.manifest.pending_decisions == 0
    assert snapshot.manifest.reported_yes == snapshot.manifest.observed_resolutions == 1
    path = tmp_path / "outcomes.csv"
    path.write_bytes(snapshot.outcomes)
    evidence = load_outcome_aggregates(path)["first_contact|transition_impact_question|voice|standard"]["S5"]
    assert (evidence.interactions, evidence.resolved, evidence.repeat_contacts) == (1, 1, 0)


def test_b4_repeat_contact_cannot_be_claimed_as_first_contact_resolution(observed, tmp_path):
    agent, records, _ = observed
    response_id = explanation_id(agent)
    report_outcome(agent, response_id, "yes")
    now = datetime.now(timezone.utc)
    repeat = Journey("J-repeat", agent.journey.member_ref)
    repeat.fire(Event.DISCLOSED_AND_AUTHENTICATED)
    repeat.record_linked_contact(ResponseReference(agent.journey.journey_id, response_id), "link-1",
                                 received_at=(now + timedelta(days=2)).isoformat(timespec="microseconds"))
    snapshot = project_feedback([agent.journey, repeat], records, now=now + timedelta(days=15))
    path = tmp_path / "outcomes.csv"
    path.write_bytes(snapshot.outcomes)
    evidence = next(iter(load_outcome_aggregates(path).values()))["S5"]
    assert (evidence.interactions, evidence.resolved, evidence.repeat_contacts) == (1, 0, 1)


def test_b4_same_second_repeat_uses_sequence_not_timestamp_precision(observed, tmp_path):
    agent, records, _ = observed
    response_id = explanation_id(agent)
    report_outcome(agent, response_id, "yes")
    playback = next(event for event in agent.journey.events if event.playback_response_id == response_id)
    agent.journey.fire(Event.INTENT_IN_SCOPE)
    agent.journey.events[-1] = replace(
        agent.journey.events[-1], created_at=playback.created_at,
        related_journey_id=agent.journey.journey_id, related_response_id=response_id)
    snapshot = project_feedback([agent.journey], records, now=datetime.now(timezone.utc) + timedelta(days=15))
    path = tmp_path / "outcomes.csv"
    path.write_bytes(snapshot.outcomes)
    evidence = next(iter(load_outcome_aggregates(path).values()))["S5"]
    assert (evidence.resolved, evidence.repeat_contacts) == (0, 1)


def test_b4_automatic_resolution_is_not_observed_success(observed, tmp_path):
    strategies = local_strategy_service(tmp_path / "priors", control_share=0)
    empty = tmp_path / "empty.csv"
    empty.write_bytes(project_feedback([], []).outcomes)
    run_cycle(strategies, outcome_path=empty)
    agent = new_session("MBR-7Q2K", strategy_service=strategies, journey_id="J-unconfirmed")
    agent.tools.store = LocalExplainabilityStore(tmp_path / "unconfirmed.jsonl")
    agent.start()
    result = agent.handle("Explain my transition letter")
    assert result.strategy_id == "S1" and result.state == "resolved"
    confirm_playback(agent, explanation_id(agent))
    offer_outcome(agent, explanation_id(agent))
    records = [ExplainabilityRecord.from_dict(record) for record in agent.tools.store.read_all()]
    snapshot = project_feedback([agent.journey], records, now=datetime.now(timezone.utc) + timedelta(days=15))
    path = tmp_path / "outcomes.csv"
    path.write_bytes(snapshot.outcomes)
    assert load_outcome_aggregates(path) == {}
    assert snapshot.manifest.observed_decisions == snapshot.manifest.pending_decisions == 1
    assert snapshot.manifest.eligible_decisions == snapshot.manifest.observed_resolutions == 0


def test_b5_one_completed_observation_cannot_bypass_minimum_samples(observed, tmp_path):
    agent, records, strategies = observed
    report_outcome(agent, explanation_id(agent), "yes")
    snapshot = project_feedback([agent.journey], records, now=datetime.now(timezone.utc) + timedelta(days=15))
    path = tmp_path / "outcomes.csv"
    path.write_bytes(snapshot.outcomes)
    cycle = run_cycle(strategies, outcome_path=path)
    assert all(candidate.candidate == candidate.previous for candidate in cycle.candidates)


def test_b4_observed_failed_teachback_is_not_censored_as_unknown(observed, tmp_path):
    _, _, strategies = observed
    agent = new_session("MBR-7Q2K", strategy_service=strategies, journey_id="J-failed")
    agent.tools.store = LocalExplainabilityStore(tmp_path / "failed.jsonl")
    agent.start()
    agent.handle("Explain my transition letter")
    agent.handle("I still do not understand")
    # Only the settled second answer is offered the outcome check; the member explicitly says it did not help.
    second = agent.tools.store.read_all()[-1]["response_id"]
    confirm_playback(agent, second)
    offer_outcome(agent, second)
    report_outcome(agent, second, "no")
    records = [ExplainabilityRecord.from_dict(record) for record in agent.tools.store.read_all()]
    snapshot = project_feedback([agent.journey], records, now=datetime.now(timezone.utc) + timedelta(days=15))
    path = tmp_path / "outcomes.csv"
    path.write_bytes(snapshot.outcomes)
    evidence = next(iter(load_outcome_aggregates(path).values()))
    assert evidence["S1"].interactions == 1 and evidence["S1"].resolved == 0
    assert "S5" not in evidence
    assert snapshot.manifest.observed_decisions == snapshot.manifest.eligible_decisions == 1
    assert snapshot.manifest.pending_decisions == 0 and snapshot.manifest.reported_no == 1


def test_b4_missing_or_withheld_audit_response_is_not_learning_evidence(observed):
    agent, records, _ = observed
    explanation = next(record for record in records if record.response_kind == "explanation")
    agent.journey.events = [
        replace(event, response_id=None, response_withheld=True, outcome_offered=False, outcome_version=None)
        if event.response_id == explanation.response_id else event
        for event in agent.journey.events if event.playback_response_id != explanation.response_id
    ]
    snapshot = project_feedback([agent.journey], records, now=datetime.now(timezone.utc) + timedelta(days=15))
    assert snapshot.manifest.observed_decisions == 0


def test_b4_conflicting_audit_identity_fails_closed(observed):
    agent, records, _ = observed
    explanation = next(record for record in records if record.response_kind == "explanation")
    with pytest.raises(ValueError, match="conflicting"):
        project_feedback([agent.journey], records + [replace(explanation, snapshot_id="changed")])


def test_b7_feedback_manifest_rejects_demographics_and_transcripts(observed):
    agent, records, _ = observed
    payload = asdict(project_feedback([agent.journey], records).manifest)
    for field in ("gender", "age_band", "transcript"):
        with pytest.raises(ContractError):
            FeedbackManifest.from_dict({**payload, field: "not permitted"})
    with pytest.raises(ValueError, match="manifest"):
        FeedbackManifest.from_dict({**payload, "observed_decisions": True})


def test_b5_historical_member_outcomes_cannot_count_for_an_edited_style(observed):
    agent, records, _ = observed
    explained = next(record for record in records if record.response_kind == "explanation")
    assert project_feedback([agent.journey], records).manifest.observed_decisions > 0
    decision = CatalogueDecision.from_dict({
        "expected_revision": "a" * 64, "decision_ref": "GB-" + "b" * 32,
        "reason": "ILLUSTRATIVE - owner approves changed wording pattern", "confirmed": True,
        "change": {"action": "edit", "strategy_id": explained.strategy_id, "name": "Illustrative replacement",
                   "pattern_id": "S3", "eligible_journey_states": ["first_contact"],
                   "channels": ["voice"], "accessibility_modes": ["standard"]},
    })
    bundle = prepare_bundle(Catalogue.load(), load_json(DATA_DIR / "catalogue" / "expert_priors_v1.json"), decision)
    result = project_feedback([agent.journey], records, catalogue=Catalogue(bundle.catalogue))
    assert result.manifest.observed_decisions == 0


def test_b4_feedback_publication_survives_restart_without_duplicate_counts(observed, tmp_path, monkeypatch):
    from governedgraph.agents.clients import HttpStrategyClient

    monkeypatch.setattr(HttpStrategyClient, "get_catalogue", lambda *_: (Catalogue.load(), {}))
    agent, _, _ = observed
    journeys = FileJourneyStore(tmp_path / "journeys")
    with journeys.transaction(agent.journey.journey_id) as transaction:
        transaction.save(JourneyRecord(agent.journey))
    output = FileFeedbackStore(tmp_path / "aggregates")
    first = publish_feedback(journeys, agent.tools.store, output)
    restarted = FileFeedbackStore(output.directory)
    second = publish_feedback(FileJourneyStore(journeys.directory), agent.tools.store, restarted)
    assert first == second == restarted.current()
    assert len(list((output.directory / "live").glob("*.csv"))) == 1
    assert list(journeys.ids()) == [agent.journey.journey_id]
    assert "response_id" not in json.dumps(asdict(first))


def test_b3_b5_b6_observed_outcome_reaches_signed_snapshot_and_next_journey(observed, tmp_path):
    agent, records, strategies = observed
    baseline = pinned_to_active(strategies, OUTCOMES, tmp_path / "baseline" / "outcomes.csv")
    output = FileFeedbackStore(tmp_path / "aggregates", baseline)
    original = load_outcome_aggregates(output.baseline)
    report_outcome(agent, explanation_id(agent), "yes")
    snapshot = project_feedback([agent.journey], records, baseline=output.baseline,
                                now=datetime.now(timezone.utc) + timedelta(days=15))
    output.save(snapshot)
    key = "first_contact|transition_impact_question|voice|standard"
    evidence = load_outcome_aggregates(output.outcome_path())[key]["S5"]
    assert evidence.interactions == original[key]["S5"].interactions + 1
    assert evidence.resolved == original[key]["S5"].resolved + 1
    previous = {key: [sid for sid, _ in ranking] for key, ranking in strategies.current.rankings.items()}
    cycle = run_cycle(strategies, outcome_path=output.outcome_path())
    assert cycle.workflow_steps == ["aggregate", "bounded-rank", "fairness-veto", "sign-govern-publish"]
    assert cycle.published and verify_snapshot(strategies.current, SIGNER)
    for candidate in cycle.candidates:
        assert set(candidate.candidate) == set(previous[candidate.request_key])
        assert max_shift(previous[candidate.request_key], candidate.candidate) <= load_thresholds()["max_rank_shift_per_cycle"]
    next_agent = new_session("MBR-7Q2K", strategy_service=strategies, journey_id="J-next")
    next_agent.start()
    reply = next_agent.handle("Explain my transition letter")
    assert reply.snapshot_id == cycle.snapshot_id
    assert "S2" not in reply.ranked and "S2" in reply.suspended
    assert reply.numeric_fidelity_passed and reply.no_option_recommendation
