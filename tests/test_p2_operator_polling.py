"""ILLUSTRATIVE - synthetic data. ARM observation must not restart P2 work."""
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import run_p2_container_jobs as p2

JOB = {"id": "/subscriptions/s/resourceGroups/rg/providers/Microsoft.App/jobs/job-gg-p2-pipeline"}
IMAGE = "crmlsynthetic.azurecr.io/governedgraph/p2@sha256:" + "a" * 64
CYCLE = "cycle-" + "a" * 16


def test_operator_cli_help_works_without_an_inherited_pythonpath():
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = subprocess.run([sys.executable, "-m", "scripts.run_p2_container_jobs", "--help"],
                            cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, check=True)
    assert "--resume-cycle" in result.stdout


def execution(stage, *, status="Succeeded", image=IMAGE, name=None):
    return {
        "name": name or "execution-" + stage, "properties": {
            "status": status, "template": {"containers": [{
                "name": "pipeline", "image": image, "env": [
                    {"name": "GOVERNEDGRAPH_P2_CYCLE_ID", "value": CYCLE},
                    {"name": "GOVERNEDGRAPH_P2_STAGE", "value": stage},
                ],
            }]},
        },
    }


def test_transient_arm_503_retries_the_same_read_not_the_job_start(monkeypatch, capsys):
    calls, sleeps = [], []

    def request(args, **kwargs):
        calls.append(args)
        if len(calls) < 3:
            raise subprocess.CalledProcessError(
                1, args, stderr="ERROR: Service Unavailable(<html>AzureResourceManager</html>)")
        return json.dumps(execution("rank"))

    monkeypatch.setattr(p2.subprocess, "check_output", request)
    monkeypatch.setattr(p2.time, "sleep", sleeps.append)
    assert p2.wait(JOB, "execution-rank") == "Succeeded"
    assert len(calls) == 3 and calls[0] == calls[1] == calls[2]
    assert all("post" not in call and "get" in call for call in calls)
    assert len(sleeps) == 2 and "p2_arm_read_retry" in capsys.readouterr().err


@pytest.mark.parametrize("error", ["ERROR: (AuthorizationFailed) Denied", "ERROR: ResourceNotFound",
                                     "ERROR: InvalidAuthenticationToken", "ERROR: BadRequest"])
def test_nontransient_read_errors_are_not_retried(monkeypatch, error):
    calls = []

    def request(args, **kwargs):
        calls.append(args)
        raise subprocess.CalledProcessError(1, args, stderr=error)

    monkeypatch.setattr(p2.subprocess, "check_output", request)
    monkeypatch.setattr(p2.time, "sleep", lambda _: pytest.fail("must not retry"))
    with pytest.raises(subprocess.CalledProcessError):
        p2.az_json("rest", "--method", "get", "--url", "https://management.azure.com/synthetic")
    assert len(calls) == 1


def test_ambiguous_job_start_is_never_retried(monkeypatch):
    calls = []

    def request(args, **kwargs):
        calls.append(args)
        raise subprocess.CalledProcessError(1, args, stderr="ERROR: Service Unavailable")

    monkeypatch.setattr(p2.subprocess, "check_output", request)
    monkeypatch.setattr(p2.time, "sleep", lambda _: pytest.fail("must not retry a write"))
    with pytest.raises(subprocess.CalledProcessError):
        p2.az_json("rest", "--method", "post", "--url", "https://management.azure.com/synthetic/start")
    assert len(calls) == 1


def test_exhausted_arm_read_is_unknown_not_a_failed_stage(monkeypatch):
    calls = []

    def request(args, **kwargs):
        calls.append(args)
        raise subprocess.CalledProcessError(1, args, stderr="ERROR: Service Unavailable")

    monkeypatch.setattr(p2.subprocess, "check_output", request)
    monkeypatch.setattr(p2.time, "sleep", lambda _: None)
    with pytest.raises(p2.P2ObservationUnavailable, match="not known"):
        p2.wait(JOB, "execution-rank")
    assert len(calls) == 5


def test_expired_arm_read_authentication_is_observation_unavailable_without_retry(monkeypatch):
    calls = []

    def request(args, **kwargs):
        calls.append(args)
        raise subprocess.CalledProcessError(
            1, args, stderr='ERROR: Unauthorized({"error":{"code":"ExpiredAuthenticationToken"}})')

    monkeypatch.setattr(p2.subprocess, "check_output", request)
    monkeypatch.setattr(p2.time, "sleep", lambda _: pytest.fail("authentication requires operator refresh"))
    with pytest.raises(p2.P2ObservationUnavailable, match="authentication expired"):
        p2.wait(JOB, "execution-rank")
    assert len(calls) == 1


def test_arm_retry_cannot_extend_the_execution_deadline(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(p2.time, "monotonic", lambda: clock[0])

    def request(args, **kwargs):
        assert kwargs["timeout"] <= 1
        clock[0] = 1.0
        raise subprocess.CalledProcessError(1, args, stderr="ERROR: Service Unavailable")

    monkeypatch.setattr(p2.subprocess, "check_output", request)
    monkeypatch.setattr(p2.time, "sleep", lambda _: pytest.fail("deadline is exhausted"))
    with pytest.raises(p2.P2ObservationUnavailable):
        p2.wait(JOB, "execution-rank", timeout=1)


def test_b6_a_confirmed_failed_execution_is_not_retried_or_relabelled(monkeypatch):
    monkeypatch.setattr(p2.subprocess, "check_output", lambda *a, **k: json.dumps(execution("rank", status="Failed")))
    monkeypatch.setattr(p2.time, "sleep", lambda _: pytest.fail("terminal status must not retry"))
    assert p2.wait(JOB, "execution-rank") == "Failed"


def test_resume_discovers_the_original_watchdog_and_unique_stages(monkeypatch):
    jobs = {"pipeline": JOB, "fairness": {"id": JOB["id"].replace("pipeline", "fairness")}}
    rows = [execution("watchdog", status="Running"), execution("key-isolation"), execution("aggregate"),
            execution("rank", status="Running")]
    monkeypatch.setattr(p2, "execution_history", lambda job: rows if job is JOB else [])
    watchdog, stages = p2.resume_cycle(jobs, CYCLE, IMAGE)
    assert watchdog == "execution-watchdog"
    assert stages == {stage: "execution-" + stage for stage in ("key-isolation", "aggregate", "rank")}


@pytest.mark.parametrize("rows", [
    [execution("watchdog", status="Failed"), execution("key-isolation")],
    [execution("watchdog", status="Running"), execution("rank")],
    [execution("watchdog", status="Running"), execution("key-isolation", image="different")],
    [execution("watchdog", status="Running"), execution("key-isolation"), execution("key-isolation", name="duplicate")],
    [execution("watchdog", status="Running"), execution("key-isolation", status="Failed")],
    [execution("key-isolation")],
])
def test_b6_resume_rejects_failed_missing_or_inconsistent_cycle_evidence(monkeypatch, rows):
    jobs = {"pipeline": JOB, "fairness": {"id": JOB["id"].replace("pipeline", "fairness")}}
    monkeypatch.setattr(p2, "execution_history", lambda job: rows if job is JOB else [])
    with pytest.raises((ValueError, RuntimeError)):
        p2.resume_cycle(jobs, CYCLE, IMAGE)


@pytest.mark.parametrize("watchdog_status", ["Running", "Failed"])
def test_main_resumes_existing_stages_without_replacing_watchdog_or_repeating_work(monkeypatch, watchdog_status):
    config = SimpleNamespace(registry_endpoint="crmlsynthetic.azurecr.io", resource_group="rg", subscription_id="s")
    monkeypatch.setattr(p2.P2Config, "from_environment", lambda: config)
    monkeypatch.setenv("AZURE_ENV_NAME", "gg-dev")
    monkeypatch.setattr(p2.subprocess, "check_output", lambda *a, **k: "AZURE_ENV_NAME=gg-dev\n")
    jobs = {"pipeline": JOB, "fairness": {"id": JOB["id"].replace("pipeline", "fairness")}}

    def request(*args, **kwargs):
        if args[:3] == ("containerapp", "job", "show"):
            return jobs[args[args.index("-n") + 1].removeprefix("job-gg-p2-")]
        return execution("watchdog", status=watchdog_status)

    monkeypatch.setattr(p2, "az_json", request)
    existing = {stage: "original-" + stage for stage in p2.STEPS[:3]}
    monkeypatch.setattr(p2, "resume_cycle", lambda *args: ("original-watchdog", existing))
    started, waited, proof = [], [], []

    def start(jobs, config, stage, cycle, image, **kwargs):
        assert stage not in ("watchdog", *existing)
        assert cycle == CYCLE and image == IMAGE
        started.append(stage)
        return "new-" + stage

    def wait(job, name, **kwargs):
        waited.append(name)
        return "Succeeded"

    monkeypatch.setattr(p2, "start", start)
    monkeypatch.setattr(p2, "wait", wait)
    monkeypatch.setattr(p2, "fairness_agent_proof", lambda *args: proof.append("proof"))
    args = ["--image", IMAGE, "--environment", "gg-dev", "--resume-cycle", CYCLE, "--fairness-agent-proof"]
    if watchdog_status == "Failed":
        with pytest.raises(RuntimeError, match="no longer active"):
            p2.main(args)
        assert started == [] and proof == []
    else:
        p2.main(args)
        assert started == list(p2.STEPS[3:])
        assert waited[:3] == list(existing.values())
        assert waited[-1] == "original-watchdog" and proof == ["proof"]


def test_resume_history_includes_every_page_and_rejects_foreign_links(monkeypatch):
    root = f"https://management.azure.com{JOB['id']}/executions?api-version=2024-03-01"
    pages = {
        root: {"value": [execution("key-isolation")], "nextLink": root + "&skiptoken=next"},
        root + "&skiptoken=next": {"value": [execution("aggregate")]},
    }
    monkeypatch.setattr(p2, "az_json", lambda *args: pages[args[-1]])
    assert len(p2.execution_history(JOB)) == 2
    pages[root]["nextLink"] = "https://foreign.invalid/executions"
    with pytest.raises(ValueError, match="same job"):
        p2.execution_history(JOB)
    pages[root]["nextLink"] = root
    with pytest.raises(ValueError, match="same job"):
        p2.execution_history(JOB)


def test_parent_owned_cycle_cannot_start_a_second_watchdog(monkeypatch):
    config = SimpleNamespace(registry_endpoint="crmlsynthetic.azurecr.io", resource_group="rg", subscription_id="s")
    monkeypatch.setattr(p2.P2Config, "from_environment", lambda: config)
    monkeypatch.setattr(p2.subprocess, "check_output", lambda *a, **k: "AZURE_ENV_NAME=gg-dev\n")
    jobs = {"pipeline": JOB, "fairness": {"id": JOB["id"].replace("pipeline", "fairness")}}
    monkeypatch.setattr(p2, "az_json", lambda *a, **k: jobs[a[a.index("-n") + 1].removeprefix("job-gg-p2-")])
    monkeypatch.setattr(p2, "execution_history", lambda job: [execution("watchdog", status="Running")])
    monkeypatch.setattr(p2, "start", lambda *a, **k: pytest.fail("must not create a replacement watchdog"))
    with pytest.raises(ValueError, match="already has execution history"):
        p2.main(["--image", IMAGE, "--environment", "gg-dev", "--cycle-id", CYCLE])
