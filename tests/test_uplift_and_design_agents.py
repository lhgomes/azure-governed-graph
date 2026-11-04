"""B10 kill switch logic and design-depth agent boundaries."""
import pytest

from pension_wtp.agents.adoption_guide import AdoptionGuide
from pension_wtp.agents.event_processing import EventProcessingAgent
from pension_wtp.agents.transition_calculation import TransitionCalculationAgent
from governedgraph.learning.uplift import ArmOutcome, evaluate_uplift


def test_kill_switch_when_window_closes_without_uplift():
    r = evaluate_uplift(ArmOutcome(10000, 7000, 900), ArmOutcome(500, 350, 45), window_closed=True)
    assert r.decision == "kill_switch"


def test_continue_when_significant_uplift():
    r = evaluate_uplift(ArmOutcome(19000, 14250, 1500), ArmOutcome(1000, 680, 120), window_closed=False)
    assert r.decision == "continue" and r.fcr_uplift_pts > 0


def test_adoption_guide_never_recommends_options():
    g = AdoptionGuide()
    assert g.next_step("first_contact", [], advice_seeking=True).step_id == "NS-05"
    assert all(s.startswith("NS-") for s in [g.next_step(st, [], False).step_id for st in ("first_contact", "values_reviewed", "other")])


def test_event_agent_routes_divorce_and_death_to_humans():
    a = EventProcessingAgent()
    assert a.process("E1", "divorce", True, "k1").route == "exception_queue"
    assert a.process("E2", "death", True, "k2").route == "exception_queue"
    assert a.process("E3", "retirement", True, "k3").route == "submitted"


def test_calculation_agent_cannot_release_without_actuary():
    a = TransitionCalculationAgent(engine=None)
    b = a.schedule_conversion_batch("RP1", "RS-1", ["M1", "M2"], capacity=10)
    with pytest.raises(PermissionError):
        a.release(b, None)
