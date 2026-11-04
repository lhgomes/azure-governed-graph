"""Temporary Container Apps hosting of the unchanged, identity-isolated P2 stages."""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from governedgraph.learning.azure_inputs import WATERMARK
from governedgraph.learning.azure_ml import STEPS, check_component_states, component_metrics, run_component


def component_states(executions: dict[str, list[dict]], cycle_id: str, image: str) -> dict[str, str]:
    states = {}
    status_map = {"Succeeded": "Completed", "Failed": "Failed", "Stopped": "Canceled",
                  "Running": "Running", "Processing": "Running", "Pending": "Running"}
    for role, rows in executions.items():
        for row in rows:
            properties = row["properties"]
            containers = properties["template"]["containers"]
            if len(containers) != 1:
                raise ValueError("unexpected P2 execution template")
            container = containers[0]
            env = {item["name"]: item.get("value") for item in container.get("env", [])}
            if env.get("GOVERNEDGRAPH_P2_CYCLE_ID") != cycle_id:
                continue
            stage = env.get("GOVERNEDGRAPH_P2_STAGE")
            if stage == "watchdog":
                continue
            if (stage not in STEPS or stage in states or container["image"] != image
                    or (stage == "audit") != (role == "fairness")):
                raise ValueError("P2 execution does not match its isolated stage/image")
            status = properties["status"]
            if status not in status_map:
                raise ValueError("unknown P2 execution status")
            states[stage] = status_map[status]
    return states


def execution_rows(credential, job_id: str) -> list[dict]:
    url = "https://management.azure.com" + job_id + "/executions?api-version=2024-03-01"
    expected = urlsplit(url)
    token = credential.get_token("https://management.azure.com/.default").token
    rows, visited = [], set()
    while url:
        parsed = urlsplit(url)
        if (parsed.scheme != expected.scheme or parsed.netloc != expected.netloc
                or parsed.path.casefold() != expected.path.casefold() or parsed.fragment or url in visited):
            raise ValueError("P2 execution pagination must stay on the same job without repeated links")
        visited.add(url)
        request = Request(url, headers={"Authorization": "Bearer " + token})
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
        if (not isinstance(payload, dict) or not isinstance(payload.get("value"), list)
                or any(not isinstance(row, dict) for row in payload["value"])):
            raise ValueError("invalid P2 execution page")
        rows.extend(payload["value"])
        next_link = payload.get("nextLink")
        if next_link is not None and not isinstance(next_link, str):
            raise ValueError("P2 execution pagination requires a string next link")
        url = next_link
    return rows


def watch(credential, cycle_id: str, image: str, *, timeout: float = 3600) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        executions = {role: execution_rows(credential, os.environ[f"P2_{role.upper()}_JOB_ID"])
                      for role in ("pipeline", "fairness")}
        if check_component_states(component_states(executions, cycle_id, image)):
            return
        time.sleep(10)
    raise TimeoutError("P2 execution monitoring deadline exceeded")


def read_state(container, cycle_id: str, stage: str, image: str) -> dict:
    previous = STEPS[STEPS.index(stage) - 1]
    raw = json.loads(container.get_blob_client(f"p2-cycles/{cycle_id}/{previous}.json").download_blob().readall())
    if (set(raw) != {"cycle_id", "stage", "image", "state", "metrics", "watermark"}
            or raw["cycle_id"] != cycle_id or raw["stage"] != previous or raw["image"] != image
            or raw["watermark"] != WATERMARK or raw["metrics"].get("component_success") != 1
            or not isinstance(raw["state"], dict)):
        raise ValueError("invalid durable P2 dependency artifact")
    return raw["state"]


def main() -> None:
    from governedgraph.common.operational_logging import configure_operational_logging

    configure_operational_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=(*STEPS, "watchdog"), required=True)
    args = parser.parse_args()
    cycle_id = os.environ["GOVERNEDGRAPH_P2_CYCLE_ID"]
    image = os.environ["GOVERNEDGRAPH_P2_IMAGE"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", cycle_id) or "@sha256:" not in image:
        raise ValueError("explicit cycle ID and immutable image are required")
    if (args.stage == "audit") != (os.environ["GOVERNEDGRAPH_P2_ROLE"] == "fairness"):
        raise ValueError("P2 stage must use its isolated role")
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient
    from governedgraph.strategy_service.client import StrategyAdminClient

    credential = DefaultAzureCredential()
    control = StrategyAdminClient(protective_only=True)
    complete = False
    try:
        container = BlobServiceClient(
            os.environ["AZURE_ML_ARTIFACT_URL"], credential=credential,
        ).get_container_client("azureml")
        metrics = {}
        state = None
        if args.stage == "watchdog":
            watch(credential, cycle_id, image)
            metrics = component_metrics("watchdog", None)
        else:
            with TemporaryDirectory(prefix="gg-p2-stage-") as directory:
                source, output = Path(directory) / "input.json", Path(directory) / "output.json"
                if args.stage != "key-isolation":
                    source.write_text(json.dumps(read_state(container, cycle_id, args.stage, image)))
                run_component(args.stage, source if args.stage != "key-isolation" else None, output,
                              reporter=lambda stage, result: metrics.update(component_metrics(stage, result)))
                state = json.loads(output.read_text())
        encoded = json.dumps({"cycle_id": cycle_id, "stage": args.stage, "image": image,
                              "state": state, "metrics": metrics, "watermark": WATERMARK}, sort_keys=True).encode()
        blob = container.get_blob_client(f"p2-cycles/{cycle_id}/{args.stage}.json")
        blob.upload_blob(encoded, overwrite=False, metadata={"watermark": WATERMARK})
        if blob.download_blob().readall() != encoded:
            raise ValueError("durable P2 stage read-back differs")
        complete = True
        result = {"cycle_id": cycle_id, "stage": args.stage, "status": "Completed"}
        if state and state.get("snapshot"):
            result["snapshot_id"] = state["snapshot"]["snapshot_id"]
            result["tier"] = state["snapshot"]["tier"]
        print(json.dumps(result), flush=True)
    finally:
        if not complete:
            print(json.dumps({"cycle_id": cycle_id, "stage": args.stage, "status": "Failed", "freeze": "requested"}),
                  flush=True)
            control.freeze()


if __name__ == "__main__":
    main()
