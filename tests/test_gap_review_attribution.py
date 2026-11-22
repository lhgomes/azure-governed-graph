"""Synthetic diagnostics must attribute linked contacts across journey boundaries."""
from datetime import timedelta
from dataclasses import asdict
import json

from admin_doubles import MemoryContainer
from governedgraph.common.contracts import OutcomeSubmission, ResponseReference
from governedgraph.learning.gap_review_job import supplemental_feedback_counts
from governedgraph.orchestrator.state_machine import Journey
from governedgraph.orchestrator.store import JourneyRecord
from test_gap_review import NOW, record


def store(*journeys):
    return MemoryContainer({f"{journey.journey_id}.json": JourneyRecord(journey).encode().encode()
                            for journey in journeys})


def test_cross_journey_followups_use_source_journey_and_member_not_just_response_id():
    source = record(1, kind="explanation", strategy="S1", issues=[])
    first = Journey("J-1", "M-1")
    first.record_outcome(OutcomeSubmission(source.response_id, "no"), "outcome-one", received_at=NOW.isoformat())
    later = Journey("J-later", "M-1")
    later.record_linked_contact(ResponseReference("J-1", source.response_id),
                                "contact-one", received_at=NOW.isoformat())
    repeated = Journey("J-later2", "M-1")
    repeated.record_linked_contact(ResponseReference("J-1", source.response_id),
                                   "contact-two", received_at=NOW.isoformat())
    other_member = Journey("J-other", "M-2")
    other_member.record_linked_contact(ResponseReference("J-1", source.response_id),
                                       "wrong-member", received_at=NOW.isoformat())
    result = supplemental_feedback_counts([source], store(first, later, repeated, other_member),
                                           since=NOW-timedelta(days=30), now=NOW)
    assert result["reported_no"] == 1
    assert result["linked_followups_observed"] == 1  # distinct original answer, not contacts
    assert result["ambiguous_link_events"] == 1
    assert result["attribution_complete"] is True
    assert result["eligible_response_references"] == 1


def test_missing_origin_and_older_contact_do_not_become_complete_evidence():
    source = record(1, kind="explanation", strategy="S1", issues=[])
    later = Journey("J-later", "M-1")
    later.record_linked_contact(ResponseReference("J-1", source.response_id),
                                "old-contact", received_at=(NOW-timedelta(days=35)).isoformat())
    result = supplemental_feedback_counts([source], store(later),
                                           since=NOW-timedelta(days=30), now=NOW)
    assert result["attribution_complete"] is False
    assert result["unverified_origin_journeys"] == 1
    assert result["linked_followups_observed"] == 0
    assert result["reported_no"] == 0


def test_test_journey_is_not_a_feedback_source():
    source = record(1, kind="explanation", strategy="S1", issues=[])
    first = Journey("J-1", "M-1", test_strategy_id="S1")
    later = Journey("J-later", "M-1")
    later.record_linked_contact(ResponseReference("J-1", source.response_id),
                                "contact-one", received_at=NOW.isoformat())
    result = supplemental_feedback_counts([source], store(first, later),
                                           since=NOW-timedelta(days=30), now=NOW)
    assert result["attribution_complete"] is False
    assert result["linked_followups_observed"] == 0  # test-origin references must not count
