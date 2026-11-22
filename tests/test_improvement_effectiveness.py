"""Pure closed-aggregate effectiveness tests; no Azure resources or member data."""
import pytest

from governedgraph.common.contracts import ContractError
from governedgraph.learning.improvement_effectiveness import evaluate


def example():
    return {
        "decision_ref": "GB-" + "a" * 32,
        "proposal_id": "GP-" + "b" * 24,
        "situation": {"journey_state": "explaining", "topic": "transition", "channel": "chat",
                      "accessibility_mode": "standard", "language": "en"},
        "published_at": "2026-11-01T00:00:00+00:00",
        "before": {"start": "2026-10-01T00:00:00+00:00", "end": "2026-10-31T00:00:00+00:00",
                   "eligible": 100, "declined": 24, "observed_journeys": 95,
                   "coverage_complete": True, "source_digest": "1" * 64},
        "after": {"start": "2026-11-02T00:00:00+00:00", "end": "2026-11-21T00:00:00+00:00",
                  "eligible": 100, "declined": 11, "observed_journeys": 95,
                  "coverage_complete": True, "source_digest": "2" * 64},
    }


def test_descriptive_change_never_claims_causal_evidence():
    result = evaluate(example())
    assert result["status"] == "observed_descriptive_change"
    assert result["decline_rate_delta_percentage_points"] == -13
    assert result["causal_uplift_proven"] is False and result["governance_action"] == "none"


def test_incomplete_or_small_window_fails_closed():
    data = example()
    data["after"]["coverage_complete"] = False
    assert evaluate(data)["status"] == "insufficient_evidence"
    assert evaluate(data)["after_decline_rate"] is None
    data["after"]["coverage_complete"] = True
    data["after"]["eligible"] = 10
    data["after"]["declined"] = 1
    data["after"]["observed_journeys"] = 9
    assert evaluate(data)["status"] == "insufficient_evidence"


@pytest.mark.parametrize("damage", [
    lambda x: x["after"].update(start="2026-10-30T00:00:00+00:00"),
    lambda x: x["after"].update(source_digest="1" * 64),
    lambda x: x.update(member_ref="private"),
    lambda x: x["after"].update(declined=101),
    lambda x: x["before"].update(eligible=True),
    lambda x: x.update(published_at="2026-11-01"),
])
def test_invalid_closed_evidence_is_rejected(damage):
    data = example()
    damage(data)
    with pytest.raises((ContractError, TypeError, ValueError)):
        evaluate(data)
