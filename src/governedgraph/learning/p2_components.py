"""Shared guarded P2 component functions for native Container Apps execution."""
from __future__ import annotations

from collections.abc import Callable
import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

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


@dataclass(frozen=True)
class P2Config:
    subscription_id: str
    resource_group: str
    artifact_storage_url: str
    registry_endpoint: str
    pipeline_client_id: str
    fairness_client_id: str
    pipeline_principal_id: str

    @classmethod
    def from_environment(cls) -> "P2Config":
        names = {
            "subscription_id": "AZURE_SUBSCRIPTION_ID", "resource_group": "AZURE_RESOURCE_GROUP",
            "artifact_storage_url": "P2_ARTIFACT_STORAGE_URL",
            "registry_endpoint": "AZURE_CONTAINER_REGISTRY_ENDPOINT", "pipeline_client_id": "PIPELINE_CLIENT_ID",
            "fairness_client_id": "FAIRNESS_CLIENT_ID", "pipeline_principal_id": "STRATEGY_PUBLISHER_PRINCIPAL_ID",
        }
        values = {key: os.environ.get(name, "") for key, name in names.items()}
        if any(not value for value in values.values()):
            raise ValueError("Native P2 deployment outputs and both isolated identities are required")
        if values["pipeline_client_id"] == values["fairness_client_id"]:
            raise ValueError("P2 and fairness must use different managed identities")
        return cls(**values)


def component_environment(config: P2Config, stage: str) -> dict[str, str]:
    names = (
        "AZURE_KEY_VAULT_URL", "SNAPSHOT_SIGNING_KEY_NAME", "RANKING_STORAGE_URL",
        "EXPLAINABILITY_STORAGE_URL", "STRATEGY_ADMIN_URL", "STRATEGY_API_CLIENT_ID",
    )
    env = {name: os.environ[name] for name in names}
    if stage == "audit":
        env.pop("AZURE_KEY_VAULT_URL")
        env.pop("SNAPSHOT_SIGNING_KEY_NAME")
    env.update({
        "AZURE_CLIENT_ID": (
            config.fairness_client_id if stage == "audit" else config.pipeline_client_id
        ), "GOVERNEDGRAPH_P2_PRINCIPAL_ID": config.pipeline_principal_id,
        "AZURE_SUBSCRIPTION_ID": config.subscription_id, "AZURE_RESOURCE_GROUP": config.resource_group,
    })
    return env


def check_component_states(states: dict[str, str]) -> bool:
    if any(status in {"Failed", "Canceled", "NotResponding"} for name, status in states.items() if name in STEPS):
        raise RuntimeError("P2 component failed; freeze required")
    return all(states.get(name) == "Completed" for name in STEPS)


def run_component(stage: str, source: Path | None, output: Path | None,
                  *, reporter: Callable[[str, dict | None], None]) -> None:
    from governedgraph.learning.azure_inputs import BlobLearningInputs
    from governedgraph.strategy_service.client import StrategyAdminClient
    from governedgraph.strategy_service.snapshot import default_signer

    control = StrategyAdminClient(protective_only=True)
    complete = False
    try:
        if stage == "key-isolation":
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


