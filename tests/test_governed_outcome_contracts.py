"""Feedback and playback evidence contracts; run only in the Azure test job."""
from datetime import datetime, timedelta, timezone

import pytest

from governedgraph.common.contracts import ContractError, OutcomeSubmission
from governedgraph.learning.feedback import FeedbackManifest, project_feedback
from governedgraph.orchestrator.state_machine import Event, Journey


def new_answer(channel="voice"):
    journey = Journey("J-evidence", "MBR-synthetic", channel=channel)
    journey.fire(Event.DISCLOSED_AND_AUTHENTICATED)
    journey.fire(Event.INTENT_IN_SCOPE)
    count = len(journey.events)
    journey.fire(Event.NO_TEACH_BACK)
    response_id = "a" * 32
    journey.record_response(response_id, count)
    return journey, response_id


def test_response_outcomes_are_explicit_and_keyed_to_a_delivered_answer():
    journey, response_id = new_answer()
    assert not journey.events[-1].outcome_offered
    journey.offer_outcome(response_id, "1.2")
    assert journey.events[-1].outcome_offered
    journey.record_playback(response_id, "playback-1")
    journey.record_outcome(OutcomeSubmission(response_id, "skip"), "outcome-1")
    assert journey.events[-1].outcome_choice == "skip"
    assert journey.events[-1].outcome_response_id == response_id
    with pytest.raises(ContractError):
        journey.record_outcome(OutcomeSubmission(response_id, "yes"), "outcome-1")


def test_without_a_checked_delivered_audit_there_is_no_implicit_success():
    journey, _ = new_answer("chat")
    # A state transition to RESOLVED is not evidence that the question was answered.
    snapshot = project_feedback([journey], [], now=datetime.now(timezone.utc) + timedelta(days=15))
    assert snapshot.manifest.observed_decisions == 0
    assert snapshot.manifest.eligible_decisions == 0
    assert snapshot.manifest.observed_resolutions == 0


def test_manifest_keeps_unknown_feedback_distinct_from_success():
    digest = "a" * 64
    manifest = FeedbackManifest(
        digest, digest, digest, 1, 0, 1, 0,
        reported_skip=1, unknown_after_window=1,
    )
    assert manifest.to_dict()["reported_skip"] == 1
    assert manifest.to_dict()["unknown_after_window"] == 1
    with pytest.raises(ValueError):
        FeedbackManifest(digest, digest, digest, 1, 0, 1, 0, reported_yes=2)
