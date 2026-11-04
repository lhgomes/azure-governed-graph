"""Azure ML packaging and managed-identity entry point for the P2 components."""
from __future__ import annotations

import argparse
from collections.abc import Callable
import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
import time

from governedgraph.learning.pipeline_steps import PipelineState, STAGES, run_guarded_stage

log = logging.getLogger("governedgraph.learning")
STEPS = ("key-isolation", *STAGES)


def component_metrics(stage: str, result: dict | None) -> dict[str, float]:
    if stage not in (*STEPS, "watchdog"):
        raise ValueError("unknown P2 component")
    metrics = {"component_success": 1.0}
    if stage == "key-isolation":
        if result is None or result.get("rbac_denial") is not True:
            raise ValueError("key-isolation evidence is missing")
        metrics["key_isolation_passed"] = 1.0
    elif stage == "uplift":
        if result is None:
            raise ValueError("completed pipeline evidence is missing")
        state = PipelineState.from_dict(result)
        if state.completed_stage != "uplift" or not state.approved or state.snapshot is None or state.uplift is None:
            raise ValueError("completed pipeline evidence is invalid")
        metrics.update({
            "tier1_evidence_stored": float(state.snapshot["tier"] == "tier1"),
            "uplift_continue": float(state.uplift["decision"] == "continue"),
        })
    return metrics


def log_component_result(stage: str, result: dict | None) -> None:
    import mlflow

    mlflow.log_metrics(component_metrics(stage, result), synchronous=True)
    if result is not None and stage == "uplift":
        state = PipelineState.from_dict(result)
        if state.snapshot is None:
            raise ValueError("completed pipeline snapshot is missing")
        mlflow.log_params({"snapshot_id": state.snapshot["snapshot_id"], "fairness_audit_id": state.audit_id})


def wait_for_job(get_status: Callable[[], str], *, timeout: float = 7200, interval: float = 10) -> None:
    deadline = time.monotonic() + timeout
    previous = None
    while time.monotonic() < deadline:
        status = get_status()
        if not isinstance(status, str) or not status:
            raise RuntimeError("Azure ML job status is unavailable")
        if status != previous:
            log.info("p2_job_status", extra={"status": status})
            previous = status
        if status == "Completed":
            return
        if status in {"Failed", "Canceled", "CancelRequested", "NotResponding"}:
            raise RuntimeError(f"Azure ML job ended with status {status}")
        time.sleep(interval)
    raise TimeoutError("Azure ML job completion deadline exceeded")


def verify_schedule_acceptance(components: list[dict], strategy_client) -> None:
    from governedgraph.common import domain
    from governedgraph.common.config import DATA_DIR
    from governedgraph.common.contracts import RequestKey
    from governedgraph.common.io import load_json
    from governedgraph.strategy_service.catalogue import Catalogue

    expected = {*STEPS, "watchdog"}
    if (len(components) != len(expected) or {c["stage"] for c in components} != expected
            or any(c["status"] != "Completed" or c["metrics"].get("component_success") != 1 for c in components)):
        raise RuntimeError("live P2 acceptance requires every unique component to complete with evidence")
    by_stage = {c["stage"]: c for c in components}
    uplift = by_stage["uplift"]
    if (by_stage["key-isolation"]["metrics"].get("key_isolation_passed") != 1
            or uplift["metrics"].get("tier1_evidence_stored") != 1
            or uplift["metrics"].get("uplift_continue") != 1):
        raise RuntimeError("live P2 acceptance lacks key isolation, Tier 1 evidence or positive uplift")
    snapshot_id = uplift["params"].get("snapshot_id")
    audit_id = uplift["params"].get("fairness_audit_id")
    if not isinstance(snapshot_id, str) or not snapshot_id or not isinstance(audit_id, str) or not audit_id:
        raise RuntimeError("live P2 acceptance lacks snapshot and fairness audit identities")
    key = RequestKey(*domain.vocabulary()["p2_acceptance_request_key"])
    catalogue = Catalogue.load()
    priors = load_json(DATA_DIR / "catalogue" / "expert_priors_v1.json")["rankings"][key.id]
    verified_arms = set()
    for index in range(1000):
        response = strategy_client.get_ranked({"journey_id": f"J-p2-acceptance-{index}",
                                               **asdict(key)})
        if (response.catalogue_version != catalogue.version
                or "S2" not in response.suspended or "S6" not in response.always_available
                or any(s.strategy_id in response.suspended or not catalogue.is_active(s.strategy_id, s.version)
                       for s in response.ranked)):
            raise RuntimeError("live P2 acceptance failed the served signature, catalogue or suspension checks")
        if response.arm == "control":
            expected_control = [sid for sid in priors if sid not in response.suspended
                                and catalogue.get(sid).eligible_for(key)]
            if (response.exploration or response.source != "expert_priors" or response.signature_valid
                    or [s.strategy_id for s in response.ranked] != expected_control):
                raise RuntimeError("live P2 acceptance control arm is not static")
            verified_arms.add("control")
        elif response.arm == "treatment":
            if not response.signature_valid or response.snapshot_id != snapshot_id or response.source != "snapshot":
                raise RuntimeError("live P2 acceptance served a different snapshot")
            if not response.exploration:
                if not response.ranked or response.ranked[0].strategy_id != "S5":
                    raise RuntimeError("live P2 acceptance did not serve S5 first")
                verified_arms.add("treatment")
        else:
            raise RuntimeError("live P2 acceptance returned an unknown arm")
        if verified_arms == {"control", "treatment"}:
            return
    raise RuntimeError("live P2 acceptance could not verify both arms")


def verify_acceptance_job(status: str, tags: dict[str, str] | None, image: str) -> None:
    if (status != "Completed" or not isinstance(tags, dict) or tags.get("gg_workload") != "bounded-p2"
            or tags.get("gg_image") != image):
        raise RuntimeError("live P2 acceptance requires a completed governed run of the scheduled image")


@dataclass(frozen=True)
class MlConfig:
    subscription_id: str
    resource_group: str
    workspace: str
    storage_id: str
    registry_id: str
    pipeline_client_id: str
    fairness_client_id: str
    pipeline_principal_id: str
    pipeline_compute: str = "gg-p2"
    fairness_compute: str = "gg-fairness"

    @classmethod
    def from_environment(cls) -> "MlConfig":
        names = {
            "subscription_id": "AZURE_SUBSCRIPTION_ID", "resource_group": "AZURE_RESOURCE_GROUP",
            "workspace": "AZURE_ML_WORKSPACE", "storage_id": "AZURE_ML_STORAGE_ID",
            "registry_id": "AZURE_ML_REGISTRY_ID", "pipeline_client_id": "PIPELINE_CLIENT_ID",
            "fairness_client_id": "FAIRNESS_CLIENT_ID", "pipeline_principal_id": "STRATEGY_PUBLISHER_PRINCIPAL_ID",
        }
        values = {key: os.environ.get(name, "") for key, name in names.items()}
        if any(not value for value in values.values()):
            raise ValueError("Azure ML deployment outputs and both isolated compute identities are required")
        if values["pipeline_client_id"] == values["fairness_client_id"]:
            raise ValueError("P2 and fairness must use different managed identities")
        return cls(**values)


def component_environment(config: MlConfig, stage: str) -> dict[str, str]:
    names = (
        "AZURE_KEY_VAULT_URL", "SNAPSHOT_SIGNING_KEY_NAME", "RANKING_STORAGE_URL",
        "EXPLAINABILITY_STORAGE_URL", "STRATEGY_ADMIN_URL", "STRATEGY_API_CLIENT_ID",
    )
    env = {name: os.environ[name] for name in names}
    if stage == "audit":
        env.pop("AZURE_KEY_VAULT_URL")
        env.pop("SNAPSHOT_SIGNING_KEY_NAME")
    env.update({
        "GOVERNEDGRAPH_MODE": "azure", "AZURE_CLIENT_ID": (
            config.fairness_client_id if stage == "audit" else config.pipeline_client_id
        ), "GOVERNEDGRAPH_P2_PRINCIPAL_ID": config.pipeline_principal_id,
        "AZURE_SUBSCRIPTION_ID": config.subscription_id, "AZURE_RESOURCE_GROUP": config.resource_group,
        "AZURE_ML_WORKSPACE": config.workspace,
    })
    return env


def build_pipeline(config: MlConfig, image: str):
    from azure.ai.ml import Input, Output, command, dsl
    from azure.ai.ml.entities import Environment, ManagedIdentityConfiguration

    registry = config.registry_id.rstrip("/").split("/")[-1] + ".azurecr.io/"
    if not image.startswith(registry) or "@sha256:" not in image:
        raise ValueError("P2 image must be immutable and use AZURE_ML_REGISTRY_ID")
    components = {}
    for stage in (*STEPS, "watchdog"):
        takes_input = stage not in {"key-isolation", "watchdog"}
        components[stage] = command(
            name="gg_p2_" + stage.replace("-", "_"),
            display_name=stage,
            command=f"PYTHONPATH=/app/src python -m governedgraph.learning.azure_ml --stage {stage}"
                    + (' --input "${{inputs.previous_state}}"' if takes_input else "")
                    + (' --output "${{outputs.state}}"' if stage != "watchdog" else ""),
            environment=Environment(image=image),
            environment_variables=component_environment(config, stage),
            inputs={"previous_state": Input(type="uri_file", mode="download")} if takes_input else {},
            outputs={"state": Output(type="uri_file", mode="upload")} if stage != "watchdog" else {},
            is_deterministic=False,
        )

    @dsl.pipeline(name="gg_p2_bounded_learning", description="ILLUSTRATIVE - synthetic data; approved reorder only")
    def graph():
        previous = None
        for stage in STEPS:
            node = components[stage]() if previous is None else components[stage](previous_state=previous)
            node.name = stage.replace("-", "_")
            node.display_name = stage
            node.compute = config.fairness_compute if stage == "audit" else config.pipeline_compute
            node.identity = ManagedIdentityConfiguration(
                client_id=config.fairness_client_id if stage == "audit" else config.pipeline_client_id,
            )
            previous = node.outputs.state
        watchdog = components["watchdog"]()
        watchdog.name = "watchdog"
        watchdog.display_name = "watchdog"
        watchdog.compute = config.pipeline_compute
        watchdog.identity = ManagedIdentityConfiguration(client_id=config.pipeline_client_id)
        return {"report": previous}

    job = graph()
    job.settings.default_datastore = "workspaceblobstore"
    job.settings.continue_on_step_failure = True
    job.settings.force_rerun = True
    job.tags = {"gg_workload": "bounded-p2", "data": "synthetic", "gg_image": image}
    return job


def check_component_states(states: dict[str, str]) -> bool:
    if any(status in {"Failed", "Canceled", "NotResponding"} for name, status in states.items() if name in STEPS):
        raise RuntimeError("P2 component failed; freeze required")
    return all(states.get(name) == "Completed" for name in STEPS)


def watch_components() -> None:
    from azure.ai.ml import MLClient
    from azure.identity import DefaultAzureCredential

    root = os.environ["AZUREML_ROOT_RUN_ID"]
    client = MLClient(DefaultAzureCredential(), os.environ["AZURE_SUBSCRIPTION_ID"],
                      os.environ["AZURE_RESOURCE_GROUP"], os.environ["AZURE_ML_WORKSPACE"])
    deadline = time.monotonic() + 3600
    while time.monotonic() < deadline:
        states = {job.display_name: job.status for job in client.jobs.list(parent_job_name=root)}
        if check_component_states(states):
            return
        time.sleep(10)
    raise RuntimeError("P2 pipeline monitoring deadline exceeded; freeze required")


def run_component(stage: str, source: Path | None, output: Path | None,
                  *, reporter: Callable[[str, dict | None], None] = log_component_result) -> None:
    from governedgraph.learning.azure_inputs import BlobLearningInputs
    from governedgraph.strategy_service.client import StrategyAdminClient
    from governedgraph.strategy_service.snapshot import default_signer

    control = StrategyAdminClient(protective_only=True)
    complete = False
    try:
        if stage == "watchdog":
            watch_components()
            result = None
        elif stage == "key-isolation":
            from governedgraph.dev.check_key_isolation import check

            result = check(os.environ["AZURE_KEY_VAULT_URL"], "pipeline", os.environ["GOVERNEDGRAPH_P2_PRINCIPAL_ID"])
            print(json.dumps(result))
        else:
            if source is None:
                raise ValueError("P2 component requires its dependency artifact")
            raw = json.loads(source.read_text())
            if stage == "aggregate":
                if (raw.get("rbac_denial") is not True or raw.get("identity_role") != "pipeline"
                        or raw.get("principal_id") != os.environ["GOVERNEDGRAPH_P2_PRINCIPAL_ID"]):
                    raise ValueError("P2 signing isolation prerequisite did not pass")
                state = PipelineState()
            else:
                state = PipelineState.from_dict(raw)
            service = control if stage == "audit" else StrategyAdminClient()
            signer = default_signer(signing=stage == "sign") if stage in {"sign", "tier", "publish"} else None
            result = asdict(run_guarded_stage(stage, state, BlobLearningInputs(), service, signer))
        if result is not None:
            if output is None:
                raise ValueError("P2 component requires a durable output artifact")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, sort_keys=True))
        reporter(stage, result)
        complete = True
    finally:
        if not complete:
            log.error("p2_component_failed_freezing", extra={"component": stage})
            control.freeze()


def main() -> None:
    from governedgraph.common.operational_logging import configure_operational_logging

    configure_operational_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=(*STEPS, "watchdog"))
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--image")
    parser.add_argument("--preview", type=Path)
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--schedule", action="store_true")
    parser.add_argument("--acceptance-job")
    args = parser.parse_args()
    from azure.core.exceptions import AzureError

    try:
        if args.stage:
            run_component(args.stage, args.input, args.output)
        else:
            config = MlConfig.from_environment()
            if not args.image:
                raise ValueError("a verified immutable P2 image is required")
            job = build_pipeline(config, args.image)
            if args.preview:
                job.dump(args.preview)
            if args.acceptance_job and (args.submit or not args.schedule):
                raise ValueError("acceptance-job requires schedule without a new submission")
            if args.submit or args.schedule:
                from azure.ai.ml import MLClient
                from azure.ai.ml.entities import JobSchedule, RecurrenceTrigger
                from azure.identity import DefaultAzureCredential

                client = MLClient(DefaultAzureCredential(), config.subscription_id, config.resource_group, config.workspace)
                if args.submit:
                    submitted = client.jobs.create_or_update(job, experiment_name="gg-bounded-p2")
                    print(json.dumps({"job_name": submitted.name, "status": submitted.status}))
                    wait_for_job(lambda: client.jobs.get(submitted.name).status)
                elif args.acceptance_job:
                    submitted = client.jobs.get(args.acceptance_job)
                else:
                    raise ValueError("schedule requires a verified acceptance-job or explicit submission")
                accepted = client.jobs.get(submitted.name)
                verify_acceptance_job(accepted.status, accepted.tags, args.image)
                if args.schedule:
                    import mlflow
                    from governedgraph.agents.clients import HttpStrategyClient

                    mlflow.set_tracking_uri(client.workspaces.get(config.workspace).mlflow_tracking_uri)
                    tracking = mlflow.MlflowClient()
                    components = []
                    for child in client.jobs.list(parent_job_name=submitted.name):
                        run = tracking.get_run(child.name)
                        components.append({"stage": child.display_name, "status": child.status,
                                           "metrics": run.data.metrics, "params": run.data.params})
                    verify_schedule_acceptance(components, HttpStrategyClient())
                    client.schedules.begin_create_or_update(JobSchedule(
                        name="gg-bounded-p2", trigger=RecurrenceTrigger(frequency="hour", interval=4), create_job=job,
                    )).result()
            elif not args.preview:
                raise ValueError("select preview or explicit submission")
    except (AzureError, OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
        log.error("p2_pipeline_failed", extra={"error_type": type(error).__name__})
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
