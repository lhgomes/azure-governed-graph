"""One setting selects the sample the repository runs by default; every default can still be overridden."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = ("import json; from governedgraph.common import config as c; "
         "print(json.dumps([str(c.DATA_DIR), c.LOCAL_VALUES_PROVIDER, c.TEXT_DEMO_MODULE]))")


def defaults(**env):
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GOVERNEDGRAPH_")}
    run = subprocess.run([sys.executable, "-c", PROBE], cwd=ROOT, capture_output=True, text=True, check=False,
                         env={**clean, "PYTHONPATH": str(ROOT / "src"), **env})
    return run.returncode, (json.loads(run.stdout) if run.returncode == 0 else run.stderr)


def test_default_sample_is_the_bundled_one():
    code, (data, provider, demo) = defaults()
    assert code == 0
    assert data == str(ROOT / "examples" / "pension_wtp" / "data")
    assert provider == "pension_wtp.member_values_api.data:get_member_values"
    assert demo == "pension_wtp.demo.run_demo"


def test_one_setting_switches_every_default_to_your_sample():
    code, (data, provider, demo) = defaults(GOVERNEDGRAPH_SAMPLE="my_domain")
    assert code == 0
    assert data == str(ROOT / "examples" / "my_domain" / "data")
    assert provider == "my_domain.member_values_api.data:get_member_values"
    assert demo == "my_domain.demo.run_demo"


def test_individual_settings_still_override_the_selected_sample():
    code, (data, provider, demo) = defaults(GOVERNEDGRAPH_SAMPLE="my_domain", GOVERNEDGRAPH_DATA_DIR="/elsewhere/pack",
                                            GOVERNEDGRAPH_LOCAL_VALUES_PROVIDER="vendor.values:get")
    assert code == 0
    assert (data, provider, demo) == ("/elsewhere/pack", "vendor.values:get", "my_domain.demo.run_demo")


def test_a_sample_name_that_is_not_a_package_name_is_refused():
    for bad in ("../outside", "a/b", "my-domain", "x.y"):
        code, error = defaults(GOVERNEDGRAPH_SAMPLE=bad)
        assert code != 0 and "GOVERNEDGRAPH_SAMPLE" in error, bad


def test_deploy_runner_default_definition_follows_the_setting():
    probe = "from scripts import deploy; print(deploy.DEFAULT_SAMPLE_DEFINITION)"
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GOVERNEDGRAPH_")}
    for sample, expected in (("", "examples/pension_wtp/deployment.json"), ("my_domain", "examples/my_domain/deployment.json")):
        env = {**clean, **({"GOVERNEDGRAPH_SAMPLE": sample} if sample else {})}
        out = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True, check=False, env=env)
        assert out.stdout.strip() == expected, out.stderr


def _dockerfile_copies(sample):
    """Expand the Dockerfile's COPY lines the way the build does: SAMPLE falls back to pension_wtp when empty."""
    text = (ROOT / "Dockerfile").read_text()
    env = {"GOVERNEDGRAPH_SAMPLE": sample or "pension_wtp"}
    copies = []
    for line in text.splitlines():
        if line.startswith("COPY ") and "examples/" in line:
            parts = [p for p in line.split()[1:] if not p.startswith("--")]
            copies.append([p.replace("${GOVERNEDGRAPH_SAMPLE}", env["GOVERNEDGRAPH_SAMPLE"]) for p in parts])
    return copies


def test_every_dockerfile_copy_resolves_to_a_real_file_for_the_bundled_sample():
    copies = _dockerfile_copies("")
    assert len(copies) >= 8
    for source, destination in copies:
        assert (ROOT / source).exists(), source
        assert destination.startswith("./examples/pension_wtp/"), destination


def test_dockerfile_defaults_the_sample_and_refuses_an_unsafe_name():
    lines = (ROOT / "Dockerfile").read_text().splitlines()
    assert "ARG SAMPLE=pension_wtp" in lines and "ENV GOVERNEDGRAPH_SAMPLE=${SAMPLE:-pension_wtp}" in lines
    start = next(i for i, line in enumerate(lines) if line.startswith("RUN case"))
    script = (lines[start].removeprefix("RUN ").rstrip("\\") + " " + lines[start + 1].strip())
    for value, ok in (("pension_wtp", True), ("my_domain2", True), ("", False), ("1abc", False), ("../x", False),
                      ("a-b", False), ("a b", False)):
        run = subprocess.run(["sh", "-c", script], env={"GOVERNEDGRAPH_SAMPLE": value}, capture_output=True, check=False)
        assert (run.returncode == 0) is ok, value


def test_every_azd_service_builds_with_the_selected_sample_and_bicep_runs_the_same_package():
    manifest = (ROOT / "azure.yaml").read_text()
    docker_lines = [line for line in manifest.splitlines() if "docker: {" in line]
    assert len(docker_lines) == 5
    assert all('buildArgs: ["SAMPLE=${GOVERNEDGRAPH_SAMPLE}"]' in line for line in docker_lines)
    parameters = json.loads((ROOT / "infra" / "main.parameters.json").read_text())["parameters"]
    assert parameters["sampleName"]["value"] == "${GOVERNEDGRAPH_SAMPLE=pension_wtp}"
    compute = (ROOT / "infra" / "modules" / "compute.bicep").read_text()
    assert "module: '${sampleName}.member_values_api.app:app'" in compute and "pension_wtp" not in compute.replace(
        "param sampleName string = 'pension_wtp'", "")
    assert "sampleName: sampleName" in (ROOT / "infra" / "main.bicep").read_text()
