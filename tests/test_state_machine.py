"""B9: escalation reachable from every state; idempotent transitions; teach-back loop."""
import pytest

from governedgraph.orchestrator.state_machine import Event, InvalidTransition, Journey, State


def test_escalation_reachable_from_every_non_closed_state():
    for st in State:
        if st == State.CLOSED:
            continue
        j = Journey("J", "M", state=st)
        assert j.fire(Event.ESCALATE) == State.ESCALATION_REQUESTED


def test_teach_back_fail_then_alternative_then_escalate():
    j = Journey("J", "M", state=State.COMPREHENSION_CHECK)
    assert j.fire(Event.TEACH_BACK_FAILED) == State.EXPLAINING
    j.fire(Event.RESPONSE_DELIVERED)
    assert j.fire(Event.TEACH_BACK_FAILED) == State.ESCALATION_REQUESTED


def test_idempotent_retry():
    j = Journey("J", "M")
    j.fire(Event.DISCLOSED_AND_AUTHENTICATED, event_id="e1")
    j.fire(Event.DISCLOSED_AND_AUTHENTICATED, event_id="e1")
    assert j.state == State.IDENTIFIED and len(j.events) == 1


def test_invalid_transition_rejected():
    with pytest.raises(InvalidTransition):
        Journey("J", "M").fire(Event.TEACH_BACK_PASSED)


def test_request_key_has_no_member_attributes():
    assert set(Journey("J", "M").request_key()) == {"journey_state", "intent", "channel", "accessibility_mode"}
