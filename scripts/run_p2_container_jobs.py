"""Operator-side coordinator; no workload identity may start/override fairness jobs."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from io import StringIO
from pathlib import Path
from random import uniform
from urllib.parse import urlsplit

from dotenv import dotenv_values
from collections.abc import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "examples")]

from governedgraph.learning.azure_ml import STEPS, MlConfig, component_environment
from governedgraph.learning.container_jobs import component_states


class P2ObservationUnavailable(RuntimeError):
    """The control plane did not supply a conclusive execution status."""


def transient_read_error(error: subprocess.CalledProcessError) -> bool:
    first = (error.stderr or "").strip().splitlines()
    text = re.sub(r"^ERROR:\s*", "", first[0] if first else "", flags=re.IGNORECASE)
    return bool(re.match(
        r"\(?(?:Service\s*Unavailable|Too\s*Many\s*Requests|Bad\s*Gateway|Gateway\s*Timeout|Internal\s*Server\s*Error)\b"
        r"|(?:HTTP(?:/\S+)?\s+|Status(?:\s+Code)?\s*[:=]\s*)(?:429|500|502|503|504)\b", text, re.IGNORECASE))


def az_json(*args: str, deadline: float | None = None) -> dict:
    read_only = (args[:3] == ("containerapp", "job", "show")
                 or args[:3] == ("rest", "--method", "get"))
    for attempt in range(5):
        remaining = deadline - time.monotonic() if deadline is not None else 60
        if remaining <= 0:
            raise P2ObservationUnavailable("P2 observation deadline exceeded; job outcome is not known")
        try:
            raw = subprocess.check_output(["az", *args, "-o", "json"], text=True,
                                          stderr=subprocess.PIPE, timeout=min(60, remaining))
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            if (read_only and isinstance(error, subprocess.CalledProcessError)
                    and "ExpiredAuthenticationToken" in (error.stderr or "")):
                raise P2ObservationUnavailable("ARM authentication expired; refresh the operator login and inspect "
                                               "the original cycle/watchdog before resuming; job outcome is not known") from error
            transient = isinstance(error, subprocess.TimeoutExpired) or transient_read_error(error)
            if not read_only or not transient:
                if error.stderr:
                    print(error.stderr, file=sys.stderr)
                raise
            delay = min(30, 2 ** (attempt + 1)) + uniform(0, 1)
            remaining = deadline - time.monotonic() if deadline is not None else None
            if attempt == 4 or remaining is not None and remaining <= delay:
                raise P2ObservationUnavailable("ARM reads remain unavailable; job outcome is not known; "
                                               "inspect and resume the existing cycle, not a new job") from error
            print(json.dumps({"event": "p2_arm_read_retry", "attempt": attempt + 1,
                              "delay_seconds": delay}), file=sys.stderr, flush=True)
            time.sleep(delay)
        else:
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise TypeError("ARM response must be an object")
            return result
    raise AssertionError("unreachable ARM retry state")


def wait(job: dict, name: str, *, timeout: float = 3600) -> str:
    deadline = time.monotonic() + timeout
    previous = None
    while time.monotonic() < deadline:
        execution = az_json("rest", "--method", "get", "--url",
                            f"https://management.azure.com{job['id']}/executions/{name}?api-version=2024-03-01",
                            deadline=deadline)
        status = execution["properties"]["status"]
        if status != previous:
            print(json.dumps({"execution": name, "status": status}), flush=True)
            previous = status
        if status in {"Succeeded", "Failed", "Stopped"}:
            return status
        time.sleep(10)
    raise P2ObservationUnavailable(f"P2 execution {name} observation deadline exceeded; job outcome is not known")


def execution_history(job: dict, *, reader: Callable[..., dict] | None = None) -> list[dict]:
    reader = reader or az_json
    url = f"https://management.azure.com{job['id']}/executions?api-version=2024-03-01"
    expected = urlsplit(url)
    rows, visited = [], set()
    while url:
        parsed = urlsplit(url)
        if (parsed.scheme != expected.scheme or parsed.netloc != expected.netloc
                or parsed.path.casefold() != expected.path.casefold() or parsed.fragment or url in visited):
            raise ValueError("P2 resume history must stay on the same job without repeated links")
        visited.add(url)
        page = reader("rest", "--method", "get", "--url", url)
        if not isinstance(page.get("value"), list) or any(not isinstance(row, dict) for row in page["value"]):
            raise ValueError("invalid P2 resume history page")
        rows.extend(page["value"])
        url = page.get("nextLink")
        if url is not None and not isinstance(url, str):
            raise ValueError("invalid P2 resume history link")
    return rows


def resume_cycle(jobs: dict, cycle: str, image: str) -> tuple[str, dict[str, str]]:
    histories = {role: execution_history(job) for role, job in jobs.items()}
    states = component_states(histories, cycle, image)
    watchdogs, stages = [], {}
    for role, rows in histories.items():
        for row in rows:
            containers = row["properties"]["template"]["containers"]
            if len(containers) != 1:
                raise ValueError("unexpected P2 execution template")
            container = containers[0]
            env = {item["name"]: item.get("value") for item in container.get("env", [])}
            if env.get("GOVERNEDGRAPH_P2_CYCLE_ID") != cycle:
                continue
            if not isinstance(row.get("name"), str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", row["name"]):
                raise ValueError("invalid P2 execution name in resume history")
            if env.get("GOVERNEDGRAPH_P2_STAGE") == "watchdog":
                if role != "pipeline" or container["image"] != image:
                    raise ValueError("P2 watchdog does not match the cycle image and isolated role")
                watchdogs.append(row)
            else:
                stages[env["GOVERNEDGRAPH_P2_STAGE"]] = row["name"]
    if len(watchdogs) != 1:
        raise ValueError("resume requires exactly one original P2 watchdog; none will be replaced")
    watchdog = watchdogs[0]
    if watchdog["properties"]["status"] not in {"Running", "Processing", "Pending", "Succeeded"}:
        raise RuntimeError("original P2 watchdog failed or stopped; verify the protective freeze through human recovery")
    earlier_complete = True
    for stage in STEPS:
        if stage in states and (not earlier_complete or states[stage] not in {"Completed", "Running"}):
            raise RuntimeError("P2 cycle has failed or nonsequential stages; verify protective state before recovery")
        earlier_complete = earlier_complete and states.get(stage) == "Completed"
    if watchdog["properties"]["status"] == "Succeeded" and not earlier_complete:
        raise ValueError("watchdog completion lacks complete P2 stage evidence")
    return watchdog["name"], stages


def start(jobs: dict, config: MlConfig, stage: str, cycle: str, image: str, *, fail_audit: bool = False) -> str:
    role = "fairness" if stage == "audit" else "pipeline"
    job = jobs[role]
    env = component_environment(config, "audit" if role == "fairness" else "aggregate")
    env.update({
        "GOVERNEDGRAPH_P2_ROLE": role, "GOVERNEDGRAPH_P2_STAGE": stage, "GOVERNEDGRAPH_P2_CYCLE_ID": cycle, "GOVERNEDGRAPH_P2_IMAGE": image,
        "AZURE_ML_ARTIFACT_URL": "https://" + config.storage_id.rsplit("/", 1)[-1] + ".blob.core.windows.net",
        "P2_PIPELINE_JOB_ID": jobs["pipeline"]["id"], "P2_FAIRNESS_JOB_ID": jobs["fairness"]["id"],
    })
    container = job["properties"]["template"]["containers"][0]
    command = ["python", "-m", "governedgraph.learning.container_jobs", "--stage", stage]
    if fail_audit:
        command = ["python", "-c", "raise RuntimeError('ILLUSTRATIVE - synthetic data: audit failure before component code')"]
    body = {"containers": [{
        "name": container["name"], "image": image, "command": command,
        "env": [{"name": name, "value": value} for name, value in env.items()],
        "resources": {"cpu": 0.5, "memory": "1Gi"},
    }]}
    result = az_json("rest", "--method", "post", "--url",
                     f"https://management.azure.com{job['id']}/start?api-version=2024-03-01",
                     "--body", json.dumps(body))
    print(json.dumps({"cycle_id": cycle, "stage": stage, "execution": result["name"]}), flush=True)
    return result["name"]


def fairness_agent_proof(job: dict, image: str, values: dict[str, str]) -> str:
    """Run the protective agent once on its own fairness job; the stored job template is not changed."""
    container = job["properties"]["template"]["containers"][0]
    env = {item["name"]: item["value"] for item in container.get("env", []) if "value" in item}
    required = {"AZURE_AI_PROJECT_ENDPOINT": values.get("AZURE_AI_PROJECT_ENDPOINT", ""),
                "AZURE_AI_FAIRNESS_AGENT_VERSION": values.get("AZURE_AI_FAIRNESS_AGENT_VERSION", ""),
                "FAIRNESS_MANAGED_IDENTITY_CLIENT_ID": values.get("FAIRNESS_CLIENT_ID", "")}
    if not all(required.values()) or not required["AZURE_AI_FAIRNESS_AGENT_VERSION"].isdecimal():
        raise ValueError("the fairness-agent proof needs the project endpoint, pinned agent version and fairness identity")
    env.update(required)
    env["GOVERNEDGRAPH_P2_STAGE"] = "fairness-agent-proof"
    body = {"containers": [{
        "name": container["name"], "image": image,
        "command": ["python", "-m", "governedgraph.agents.fairness_runtime", "--action", "audit"],
        "env": [{"name": name, "value": value} for name, value in env.items()],
        "resources": {"cpu": 0.5, "memory": "1Gi"},
    }]}
    result = az_json("rest", "--method", "post", "--url",
                     f"https://management.azure.com{job['id']}/start?api-version=2024-03-01",
                     "--body", json.dumps(body))
    print(json.dumps({"stage": "fairness-agent-proof", "execution": result["name"]}), flush=True)
    status = wait(job, result["name"])
    if status != "Succeeded":
        raise RuntimeError("the fairness-agent proof did not succeed; verify the protective state before retrying")
    print(json.dumps({"fairness_agent": "Succeeded", "execution": result["name"]}), flush=True)
    return result["name"]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--environment", help="azd environment to read; defaults to the selected one")
    parser.add_argument("--fail-audit", action="store_true")
    parser.add_argument("--fairness-agent-proof", action="store_true",
                        help="after the cycle, run the protective Foundry agent once on the fairness job")
    cycle_args = parser.add_mutually_exclusive_group()
    cycle_args.add_argument("--resume-cycle", help="resume this exact cycle and its original watchdog; never replace failed work")
    cycle_args.add_argument("--cycle-id", help="start this deployment-owned cycle only if it has no execution history")
    args = parser.parse_args(argv)
    if args.resume_cycle and (not re.fullmatch(r"cycle-[0-9a-f]{16}", args.resume_cycle) or args.fail_audit):
        parser.error("--resume-cycle needs an exact cycle ID and cannot inject a new audit failure")
    if args.cycle_id and not re.fullmatch(r"cycle-[0-9a-f]{16}", args.cycle_id):
        parser.error("--cycle-id needs an exact cycle ID")
    flags = ["--environment", args.environment] if args.environment else []
    raw = dotenv_values(stream=StringIO(subprocess.check_output(["azd", "env", "get-values", *flags], text=True)))
    values = {name: value for name, value in raw.items() if value is not None}
    os.environ.update(values)
    config = MlConfig.from_environment()
    registry = config.registry_id.rsplit("/", 1)[-1] + ".azurecr.io/"
    if not args.image.startswith(registry) or "@sha256:" not in args.image:
        raise ValueError("an immutable dedicated-ML-registry image is required")
    if args.fail_audit and values.get("AZURE_ENV_NAME") != "gg-dev":
        raise ValueError("synthetic audit failure is restricted to gg-dev")
    if args.resume_cycle and values.get("AZURE_ENV_NAME") != "gg-dev":
        raise ValueError("operator cycle resume is restricted to gg-dev")
    jobs = {role: az_json("containerapp", "job", "show", "-n", f"job-gg-p2-{role}",
                         "-g", config.resource_group, "--subscription", config.subscription_id)
            for role in ("pipeline", "fairness")}
    for role, job in jobs.items():
        expected = (f"/subscriptions/{config.subscription_id}/resourceGroups/{config.resource_group}"
                    f"/providers/Microsoft.App/jobs/job-gg-p2-{role}")
        if job.get("id", "").casefold() != expected.casefold():
            raise ValueError("P2 job does not match the selected subscription and resource group")
    cycle = args.resume_cycle or args.cycle_id or "cycle-" + uuid.uuid4().hex[:16]
    print(json.dumps({"cycle_id": cycle, "image": args.image,
                      "status": "resuming" if args.resume_cycle else "starting"}), flush=True)
    if args.resume_cycle:
        watchdog, stages = resume_cycle(jobs, cycle, args.image)
        print(json.dumps({"cycle_id": cycle, "resuming": True, "watchdog": watchdog}), flush=True)
    else:
        if args.cycle_id:
            for job in jobs.values():
                for row in execution_history(job):
                    for container in row["properties"]["template"]["containers"]:
                        if any(item["name"] == "GOVERNEDGRAPH_P2_CYCLE_ID" and item.get("value") == cycle
                               for item in container.get("env", [])):
                            raise ValueError("the cycle already has execution history; use --resume-cycle")
        stages = {}
        watchdog = start(jobs, config, "watchdog", cycle, args.image)
    for stage in STEPS:
        execution = stages.get(stage)
        if execution is None:
            if args.resume_cycle:
                status = az_json("rest", "--method", "get", "--url",
                                 f"https://management.azure.com{jobs['pipeline']['id']}/executions/{watchdog}"
                                 "?api-version=2024-03-01")["properties"]["status"]
                if status not in {"Running", "Processing", "Pending"}:
                    raise RuntimeError("original P2 watchdog is no longer active; verify protective state before recovery")
            execution = start(jobs, config, stage, cycle, args.image, fail_audit=args.fail_audit and stage == "audit")
        status = wait(jobs["fairness" if stage == "audit" else "pipeline"], execution)
        if status != "Succeeded":
            watchdog_status = wait(jobs["pipeline"], watchdog)
            print(json.dumps({"cycle_id": cycle, "failed_stage": stage, "watchdog": watchdog_status}), flush=True)
            raise RuntimeError("P2 stage failed; verify the protective freeze before any recurrence")
    if wait(jobs["pipeline"], watchdog) != "Succeeded":
        raise RuntimeError("P2 watchdog did not certify completion")
    print(json.dumps({"cycle_id": cycle, "pipeline": "Completed", "watermark": "ILLUSTRATIVE - synthetic data"}), flush=True)
    if args.fairness_agent_proof:
        fairness_agent_proof(jobs["fairness"], args.image, values)


if __name__ == "__main__":
    try:
        main()
    except P2ObservationUnavailable as error:
        print(json.dumps({"p2_status": "observation_unavailable", "error": str(error)}), file=sys.stderr, flush=True)
        raise SystemExit(3) from error
