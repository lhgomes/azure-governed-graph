"""Teardown remains separate from delivery and does not impersonate a deployment acceptance test."""
import inspect

def test_teardown_has_only_a_historical_scope_reader_and_no_delivery_state_machine():
    from scripts import teardown

    source = inspect.getsource(teardown)
    assert "from scripts.deploy" not in source and "from scripts.delivery" not in source
    assert "State.load" not in source and "historical_answers" in source
