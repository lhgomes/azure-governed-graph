"""Code-only redeploy of an existing delivery: new image digests through the last reviewed parameters, nothing else."""
from __future__ import annotations

import os
import sys
from contextlib import nullcontext

from scripts import redeploy
from scripts.azd_env import load
from scripts.delivery import ROOT, environment_values, provision, run_together, save_json
from scripts.delivery_evidence import stage
from scripts.deployment_target import same_scope
from scripts.deploy import image_artifacts, readiness
from scripts.p2_schedules import paused


def redeploy_images(runner, inputs, composition) -> dict | None:
    """Roll changed images out through the last reviewed parameters, or return None when more than images changed.

    Everything that can rule a code-only redeploy out runs before any cloud change (building immutable images aside).
    """
    sys.path[:0] = [str(ROOT / "src"), str(ROOT / "examples")]
    os.environ.update({key: value for key, value in environment_values(inputs, composition).items() if value})
    stage(runner, "environment", target={"subscription": inputs.subscription, "resource_group": inputs.resource_group,
                                       "naming_seed": inputs.naming_seed, "composition": composition.name},
          mode="apps_only")
    try:
        plan = prepare(runner, inputs, composition)
    except redeploy.NotCodeOnly as reason:
        print(f"Full deployment required: {reason}.", flush=True)
        return None
    return plan["previous"] if plan["unchanged"] else roll_out(runner, inputs, composition, plan)


def prepare(runner, inputs, composition) -> dict:
    env_file = ROOT / ".azure" / inputs.environment / ".env"
    if not env_file.is_file() or not same_scope(load(env_file=str(env_file)), inputs):
        raise redeploy.NotCodeOnly("the saved azd environment is missing or for another scope")
    previous = redeploy.load_baseline(inputs, root=ROOT)
    current = redeploy.fingerprint(composition, inputs, root=ROOT)
    redeploy.require_unchanged(previous["fingerprint"], current)
    workloads = redeploy.stored_parameters(previous["baseline"]["workloads"], root=ROOT)["configuration"]
    bootstrap = redeploy.stored_parameters(previous["baseline"]["bootstrap"], root=ROOT)
    if inputs.operator_id and inputs.operator_id != workloads.get("principalId"):
        raise redeploy.NotCodeOnly("the delivery operator differs from the deployed one")
    print("Preparing immutable images...", flush=True)
    stage(runner, "immutable images")
    images = image_artifacts(runner, workloads["foundation"], composition, subscription=inputs.subscription)
    changed = redeploy.changed_images(previous["images"], images)
    plan = {"previous": previous, "current": current, "images": images, "changed": changed,
            "workloads": redeploy.replace_images(workloads, redeploy.WORKLOAD_IMAGES, images),
            "bootstrap": redeploy.replace_images(bootstrap, redeploy.BOOTSTRAP_IMAGES, images)}
    plan["unchanged"] = not changed & {"runtime", "p2", "probe", "fixtures"}
    if plan["unchanged"]:
        print("No deployable image changed; nothing was deployed.", flush=True)
    return plan


def roll_out(runner, inputs, composition, plan: dict) -> dict:
    previous, images, changed = plan["previous"], plan["images"], plan["changed"]
    roll_workloads = bool(changed & {"runtime", "p2"})
    roll_jobs = bool(changed & {"runtime", "probe", "fixtures"})
    outputs = previous["outputs"]
    tasks = {}
    if roll_workloads:
        tasks["workloads"] = lambda: provision(runner, inputs, composition.workload_template,
                                               {"configuration": plan["workloads"]})["deployment"]
    if roll_jobs:
        tasks["jobs"] = lambda: provision(runner, inputs, redeploy.BOOTSTRAP_TEMPLATE, plan["bootstrap"])
    if tasks:
        # The two templates deploy disjoint resources (apps and RBAC versus jobs), so they roll out side by side.
        print("Rolling out workload and job images...", flush=True)
        stage(runner, "complete workloads" if roll_workloads else "bootstrap job images",
              images={role: images[role]["image"] for role in ("runtime", "p2")})
        with paused(runner, inputs) if roll_workloads else nullcontext():
            outputs = run_together(tasks).get("workloads", outputs)
    for key, role in (("P2_CONTAINER_IMAGE", "p2"), ("DEV_FIXTURES_IMAGE", "fixtures"), ("DEV_ISSUER_IMAGE", "probe")):
        if role in changed:
            runner.run(["azd", "env", "set", key, images[role]["image"], "--environment", inputs.environment])
    print("Checking deployment readiness...", flush=True)
    stage(runner, "readiness")
    readiness(runner, inputs, outputs,
              images={service: images["runtime"]["image"] for service in redeploy.REQUIRED_APPS} if roll_workloads else None)
    receipt = {**previous, "images": images, "outputs": outputs, "readiness": "passed",
               "fingerprint": plan["current"], "mode": "apps_only", "based_on": previous.get("run_id")}
    evidence = getattr(runner, "evidence", None)
    if evidence is not None:
        receipt.update(run_id=evidence.run_id, evidence_path=str(evidence.path),
                       baseline={**previous["baseline"], **redeploy.baselines(evidence.data, composition)})
    save_json(ROOT / ".run/delivery.json", receipt)
    return receipt
