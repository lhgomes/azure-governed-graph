"""Framework inputs are explicit; selecting the sample supplies its offline defaults."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PROBE = ("import json; from governedgraph.common import config as c; "
         "print(json.dumps([str(c.DATA_DIR), c.LOCAL_VALUES_PROVIDER, c.TEXT_DEMO_MODULE]))")


def defaults(*, sample=False, **env):
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GOVERNEDGRAPH_")}
    run = subprocess.run([sys.executable, "-c", ("import pension_wtp; " if sample else "") + PROBE],
                         cwd=ROOT, capture_output=True, text=True, check=False,
                         env={**clean, "PYTHONPATH": str(ROOT / "src") + ":" + str(ROOT / "examples"), **env})
    return run.returncode, (json.loads(run.stdout) if run.returncode == 0 else run.stderr)


def test_explicit_sample_entry_point_supplies_offline_defaults():
    code, (data, provider, demo) = defaults(sample=True)
    assert code == 0
    assert data == str(ROOT / "examples" / "pension_wtp" / "data")
    assert provider == "pension_wtp.member_values_api.data:get_member_values"
    assert demo == "pension_wtp.demo.run_demo"


def test_framework_without_explicit_data_has_no_domain_default():
    code, error = defaults()
    assert code != 0 and "explicit GOVERNEDGRAPH_DATA_DIR" in error
    assert "pension" not in (ROOT / "src/governedgraph/common/config.py").read_text()


def test_individual_settings_still_override_the_selected_sample():
    code, (data, provider, demo) = defaults(sample=True, GOVERNEDGRAPH_DATA_DIR="/elsewhere/pack",
                                          GOVERNEDGRAPH_LOCAL_VALUES_PROVIDER="vendor.values:get",
                                          GOVERNEDGRAPH_TEXT_DEMO_MODULE="vendor.demo")
    assert code == 0
    assert (data, provider, demo) == ("/elsewhere/pack", "vendor.values:get", "vendor.demo")


def test_azure_extension_import_does_not_supply_missing_runtime_inputs():
    code, error = defaults(sample=True, GOVERNEDGRAPH_MODE="azure")
    assert code != 0 and "explicit GOVERNEDGRAPH_DATA_DIR" in error


def test_deploy_runner_default_definition_follows_the_setting():
    from scripts.delivery import Inputs

    assert Inputs.__dataclass_fields__["composition"].default is __import__("dataclasses").MISSING
    assert "DEFAULT_SAMPLE_DEFINITION" not in (ROOT / "scripts/deploy.py").read_text()


def _dockerfile_copies():
    text = "\n".join((ROOT / "examples/pension_wtp" / name).read_text()
                     for name in ("Dockerfile", "Dockerfile.p2"))
    copies = []
    for line in text.splitlines():
        if line.startswith("COPY ") and "examples/" in line:
            parts = [p for p in line.split()[1:] if not p.startswith("--")]
            copies.append(parts)
    return copies


def test_every_dockerfile_copy_resolves_to_a_real_file_for_the_bundled_sample():
    copies = _dockerfile_copies()
    assert len(copies) >= 8
    for source, destination in copies:
        assert (ROOT / source).exists(), source
        assert destination.startswith("./examples/pension_wtp/"), destination


def test_dockerfile_defaults_the_sample_and_refuses_an_unsafe_name():
    source = (ROOT / "Dockerfile").read_text()
    assert "SAMPLE" not in source and "examples/" not in source and "pension" not in source
    code, error = defaults(GOVERNEDGRAPH_MODE="azure")
    assert code != 0 and "explicit GOVERNEDGRAPH_DATA_DIR" in error


def test_every_azd_service_builds_with_the_selected_sample_and_bicep_runs_the_same_package():
    manifest = (ROOT / "azure.yaml").read_text()
    assert "services:" not in manifest and "hooks:" not in manifest and "pip install" not in manifest
    composition = json.loads((ROOT / "examples/pension_wtp/deployment.json").read_text())
    compute = (ROOT / "infra" / "modules" / "compute.bicep").read_text()
    assert "module: memberValuesModule" in compute and "pension_wtp" not in compute
    assert composition["member_values_module"] == "pension_wtp.member_values_api.app:app"
    assert "memberValuesModule: memberValuesModule" in (ROOT / "infra/workloads.bicep").read_text()
