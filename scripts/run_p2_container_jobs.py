"""Operator-side coordinator; no workload identity may start/override fairness jobs."""
from __future__ import annotations

import argparse
from io import StringIO
import json
import os
import subprocess
import time
import uuid

from dotenv import dotenv_values

from governedgraph.learning.azure_ml import MlConfig, STEPS, component_environment


def az_json(*args: str) -> dict:
    return json.loads(subprocess.check_output(["az", *args, "-o", "json"], text=True))


def wait(job: dict, name: str, *, timeout: float = 3600) -> str:
    deadline = time.monotonic() + timeout
    previous = None
    while time.monotonic() < deadline:
        execution = az_json("rest", "--method", "get", "--url",
                            f"https://management.azure.com{job['id']}/executions/{name}?api-version=2024-03-01")
        status = execution["properties"]["status"]
        if status != previous:
            print(json.dumps({"execution": name, "status": status}), flush=True)
            previous = status
        if status in {"Succeeded", "Failed", "Stopped"}:
            return status
        time.sleep(10)
    raise TimeoutError("P2 execution deadline exceeded")


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--environment", help="azd environment to read; defaults to the selected one")
    parser.add_argument("--fail-audit", action="store_true")
    parser.add_argument("--fairness-agent-proof", action="store_true",
                        help="after the cycle, run the protective Foundry agent once on the fairness job")
    args = parser.parse_args()
    flags = ["--environment", args.environment] if args.environment else []
    values = dotenv_values(stream=StringIO(subprocess.check_output(["azd", "env", "get-values", *flags], text=True)))
    os.environ.update({name: value for name, value in values.items() if value is not None})
    config = MlConfig.from_environment()
    registry = config.registry_id.rsplit("/", 1)[-1] + ".azurecr.io/"
    if not args.image.startswith(registry) or "@sha256:" not in args.image:
        raise ValueError("an immutable dedicated-ML-registry image is required")
    if args.fail_audit and values.get("AZURE_ENV_NAME") != "gg-dev":
        raise ValueError("synthetic audit failure is restricted to gg-dev")
    jobs = {role: az_json("containerapp", "job", "show", "-n", f"job-gg-p2-{role}",
                         "-g", config.resource_group) for role in ("pipeline", "fairness")}
    cycle = "cycle-" + uuid.uuid4().hex[:16]
    watchdog = start(jobs, config, "watchdog", cycle, args.image)
    for stage in STEPS:
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
    main()
