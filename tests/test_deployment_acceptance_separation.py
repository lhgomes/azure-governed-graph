"""Hosted evaluation targets the latest deployed writers and retained evidence, never deployment or a real-channel proof."""
import inspect

from scripts import delivery, run_foundry_evals


def test_evaluation_contract_is_separate_and_never_provisions_or_issues_tokens():
    source = inspect.getsource(run_foundry_evals)
    assert "create_version" not in source and "scripts.deploy" not in source
    assert "containerapp" not in source and "issue_member_token" not in source
    assert "local deterministic adapters" in source
    assert 'report["evaluation_passed"]' in source
    assert 'report["evaluation_status"] = "passed" if report["evaluation_passed"] else "failed"' in source


def test_evaluation_targets_the_deployed_writers_and_fairness_stays_separate():
    assert set(run_foundry_evals.WRITER_NAMES) == {delivery.AGENT_NAMES[role] for role in delivery.AGENT_ROLES} - {
        delivery.AGENT_NAMES["fairness"]}


def test_teardown_has_only_a_historical_scope_reader_and_no_delivery_state_machine():
    from scripts import teardown

    source = inspect.getsource(teardown)
    assert "from scripts.deploy" not in source and "from scripts.delivery" not in source
    assert "State.load" not in source and "historical_answers" in source
