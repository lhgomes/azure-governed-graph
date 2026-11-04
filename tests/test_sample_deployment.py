"""ILLUSTRATIVE - synthetic data. Sample-owned deployment verification contracts."""
import json

import pytest

from scripts import deploy


def specification(tmp_path, monkeypatch):
    root = tmp_path
    sample = root / "examples" / "sample"
    (sample / "data").mkdir(parents=True)
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_sample.py").write_text("")
    (root / "Dockerfile").write_text("synthetic")
    (root / "requirements-azure.txt").write_text("synthetic")
    (root / "requirements.txt").write_text("synthetic")
    (root / "requirements-p2.txt").write_text("synthetic")
    (root / "pyproject.toml").write_text("synthetic")
    (root / "scripts").mkdir()
    (root / "scripts/deploy.py").write_text("synthetic")
    (root / "scripts/run_dev_proof.py").write_text("synthetic")
    (root / "scripts/run_p2_container_jobs.py").write_text("synthetic")
    (root / "scripts/configure_strategy_recovery.py").write_text("synthetic")
    (root / "src").mkdir()
    value = {
        "name": "sample", "watermark": "ILLUSTRATIVE - synthetic data", "data_directory": "data",
        "local_checks": [{"kind": "pytest", "targets": ["tests/test_sample.py"]},
                         {"kind": "module", "targets": ["sample.evaluation"]}],
        "deployed_checks": ["channel", "voice"],
        "learning": {"fixtures": True, "checks": ["p2-cycle", "fairness-agent"]},
        "release_approval": "release-approval.json",
    }
    (sample / "release-approval.json").write_text("{}")
    path = sample / "deployment.json"
    path.write_text(json.dumps(value))
    monkeypatch.setattr(deploy, "REPO_ROOT", root)
    return path, value


@pytest.mark.parametrize("change", [
    lambda r: r.update(age_band="synthetic"),
    lambda r: r.update(watermark=""),
    lambda r: r.update(data_directory="../../../"),
    lambda r: r.update(deployed_checks=[]),
    lambda r: r.update(deployed_checks=["voice"]),
    lambda r: r.update(deployed_checks=["channel", "channel"]),
    lambda r: r.update(deployed_checks=["channel", "unsupported"]),
    lambda r: r.update(local_checks=[]),
    lambda r: r["local_checks"][0].update(targets=["../../outside.py"]),
    lambda r: r["local_checks"][0].update(targets=["--ignore=tests"]),
    lambda r: r["local_checks"][1].update(targets=["azure.identity"]),
    lambda r: r["local_checks"][1].update(kind="shell"),
    lambda r: r.pop("learning"),
    lambda r: r["learning"].update(age_band="synthetic"),
    lambda r: r["learning"].update(fixtures="true"),
    lambda r: r["learning"].update(checks=[]),
    lambda r: r["learning"].update(checks=["fairness-agent"]),
    lambda r: r["learning"].update(checks=["p2-cycle", "p2-cycle"]),
    lambda r: r["learning"].update(checks=["p2-cycle", "shell"]),
    lambda r: r.pop("release_approval"),
    lambda r: r.update(release_approval="../../../deployment.json"),
    lambda r: r.update(release_approval="missing.json"),
    lambda r: r.update(release_approval="release-approval.txt"),
    lambda r: r.update(release_approval=3),
])
def test_b7_b8_sample_definition_is_closed_and_cannot_escape_its_verification_contract(tmp_path, monkeypatch, change):
    path, value = specification(tmp_path, monkeypatch)
    change(value)
    path.write_text(json.dumps(value))
    with pytest.raises(deploy.DeployError):
        deploy.SampleDefinition.load(str(path))


def test_sample_fingerprint_tracks_cases_definitions_and_runtime_not_only_its_name(tmp_path, monkeypatch):
    path, _ = specification(tmp_path, monkeypatch)
    definition = deploy.SampleDefinition.load(str(path))
    original = definition.fingerprint()
    (definition.data_directory / "synthetic.json").write_text('{"watermark":"ILLUSTRATIVE - synthetic data"}')
    changed = definition.fingerprint()
    assert changed != original
    (tmp_path / "src" / "synthetic.py").write_text("changed = True")
    assert definition.fingerprint() != changed


def test_pension_cases_and_runner_live_in_the_sample_not_the_framework():
    definition = deploy.SampleDefinition.load(deploy.DEFAULT_SAMPLE_DEFINITION)
    assert definition.name == "pension_wtp"
    assert definition.deployed_checks == ("channel", "voice")
    assert definition.learning_fixtures and definition.learning_checks == ("p2-cycle", "fairness-agent")
    assert ("module", ("pension_wtp.evaluation.run_evals",)) in definition.local_checks
    datasets = definition.path.parent / "evaluation" / "datasets"
    for name in ("curated.jsonl", "adversarial.jsonl"):
        assert (datasets / name).is_file()
        assert not (deploy.REPO_ROOT / "evals" / "datasets" / name).exists()
