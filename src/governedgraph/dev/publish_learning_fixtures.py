"""Explicit gg-dev-only stand-in for telemetry and the DPO-governed cohort feed."""
from __future__ import annotations

import hashlib
import argparse
import json
import os
from pathlib import Path

from governedgraph.common.config import DATA_DIR
from governedgraph.learning.azure_inputs import WATERMARK
from governedgraph.learning.ranking_pipeline import load_outcome_aggregates, load_thresholds
from governedgraph.learning.uplift import ArmOutcome


def fixture_payloads(root: Path) -> dict[tuple[str, str], bytes]:
    from governedgraph.agents.fairness_explainability import run_cohort_audit

    outcomes = root / "synthetic" / "outcome_aggregates_illustrative.csv"
    cohorts = root / "fairness" / "cohort_aggregates_illustrative.csv"
    uplift = root / "synthetic" / "uplift_window_illustrative.json"
    load_outcome_aggregates(outcomes)
    run_cohort_audit(load_thresholds(), cohorts)
    window = json.loads(uplift.read_text())
    if (set(window) != {"window_id", "window_closed", "treatment", "control", "watermark"}
            or window["watermark"] != WATERMARK or not isinstance(window["window_closed"], bool)
            or not isinstance(window["window_id"], str) or not window["window_id"].startswith("synthetic-")):
        raise ValueError("an explicitly illustrative synthetic evaluation window is required")
    ArmOutcome.from_dict(window["treatment"])
    ArmOutcome.from_dict(window["control"])
    return {
        ("learning-aggregates", "outcomes.csv"): outcomes.read_bytes(),
        ("learning-aggregates", "uplift.json"): uplift.read_bytes(),
        ("cohort-zone", "cohort_aggregates.csv"): cohorts.read_bytes(),
    }


def publish(clients: dict, payloads: dict[tuple[str, str], bytes]) -> None:
    from azure.core.exceptions import ResourceExistsError

    for (container, name), data in payloads.items():
        blob = clients[container].get_blob_client(name)
        metadata = {"watermark": WATERMARK, "sha256": hashlib.sha256(data).hexdigest(),
                    "source": "repository-dev-fixture"}
        try:
            blob.upload_blob(data, overwrite=False, metadata=metadata)
        except ResourceExistsError:
            print(json.dumps({"existing_fixture": f"{container}/{name}"}), flush=True)
        if blob.download_blob().readall() != data or dict(blob.get_blob_properties().metadata) != metadata:
            raise ValueError("existing fixture differs from the labelled repository source")


def main() -> None:
    if os.environ.get("AZURE_ENV_NAME") != "gg-dev" or os.environ.get("DEPLOY_DEV_FIXTURES") != "true":
        raise RuntimeError("fixture publishing requires explicit gg-dev deployment opt-in")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initialize-only", action="store_true", help="preserve every existing upstream input")
    args = parser.parse_args()
    payloads = fixture_payloads(DATA_DIR)
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient

    credential = DefaultAzureCredential()
    clients = {
        "learning-aggregates": BlobServiceClient(
            os.environ["EXPLAINABILITY_STORAGE_URL"], credential=credential,
        ).get_container_client("learning-aggregates"),
        "cohort-zone": BlobServiceClient(
            os.environ["RANKING_STORAGE_URL"], credential=credential,
        ).get_container_client("cohort-zone"),
    }
    if args.initialize_only:
        initialize(clients, payloads)
    else:
        publish(clients, payloads)
    print(json.dumps({"fixture_publisher": "passed", "watermark": WATERMARK,
                      "blob_ids": [f"{container}/{name}" for container, name in payloads]}))


def initialize(clients: dict, payloads: dict[tuple[str, str], bytes]) -> None:
    from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError

    for (container, name), data in payloads.items():
        blob = clients[container].get_blob_client(name)
        try:
            blob.get_blob_properties()
        except ResourceNotFoundError as error:
            if getattr(error, "error_code", None) != "BlobNotFound":
                raise
            try:
                blob.upload_blob(data, overwrite=False, metadata={
                    "watermark": WATERMARK, "sha256": hashlib.sha256(data).hexdigest(),
                    "source": "repository-dev-fixture",
                })
            except ResourceExistsError:
                pass  # A concurrent upstream publication always wins over bootstrap.


if __name__ == "__main__":
    main()
