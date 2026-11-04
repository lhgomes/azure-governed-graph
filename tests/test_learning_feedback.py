"""ILLUSTRATIVE - synthetic data: decisions -> outcomes -> bounded snapshots."""
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from governedgraph.agents.counsellor import new_session
from governedgraph.common.config import settings
from governedgraph.common.contracts import ContractError, ExplainabilityRecord
from governedgraph.explainability.store import LocalExplainabilityStore
from governedgraph.learning.feedback import FeedbackManifest, project_feedback
from governedgraph.learning.feedback_store import FileFeedbackStore, publish_feedback
from governedgraph.learning.ranking_pipeline import load_outcome_aggregates, load_thresholds, max_shift
from governedgraph.learning.run_cycle import run_cycle
from governedgraph.orchestrator.state_machine import Event, Journey
from governedgraph.orchestrator.store import FileJourneyStore, JourneyRecord
from governedgraph.strategy_service.service import StrategyService
from governedgraph.strategy_service.snapshot import default_signer, verify_snapshot


@pytest.fixture
def observed(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.__class__, "run_dir", property(lambda self: tmp_path))
    strategies = StrategyService(state_dir=tmp_path / "strategy", control_share=0)
    run_cycle(strategies)
    agent = new_session("MBR-7Q2K", strategy_service=strategies, journey_id="J-feedback")
    agent.tools.store = LocalExplainabilityStore(tmp_path / "audit.jsonl")
    agent.start()
    agent.handle("Explain my transition letter")
    agent.handle("It stays the same and can go up or down each year.")
    records = [ExplainabilityRecord.from_dict(record) for record in agent.tools.store.read_all()]
    return agent, records, strategies


def test_b4_pending_outcomes_are_observed_but_not_invented_as_completed_windows(observed, tmp_path):
    agent, records, _ = observed
    snapshot = project_feedback([agent.journey], records)
    assert snapshot.manifest.observed_decisions == snapshot.manifest.pending_decisions == 1
    assert snapshot.manifest.observed_resolutions == 1
    assert snapshot.manifest.eligible_decisions == 0
    path = tmp_path / "outcomes.csv"
    path.write_bytes(snapshot.outcomes)
    assert load_outcome_aggregates(path) == {}
    assert all(value not in snapshot.outcomes.decode() for value in ("MBR-", "J-feedback", "€", "letter"))


def test_b4_mature_outcome_joins_audited_strategy_and_deduplicates(observed, tmp_path):
    agent, records, _ = observed
    now = datetime.now(timezone.utc) + timedelta(days=15)
    snapshot = project_feedback([agent.journey], records + records, now=now)
    assert snapshot.manifest.observed_decisions == snapshot.manifest.eligible_decisions == 1
    assert snapshot.manifest.pending_decisions == 0
    path = tmp_path / "outcomes.csv"
    path.write_bytes(snapshot.outcomes)
    evidence = load_outcome_aggregates(path)["first_contact|transition_impact_question|voice|standard"]["S5"]
    assert (evidence.interactions, evidence.resolved, evidence.repeat_contacts) == (1, 1, 0)


def test_b4_repeat_contact_cannot_be_claimed_as_first_contact_resolution(observed, tmp_path):
    agent, records, _ = observed
    now = datetime.now(timezone.utc)
    repeat = Journey("J-repeat", agent.journey.member_ref)
    repeat.fire(Event.DISCLOSED_AND_AUTHENTICATED)
    repeat.events[0] = replace(repeat.events[0], created_at=(now + timedelta(days=2)).isoformat())
    snapshot = project_feedback([agent.journey, repeat], records, now=now + timedelta(days=15))
    path = tmp_path / "outcomes.csv"
    path.write_bytes(snapshot.outcomes)
    evidence = next(iter(load_outcome_aggregates(path).values()))["S5"]
    assert (evidence.interactions, evidence.resolved, evidence.repeat_contacts) == (1, 0, 1)


def test_b4_same_second_repeat_uses_sequence_not_timestamp_precision(observed, tmp_path):
    agent, records, _ = observed
    response_id = next(record.response_id for record in records if record.response_kind == "explanation")
    delivered = next(event for event in agent.journey.events if event.response_id == response_id)
    agent.journey.fire(Event.INTENT_IN_SCOPE)
    agent.journey.events[-1] = replace(agent.journey.events[-1], created_at=delivered.created_at)
    snapshot = project_feedback([agent.journey], records, now=datetime.now(timezone.utc) + timedelta(days=15))
    path = tmp_path / "outcomes.csv"
    path.write_bytes(snapshot.outcomes)
    evidence = next(iter(load_outcome_aggregates(path).values()))["S5"]
    assert (evidence.resolved, evidence.repeat_contacts) == (0, 1)


def test_b4_automatic_resolution_is_not_observed_success(observed, tmp_path):
    strategies = StrategyService(state_dir=tmp_path / "priors", control_share=0)
    empty = tmp_path / "empty.csv"
    empty.write_bytes(project_feedback([], []).outcomes)
    run_cycle(strategies, outcome_path=empty)
    agent = new_session("MBR-7Q2K", strategy_service=strategies, journey_id="J-unconfirmed")
    agent.tools.store = LocalExplainabilityStore(tmp_path / "unconfirmed.jsonl")
    agent.start()
    result = agent.handle("Explain my transition letter")
    assert result.strategy_id == "S1" and result.state == "resolved"
    records = [ExplainabilityRecord.from_dict(record) for record in agent.tools.store.read_all()]
    snapshot = project_feedback([agent.journey], records, now=datetime.now(timezone.utc) + timedelta(days=15))
    path = tmp_path / "outcomes.csv"
    path.write_bytes(snapshot.outcomes)
    assert load_outcome_aggregates(path) == {}
    assert snapshot.manifest.observed_decisions == snapshot.manifest.pending_decisions == 1
    assert snapshot.manifest.eligible_decisions == 0


def test_b5_one_completed_observation_cannot_bypass_minimum_samples(observed, tmp_path):
    agent, records, strategies = observed
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
    records = [ExplainabilityRecord.from_dict(record) for record in agent.tools.store.read_all()]
    snapshot = project_feedback([agent.journey], records, now=datetime.now(timezone.utc) + timedelta(days=15))
    path = tmp_path / "outcomes.csv"
    path.write_bytes(snapshot.outcomes)
    evidence = next(iter(load_outcome_aggregates(path).values()))
    assert evidence["S5"].interactions == 1 and evidence["S5"].resolved == 0
    assert "S1" not in evidence
    assert snapshot.manifest.observed_decisions == 2
    assert snapshot.manifest.eligible_decisions == snapshot.manifest.pending_decisions == 1


def test_b4_missing_or_withheld_audit_response_is_not_learning_evidence(observed):
    agent, records, _ = observed
    explanation = next(record for record in records if record.response_kind == "explanation")
    agent.journey.events = [
        replace(event, response_id=None, response_withheld=True)
        if event.response_id == explanation.response_id else event for event in agent.journey.events
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


def test_b4_feedback_publication_survives_restart_without_duplicate_counts(observed, tmp_path):
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
    output = FileFeedbackStore(tmp_path / "aggregates")
    original = load_outcome_aggregates(output.baseline)
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
    assert cycle.published and verify_snapshot(strategies.current, default_signer())
    for candidate in cycle.candidates:
        assert set(candidate.candidate) == set(previous[candidate.request_key])
        assert max_shift(previous[candidate.request_key], candidate.candidate) <= load_thresholds()["max_rank_shift_per_cycle"]
    next_agent = new_session("MBR-7Q2K", strategy_service=strategies, journey_id="J-next")
    next_agent.start()
    reply = next_agent.handle("Explain my transition letter")
    assert reply.snapshot_id == cycle.snapshot_id
    assert "S2" not in reply.ranked and "S2" in reply.suspended
    assert reply.numeric_fidelity_passed and reply.no_option_recommendation
