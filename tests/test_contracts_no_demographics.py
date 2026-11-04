"""B7: runtime demographic routing is prohibited - enforced by closed schemas."""
import pytest

from governedgraph.common.contracts import ContractError, ExplainabilityRecord, StrategyRequest

BASE = {"journey_id": "J-1", "journey_state": "first_contact", "intent": "transition_impact_question", "channel": "voice"}


@pytest.mark.parametrize("field", ["age", "gender", "salary_band", "date_of_birth", "cohort"])
def test_strategy_request_rejects_demographic_fields(field):
    with pytest.raises(ContractError, match="B7"):
        StrategyRequest.from_dict({**BASE, field: "x"})


def test_strategy_request_rejects_any_unknown_field():
    with pytest.raises(ContractError):
        StrategyRequest.from_dict({**BASE, "persona": "annelies"})


def test_accessibility_mode_must_be_member_selectable_value():
    with pytest.raises(ContractError):
        StrategyRequest.from_dict({**BASE, "accessibility_mode": "elderly"}).key


def test_explainability_record_request_key_is_closed():
    with pytest.raises(ContractError):
        ExplainabilityRecord(
            journey_id="J", member_ref="M", strategy_id="S1", strategy_version="1.0", snapshot_id="s", arm="treatment",
            content_versions=[], value_ids=[], value_sources=[], model_deployment="m", template_version="1",
            agent_name="local-template-composer", agent_version="1",
            disclosure_given=True, escalation_offered=True, numeric_fidelity_passed=True,
            request_key={"journey_state": "a", "intent": "b", "channel": "voice", "accessibility_mode": "standard", "age": "78"},
        )
