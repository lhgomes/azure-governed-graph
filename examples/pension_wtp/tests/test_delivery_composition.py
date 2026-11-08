"""Closed pension extension composition; domain packs cannot smuggle delivery/proof policy."""
import json

import pytest

from scripts.delivery import Composition, DeliveryError, ROOT


@pytest.mark.parametrize("field", ["local_checks", "deployed_checks", "learning", "extra"])
def test_b7_b8_sample_definition_is_closed_and_cannot_escape_its_verification_contract(monkeypatch, field):
    path = ROOT / "examples/pension_wtp/deployment.json"
    original = path.read_text()
    value = json.loads(original)
    value[field] = []
    monkeypatch.setattr(type(path), "read_text", lambda _self: json.dumps(value))
    with pytest.raises(DeliveryError):
        Composition.load(path)


def test_pension_cases_and_runner_live_in_the_sample_not_the_framework():
    assert (ROOT / "examples/pension_wtp/evaluation/run_evals.py").exists()
    assert (ROOT / "examples/pension_wtp/tests/test_acceptance_cases.py").exists()
    assert not (ROOT / "src/governedgraph/evaluation_cases").exists()
