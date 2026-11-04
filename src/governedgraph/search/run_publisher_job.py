"""Invoke the private P3 publisher through Azure's control plane, never storage keys."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from governedgraph.search.corpus import ContentDocument


def azure_cli(*args: str) -> str:
    try:
        return subprocess.run(["az", *args, "--only-show-errors"], check=True, capture_output=True,
                              text=True).stdout.strip()
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"Azure CLI failed: {error.stderr.strip()}") from error


def run_publisher_job(candidate: ContentDocument | None = None, *, timeout: float = 1200) -> str:
    if candidate is not None and candidate.status != "approved":
        raise ValueError("remote publication requires P3 approval")
    group = os.environ["AZURE_RESOURCE_GROUP"]
    subscription = os.environ["AZURE_SUBSCRIPTION_ID"]
    job = os.environ["CONTENT_PUBLISH_JOB_NAME"]
    shared_link = os.environ["CONTENT_SEARCH_SHARED_LINK_ID"]
    state = json.loads(azure_cli("rest", "--method", "get", "--url",
        f"https://management.azure.com{shared_link}?api-version=2023-11-01"))
    if state["properties"]["status"] != "Approved" or state["properties"]["provisioningState"] != "Succeeded":
        raise RuntimeError("Search content private link is not approved/ready; owner approval is required")
    base = ("--subscription", subscription, "-g", group, "-n", job)
    if candidate is None:
        execution = azure_cli("containerapp", "job", "start", *base, "--query", "name", "-o", "tsv")
    else:
        template = json.loads(azure_cli("containerapp", "job", "show", *base,
                                      "--query", "properties.template", "-o", "json"))
        container = template["containers"][0]
        container["command"] = ["python", "-m", "governedgraph.search.reindex_on_approval"]
        container["args"] = ["--from-env"]
        container["env"] = [entry for entry in container["env"] if entry["name"] != "CONTENT_APPROVAL_DOCUMENT"]
        container["env"].append({"name": "CONTENT_APPROVAL_DOCUMENT", "value": json.dumps(asdict(candidate))})
        with tempfile.TemporaryDirectory(prefix="gg-p3-") as directory:
            path = Path(directory) / "execution.json"
            path.write_text(json.dumps(template))
            execution = azure_cli("containerapp", "job", "start", *base, "--yaml", str(path),
                                  "--query", "name", "-o", "tsv")
    if not execution:
        raise RuntimeError("Azure did not return a content publisher execution name")
    print(f"content publisher execution: {execution}", flush=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = azure_cli("containerapp", "job", "execution", "show", *base,
                           "--job-execution-name", execution, "--query", "properties.status", "-o", "tsv")
        if status == "Succeeded":
            print("private content publication succeeded", flush=True)
            return execution
        if status not in {"Running", "Processing", "Pending"}:
            raise RuntimeError(f"content publisher {execution} ended with status {status!r}; inspect job logs")
        time.sleep(5)
    raise TimeoutError(f"content publisher {execution} still pending; inspect execution and lease before retrying")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document", type=Path, help="P3-approved synthetic content JSON for remote publication")
    args = parser.parse_args()
    candidate = ContentDocument.from_dict(json.loads(args.document.read_text())) if args.document else None
    run_publisher_job(candidate)


if __name__ == "__main__":
    main()
