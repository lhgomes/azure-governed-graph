"""ILLUSTRATIVE - synthetic data. Coverage requirements owned by the pension sample."""
import json
from pathlib import Path


SAMPLE = Path(__file__).resolve().parents[1]


def cases(filename):
    return [json.loads(line) for line in (SAMPLE / "evaluation" / "datasets" / filename).read_text().splitlines()
            if line.strip()]


def test_sample_cases_keep_the_baseline_human_access_and_adversarial_coverage():
    curated = cases("curated.jsonl")
    adversarial = cases("adversarial.jsonl")
    assert len({case["id"] for case in curated + adversarial}) == len(curated + adversarial)
    assert any(case["id"] == "cur-01" and case["expect"] == "explain" for case in curated)
    assert any(case["id"] == "cur-05" and case["expect"] == "escalate" for case in curated)
    assert any(case["id"] == "adv-06" and case["expect"] == "escalate" for case in adversarial)
    assert any(case["expect"] == "explain_same_as_any_member" for case in adversarial)
    assert all(set(case) <= {"id", "member_ref", "utterance", "expect", "persona"} for case in curated + adversarial)


def test_sample_deployment_requires_both_channel_modes_and_its_own_evaluation():
    definition = json.loads((SAMPLE / "deployment.json").read_text())
    assert definition["watermark"] == "ILLUSTRATIVE - synthetic data"
    assert definition["deployed_checks"] == ["channel", "voice"]
    assert {"kind": "module", "targets": ["pension_wtp.evaluation.run_evals"]} in definition["local_checks"]
