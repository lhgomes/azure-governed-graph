"""Native scheduled, identity-isolated P2 jobs; shared renewable Blob lease, no job-start rights."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable
from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler

from governedgraph.common.config import settings
from governedgraph.common.workflow import run_sync
from governedgraph.learning.azure_inputs import BlobLearningInputs, WATERMARK
from governedgraph.learning.azure_ml import run_component
from governedgraph.learning.pipeline_steps import PipelineState, run_guarded_stage
from governedgraph.learning.ranking_pipeline import load_thresholds
from governedgraph.orchestrator.store import BlobRecordStore
from governedgraph.strategy_service.client import StrategyAdminClient
from governedgraph.strategy_service.snapshot import default_signer

COMMAND = ["python", "-m", "governedgraph.learning.scheduled_jobs"]
AUDIT_FIELDS = {"cycle_id", "cadence_seconds", "image", "audit_id", "outcome_digest",
                "started_at", "completed_at", "execution_id", "watermark"}


class _ScheduledStage(Executor):
    def __init__(self, stage: str, action: Callable[[], PipelineState | None]):
        super().__init__(id=f"p2-{stage}")
        self.action = action

    @handler
    async def process(self, _signal: bool, ctx: WorkflowContext[bool, PipelineState]) -> None:
        result = self.action()
        if result is None:
            await ctx.send_message(True)
        else:
            await ctx.yield_output(result)


def cycle_window(now: datetime, cadence: int) -> tuple[str, datetime]:
    if now.tzinfo is None or isinstance(cadence, bool) or cadence not in {300, 14400}:
        raise ValueError("approved UTC cadence must be five-minute proof or four-hour normal schedule")
    start = int(now.timestamp()) // cadence * cadence
    return f"schedule-{cadence}-{start}", datetime.fromtimestamp(start, timezone.utc)


def validate_audit(record: dict, cycle: str, cadence: int, image: str, now: datetime) -> str:
    expected_cycle, start = cycle_window(now, cadence)
    if (set(record) != AUDIT_FIELDS or record["cycle_id"] != cycle or cycle != expected_cycle
            or record["cadence_seconds"] != cadence or record["image"] != image
            or record["watermark"] != WATERMARK or not isinstance(record["audit_id"], str)
            or not record["audit_id"].startswith("audit-") or "/" in record["audit_id"]
            or len(record["outcome_digest"]) != 64):
        raise ValueError("same-cycle successful isolated audit is required")
    started = datetime.fromisoformat(record["started_at"])
    completed = datetime.fromisoformat(record["completed_at"])
    if (started.tzinfo is None or completed.tzinfo is None
            or not start <= started <= completed <= now or (now - completed).total_seconds() >= cadence):
        raise ValueError("audit is stale, future-dated or outside the current cadence window")
    return record["audit_id"]


def run_pipeline(inputs, service, audit: dict, cycle: str, cadence: int, image: str,
                 checkpoint: Callable[[str, dict], None], lease_check: Callable[[], None],
                 clock: Callable[[], datetime], audit_check: Callable[[dict], None],
                 signer_factory=default_signer) -> PipelineState:
    if isinstance(inputs, BlobLearningInputs):
        validate_audit(audit, cycle, cadence, image, clock())
        inputs.pin_outcome_digest(audit["outcome_digest"])

    def guard() -> str:
        lease_check()
        audit_id = validate_audit(audit, cycle, cadence, image, clock())
        audit_check(audit)
        if hashlib.sha256(inputs.outcome_bytes()).hexdigest() != audit["outcome_digest"]:
            raise ValueError("aggregate inputs changed after this cycle's audit")
        return audit_id

    audit_id = guard()
    state = PipelineState()

    def perform(stage: str) -> PipelineState | None:
        nonlocal state
        guard()
        if stage == "audit":
            inputs.ranking_inputs(audit_id, load_thresholds())
            state.audit_id = audit_id
            state.completed_stage = "audit"
        else:
            signer = signer_factory(signing=stage == "sign") if stage in {"sign", "tier", "publish"} else None
            state = run_guarded_stage(stage, state, inputs, service, signer)
        checkpoint(stage, asdict(state))
        return state if stage == "uplift" else None

    async def execute() -> PipelineState:
        stages = [_ScheduledStage(stage, lambda stage=stage: perform(stage))
                  for stage in ("aggregate", "rank", "audit", "veto", "sign", "tier", "publish", "uplift")]
        builder = WorkflowBuilder(start_executor=stages[0], name="governed-scheduled-learning")
        for source, target in zip(stages, stages[1:]):
            builder.add_edge(source, target)
        outputs = (await builder.build().run(True)).get_outputs()
        if len(outputs) != 1 or not isinstance(outputs[0], PipelineState):
            raise RuntimeError("scheduled workflow must produce one fully guarded pipeline state")
        return outputs[0]

    return run_sync(execute())


def put_once(blob, payload: dict) -> None:
    from azure.core.exceptions import ResourceExistsError

    encoded = json.dumps(payload, sort_keys=True).encode()
    try:
        blob.upload_blob(encoded, overwrite=False, metadata={"watermark": WATERMARK})
    except ResourceExistsError:
        print(json.dumps({"event": "p2_scheduled_artifact_exists", "blob_id": blob.blob_name}), flush=True)
    if blob.download_blob().readall() != encoded:
        raise ValueError("scheduled P2 immutable artifact conflict")


def execute(role: str, cadence: int, image: str, artifacts, audits, lease_store, inputs,
            control, audit_check: Callable[[dict], None], execution_id: str,
            service_factory=StrategyAdminClient, clock=lambda: datetime.now(timezone.utc)) -> dict:
    if role not in {"fairness", "pipeline"} or "@sha256:" not in image:
        raise ValueError("isolated scheduled role and immutable image are required")
    now = clock()
    cycle, _ = cycle_window(now, cadence)
    complete = False
    try:
        with lease_store.transaction("p2-scheduled-cycle") as transaction:
            def lease_check() -> None:
                transaction.write(json.dumps({"cycle_id": cycle, "role": role, "checked_at": clock().isoformat()}))

            lease_check()
            prefix = f"p2-schedules/{cycle}/"
            completed_blob = artifacts.get_blob_client(prefix + role + "-completed.json")
            if role == "fairness":
                audit_id = inputs.run_fairness(
                    control, expected_digest=hashlib.sha256(inputs.outcome_bytes()).hexdigest(),
                )
                record = {"cycle_id": cycle, "cadence_seconds": cadence, "image": image,
                          "audit_id": audit_id, "outcome_digest": hashlib.sha256(inputs.outcome_bytes()).hexdigest(),
                          "started_at": now.isoformat(), "completed_at": clock().isoformat(),
                          "execution_id": execution_id, "watermark": WATERMARK}
                validate_audit(record, cycle, cadence, image, clock())
                # This hand-off is in the fairness-writable / P2-read-only container, not shared artifacts.
                put_once(audits.get_blob_client(f"scheduled/{cycle}.json"), record)
                result = {"cycle_id": cycle, "role": role, "audit_id": audit_id, "status": "Succeeded"}
            else:
                audit = json.loads(audits.get_blob_client(f"scheduled/{cycle}.json").download_blob().readall())
                validate_audit(audit, cycle, cadence, image, clock())
                with TemporaryDirectory(prefix="gg-p2-key-check-") as directory:
                    output = Path(directory) / "key-isolation.json"
                    run_component("key-isolation", None, output, reporter=lambda *_: None)
                    isolation = json.loads(output.read_text())
                if (isolation.get("rbac_denial") is not True or isolation.get("identity_role") != "pipeline"
                        or isolation.get("principal_id") != os.environ["GOVERNEDGRAPH_P2_PRINCIPAL_ID"]):
                    raise ValueError("scheduled signing isolation prerequisite failed")
                put_once(artifacts.get_blob_client(prefix + "key-isolation.json"), isolation)
                service = service_factory()

                def checkpoint(stage: str, state: dict) -> None:
                    lease_check()
                    put_once(artifacts.get_blob_client(prefix + stage + ".json"), {
                        "cycle_id": cycle, "stage": stage, "image": image, "state": state, "watermark": WATERMARK,
                    })

                state = run_pipeline(inputs, service, audit, cycle, cadence, image,
                                     checkpoint, lease_check, clock, audit_check)
                result = {"cycle_id": cycle, "role": role, "status": "Succeeded",
                          "snapshot_id": state.snapshot["snapshot_id"], "tier": state.snapshot["tier"],
                          "audit_id": state.audit_id, "uplift_decision": state.uplift["decision"]}
            lease_check()
            put_once(completed_blob,
                     {**result, "watermark": WATERMARK, "completed_at": clock().isoformat(), "image": image})
        complete = True
        print(json.dumps({**result, "watermark": WATERMARK}), flush=True)
        return result
    finally:
        if not complete:
            print(json.dumps({"event": "p2_scheduled_failed", "cycle_id": cycle, "role": role,
                              "watermark": WATERMARK, "freeze": "requested"}), flush=True)
            control.freeze()


def check_execution(record: dict, metadata: dict) -> None:
    properties = metadata["properties"]
    containers = properties["template"]["containers"]
    if len(containers) != 1:
        raise ValueError("audit execution must have one isolated container")
    container = containers[0]
    env = {item["name"]: item.get("value") for item in container["env"]}
    if (metadata["name"] != record["execution_id"] or properties["status"] != "Succeeded"
            or container["image"] != record["image"] or container.get("command") != COMMAND
            or env.get("GOVERNEDGRAPH_P2_ROLE") != "fairness" or env.get("GOVERNEDGRAPH_P2_IMAGE") != record["image"]
            or env.get("GOVERNEDGRAPH_P2_CADENCE_SECONDS") != str(record["cadence_seconds"])
            or env.get("GOVERNEDGRAPH_P2_STAGE") != "audit"):
        raise ValueError("this cycle's actual isolated fairness execution did not succeed")
    start = datetime.fromisoformat(properties["startTime"].replace("Z", "+00:00"))
    if cycle_window(start, record["cadence_seconds"])[0] != record["cycle_id"]:
        raise ValueError("fairness execution belongs to a different cycle")


def execution_checker(credential, job_id: str) -> Callable[[dict], None]:
    import re
    from urllib.request import Request, urlopen

    if not job_id.endswith("/Microsoft.App/jobs/job-gg-p2-fairness"):
        raise ValueError("dedicated fairness job ID is required")

    def check(record: dict) -> None:
        name = record["execution_id"]
        if not isinstance(name, str) or not re.fullmatch(r"job-gg-p2-fairness-[a-z0-9]+", name):
            raise ValueError("invalid isolated fairness execution ID")
        token = credential.get_token("https://management.azure.com/.default").token
        url = f"https://management.azure.com{job_id}/executions/{name}?api-version=2024-03-01"
        with urlopen(Request(url, headers={"Authorization": "Bearer " + token}), timeout=30) as response:
            check_execution(record, json.load(response))

    return check


def main() -> None:
    from governedgraph.common.operational_logging import configure_operational_logging
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient

    configure_operational_logging()
    if not settings.is_azure:
        raise ValueError("native scheduled adapter requires Azure mode")
    role, cadence, image = os.environ["GOVERNEDGRAPH_P2_ROLE"], int(os.environ["GOVERNEDGRAPH_P2_CADENCE_SECONDS"]), os.environ["GOVERNEDGRAPH_P2_IMAGE"]
    if cadence == 300 and settings.environment_name != "gg-dev":
        raise ValueError("shortened schedule proof is dev-only")
    account = os.environ["AZURE_ML_ARTIFACT_URL"]
    with DefaultAzureCredential() as credential:
        artifacts = BlobServiceClient(account, credential=credential).get_container_client("azureml")
        inputs = BlobLearningInputs()
        lease_store = BlobRecordStore(account, "azureml", json.loads)
        execute(role, cadence, image, artifacts, inputs.private.get_container_client("learning-audits"),
                lease_store, inputs, StrategyAdminClient(protective_only=True),
                execution_checker(credential, os.environ["P2_FAIRNESS_JOB_ID"]) if role == "pipeline" else lambda _: None,
                os.environ["CONTAINER_APP_JOB_EXECUTION_NAME"])


if __name__ == "__main__":
    main()
