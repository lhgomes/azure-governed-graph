"""Hosted evaluation has exact pins and retained evidence, never deployment or a real-channel proof."""
import inspect

import pytest

from scripts import delivery, run_foundry_evals
from scripts.azd_env import EnvError


def test_evaluation_contract_is_separate_and_never_provisions_or_issues_tokens():
    source = inspect.getsource(run_foundry_evals)
    assert "create_version" not in source and "scripts.deploy" not in source
    assert "containerapp" not in source and "issue_member_token" not in source
    assert "local deterministic adapters" in source
    assert 'report["evaluation_passed"]' in source
    assert 'report["evaluation_status"] = "passed" if report["evaluation_passed"] else "failed"' in source


@pytest.mark.parametrize("key", list(run_foundry_evals.PIN_KEYS.values()))
@pytest.mark.parametrize("version", ["", "latest", "-1"])
def test_evaluation_refuses_missing_or_nonimmutable_pins(key, version):
    values = {"AZURE_ENV_NAME": "gg-dev", "AZURE_AI_PROJECT_ENDPOINT": "https://synthetic.services.ai.azure.com/api/projects/test",
              **{pin: "1" for pin in run_foundry_evals.PIN_KEYS.values()}, key: version}
    with pytest.raises(EnvError):
        run_foundry_evals.validate(values)


def test_evaluation_pins_match_deployed_writer_and_semantic_roles():
    assert set(run_foundry_evals.PIN_KEYS.values()) == set(delivery.AGENT_ROLES.values()) - {"AZURE_AI_FAIRNESS_AGENT_VERSION"}


def test_teardown_has_only_a_historical_scope_reader_and_no_delivery_state_machine():
    from scripts import teardown

    source = inspect.getsource(teardown)
    assert "from scripts.deploy" not in source and "from scripts.delivery" not in source
    assert "State.load" not in source and "historical_answers" in source
