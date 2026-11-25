"""Deliver an explicit gg-dev composition; deployment readiness is not evaluation or acceptance."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
import sys
from pathlib import Path
from urllib.request import urlopen

from scripts.delivery import (ROOT, Composition, DeliveryError, Runner, build, build_together, environment_values, provision,
                              run_job, run_together, save_json)
from scripts import redeploy
from scripts.azd_env import EnvError, derived, load
from scripts.deployment_target import new_target, read_inputs, same_scope, select_target
from scripts.p2_schedules import paused
from scripts.delivery_evidence import (Evidence, admin_readiness, network_snapshot, readiness_observation, require_images,
                                       stage)

ISSUER = "https://gg-dev.synthetic.invalid"
SEQUENCE = ("foundation", "immutable images", "bounded bootstrap", "complete workloads", "readiness")


def image_artifacts(runner, foundation: dict, composition, *, subscription: str) -> dict:
    registry = foundation["compute"]["registryName"]
    core = [ROOT / "src", ROOT / "requirements.txt"]
    framework = build_together({
        "runtime": lambda: build(runner, registry, "runtime", "Dockerfile", core + [ROOT / "requirements-azure.txt"], {},
                                 subscription=subscription),
        "p2": lambda: build(runner, registry, "p2", "Dockerfile.p2", core + [ROOT / "requirements-p2.txt"], {},
                            subscription=subscription),
    })
    args = {"FRAMEWORK_RUNTIME": framework["runtime"]["image"]}
    p2_args = {"FRAMEWORK_P2": framework["p2"]["image"]}
    data = composition.data_directory
    domain = [data / "catalogue", data / "content", data / "fairness/thresholds_v1.json",
              data.parent / "__init__.py"]
    runtime_sources = [data / "synthetic/members.json", data.parent / "member_values_api",
                       data / "personas/personas.json", data.parent / "demo/build_simulations.py"]
    derived = build_together({
        "runtime": lambda: build(runner, registry, "runtime", composition.dockerfile, domain + runtime_sources, args,
                                 subscription=subscription),
        "p2": lambda: build(runner, registry, "p2", composition.p2_dockerfile, domain, p2_args,
                            subscription=subscription),
        "fixtures": lambda: build(runner, registry, "fixtures", composition.p2_dockerfile, domain + [
            data / "synthetic/outcome_aggregates_illustrative.csv", data / "synthetic/uplift_window_illustrative.json",
            data / "fairness/cohort_aggregates_illustrative.csv"], p2_args, subscription=subscription),
        "probe": lambda: build(runner, registry, "probe", composition.dockerfile,
                               domain + runtime_sources + [data.parent / "evaluation/datasets"], args,
                               subscription=subscription),
    })
    return {"framework-runtime": framework["runtime"], "framework-p2": framework["p2"], **derived}


def environment(runner, inputs, composition) -> None:
    directory = ROOT / ".azure" / inputs.environment
    if not directory.exists():
        runner.run(["azd", "env", "new", inputs.environment, "--subscription", inputs.subscription,
                    "--location", inputs.location, "--no-prompt"])
    else:
        env_file = directory / ".env"
        if not env_file.is_file() or not same_scope(load(env_file=str(env_file)), inputs):
            raise DeliveryError("saved azd environment scope differs; select the deployment target again "
                                "with --reuse RESOURCE_GROUP or --new")
    values = environment_values(inputs, composition)
    for key, value in values.items():
        runner.run(["azd", "env", "set", key, value, "--environment", inputs.environment])
    os.environ.update(values)


def deploy(runner, inputs, composition, *, catalogue_upgrade: bool = False) -> dict:
    sys.path[:0] = [str(ROOT / "src"), str(ROOT / "examples")]
    from governedgraph.strategy_service.provision_auth import GraphError, graph

    stage(runner, "environment", target={"subscription": inputs.subscription, "resource_group": inputs.resource_group,
                                       "naming_seed": inputs.naming_seed, "location": inputs.location,
                                       "composition": composition.name})
    if not inputs.operator_id:
        from scripts.configure_admin_console import signed_in_user

        print("Resolving delivery operator from the signed-in user...", flush=True)
        os.environ["AZURE_SUBSCRIPTION_ID"] = inputs.subscription
        try:
            inputs = replace(inputs, operator_id=signed_in_user(call=graph))
        except (GraphError, ValueError) as error:
            raise DeliveryError(
                "Cannot resolve operator_id in the selected subscription's tenant; "
                f"sign in as a user or supply operator_id explicitly. {error}"
            ) from None
    environment(runner, inputs, composition)
    from scripts.bootstrap import (approve_search_link, initial_priors, member_public_jwk, publish_agents,
                                   register_identities, release_approval)

    approval = release_approval(composition)
    if catalogue_upgrade:
        from governedgraph.strategy_service.source_upgrade import load_upgrade

        upgrade = load_upgrade(composition.data_directory)
        if upgrade is None:
            raise DeliveryError("explicit catalogue upgrade requires a reviewed source upgrade plan")
        upgrade.validate_approval(approval)
    base = {"environmentName": inputs.environment, "location": inputs.location,
            "searchLocation": inputs.search_location, "resourceGroupName": inputs.resource_group,
            "namingSeed": inputs.naming_seed, "principalId": inputs.operator_id,
            "decisionDeployment": inputs.decision_deployment,
            "publisherEmail": inputs.on_call_email, "deployDevIssuer": True,
            "deploySyntheticContactCentre": True, "deployAdminConsole": True,
            "deployVoicePersona": inputs.voice_demo}
    print("Deploying foundation...", flush=True)
    stage(runner, "foundation")
    foundation = provision(runner, inputs, "infra/main.bicep", {**base, "retainedSubnets": inputs.subnet_parameters()})["foundation"]
    network_snapshot(runner, inputs, foundation, "foundation")
    print("Preparing immutable images...", flush=True)
    stage(runner, "immutable images")
    images = image_artifacts(runner, foundation, composition, subscription=inputs.subscription)
    print("Running bounded bootstrap...", flush=True)
    stage(runner, "bounded bootstrap", images=images)
    os.environ["AZURE_AI_PROJECT_ENDPOINT"] = foundation["ai"]["projectEndpoint"]

    def jobs() -> tuple:
        created = provision(runner, inputs, "infra/bootstrap.bicep", {
            "resourceGroupName": inputs.resource_group, "location": inputs.location, "foundation": foundation,
            "runtimeImage": images["runtime"]["image"], "probeImage": images["probe"]["image"],
            "fixturesImage": images["fixtures"]["image"], "domainEnv": composition.domain_environment,
            "memberTokenIssuer": ISSUER, "deployDevIssuer": True, "deployAdminConsole": True,
        })

        def ingest() -> str:
            approve_search_link(runner, inputs, foundation)
            return run_job(runner, inputs, created["contentJob"])

        done = run_together({
            "key": lambda: member_public_jwk(runner, inputs, created["issuerJob"],
                                             run_job(runner, inputs, created["issuerJob"])),
            "content": ingest, "fixtures": lambda: run_job(runner, inputs, created["fixturesJob"])})
        return done["key"], {"content": done["content"], "fixtures": done["fixtures"]}

    # Only the content job waits for bootstrap.bicep and the Search link; the initial priors need neither, and
    # the workloads below need every result.
    first = run_together({
        "identities": lambda: register_identities(inputs, foundation),
        "agents": lambda: publish_agents(inputs, foundation["ai"]["projectEndpoint"]),
        "priors": lambda: None if catalogue_upgrade else initial_priors(runner, inputs, composition, foundation, images),
        "jobs": jobs})
    registrations, pins, initial = first["identities"], first["agents"], first["priors"]
    public_key, executions = first["jobs"]
    workload = {
        **base, **registrations, "foundation": foundation, "domainEnv": composition.domain_environment,
        "memberValuesModule": composition.member_values_module,
        "orchestratorImage": images["runtime"]["image"], "strategyServiceImage": images["runtime"]["image"],
        "memberValuesImage": images["runtime"]["image"], "contactCentreImage": images["runtime"]["image"],
        "adminImage": images["runtime"]["image"], "p2ContainerImage": images["p2"]["image"],
        "agentsPublished": True,
        "personaAgentPublished": inputs.voice_demo,
        "gapReviewAgentPublished": True,
        "memberTokenIssuer": ISSUER, "memberTokenPublicJwk": public_key, "onCallEmail": inputs.on_call_email,
        "deployVoiceDemo": inputs.voice_demo, "traceSyntheticConversations": inputs.voice_demo,
        "adminTraceLookup": inputs.voice_demo,
    }
    print("Deploying complete workloads...", flush=True)
    stage(runner, "complete workloads", agents={
        role: {key: pin[key] for key in ("agent_name", "agent_version", "definition_sha256") if key in pin}
        for role, pin in pins.items()})
    outputs = provision(runner, inputs, composition.workload_template, {"configuration": workload})["deployment"]
    network_snapshot(runner, inputs, foundation, "workloads")
    if catalogue_upgrade:
        print("Upgrading governed catalogue state...", flush=True)
        stage(runner, "signed catalogue upgrade")
        initial = initial_priors(runner, inputs, composition, foundation, images, catalogue_upgrade=True)
    environment_values = {**outputs,
                          "MEMBER_TOKEN_PUBLIC_JWK": json.dumps(public_key), "MEMBER_TOKEN_ISSUER": ISSUER,
                          "P2_CONTAINER_IMAGE": images["p2"]["image"], "DEV_FIXTURES_IMAGE": images["fixtures"]["image"],
                          "DEV_ISSUER_IMAGE": images["probe"]["image"],
                          "VOICE_DEMO_CLIENT_ID": registrations["voiceDemoClientId"],
                          "GOVERNEDGRAPH_ADMIN_CLIENT_ID": registrations["adminClientId"],
                          "GOVERNEDGRAPH_ADMIN_READER_GROUP_ID": registrations["adminReaderGroupId"],
                          "GOVERNEDGRAPH_ADMIN_OPERATOR_GROUP_ID": registrations["adminOperatorGroupId"],
                          "GOVERNEDGRAPH_ADMIN_OWNER_GROUP_ID": registrations["adminOwnerGroupId"],
                          "STRATEGY_RECOVERY_OPERATOR_ID": inputs.operator_id,
                          "CONTACT_CENTRE_CLIENT_ID": registrations["contactCentreApiClientId"],
                          "FAIRNESS_MANAGED_IDENTITY_CLIENT_ID": outputs["FAIRNESS_CLIENT_ID"],
                          "VOICE_DEMO_OPERATOR_ID": inputs.operator_id, "DEPLOY_VOICE_DEMO": str(inputs.voice_demo).lower()}
    for key, value in environment_values.items():
        runner.run(["azd", "env", "set", key, str(value), "--environment", inputs.environment])
    print("Checking deployment readiness...", flush=True)
    stage(runner, "readiness")
    readiness(runner, inputs, outputs)
    receipt = {"label": "ILLUSTRATIVE - synthetic data", "environment": inputs.environment,
               "subscription": inputs.subscription, "resource_group": inputs.resource_group, "naming_seed": inputs.naming_seed,
               "images": images, "agents": pins, "bootstrap": {"priors": initial, "executions": executions},
               "outputs": outputs, "readiness": "passed", "evaluation": "not_run",
               "fingerprint": redeploy.fingerprint(composition, inputs, root=ROOT)}
    evidence = getattr(runner, "evidence", None)
    if evidence is not None:
        receipt.update(run_id=evidence.run_id, evidence_path=str(evidence.path),
                       baseline=redeploy.baselines(evidence.data, composition))
    save_json(ROOT / ".run/delivery.json", receipt)
    return receipt


def readiness(runner, inputs, outputs, *, images: dict | None = None) -> None:
    apps = json.loads(runner.run(["az", "containerapp", "list", "-g", inputs.resource_group,
                                 "--subscription", inputs.subscription, "-o", "json"]))
    readiness_observation(runner, "apps", apps)
    required = set(redeploy.REQUIRED_APPS)
    ready = {app.get("tags", {}).get("azd-service-name") for app in apps
             if app["properties"]["provisioningState"] == "Succeeded" and app["properties"].get("runningStatus") == "Running"}
    if not required <= ready:
        raise DeliveryError("complete workloads are not ready: " + ", ".join(sorted(required - ready)))
    require_images(apps, images or {})
    jobs = json.loads(runner.run(["az", "containerapp", "job", "list", "-g", inputs.resource_group,
                                 "--subscription", inputs.subscription, "-o", "json"]))
    readiness_observation(runner, "jobs", jobs)
    scheduled = {job["name"] for job in jobs if job["properties"]["configuration"]["triggerType"] == "Schedule"}
    if not {"job-gg-p2-fairness", "job-gg-p2-pipeline"} <= scheduled:
        raise DeliveryError("native learning and fairness schedules are not enabled")
    admin_readiness(runner, outputs["ADMIN_CONSOLE_URL"], opener=urlopen)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", required=True, type=Path, help="explicit private delivery inputs JSON")
    parser.add_argument("--plan", action="store_true", help="local contract/sequence only; no cloud commands")
    parser.add_argument("--full", action="store_true", help=(
        "always run the full deployment; by default, when only application images changed, only they are rolled out"))
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--reuse", metavar="RESOURCE_GROUP", help="reuse a discovered GovernedGraph gg-dev deployment")
    target.add_argument("--new", action="store_true", help="generate and save a fresh seed and deployment target")
    args = parser.parse_args(argv)
    evidence = None
    try:
        raw = read_inputs(args.inputs)
        inputs = new_target(raw)
        composition = Composition.load(ROOT / inputs.composition)
        if args.plan:
            print(json.dumps({"environment": inputs.environment, "composition": composition.name, "sequence": SEQUENCE}))
            return 0
        evidence = Evidence(ROOT)
        evidence.data["selection"] = {"subscription": inputs.subscription, "reuse_requested": args.reuse is not None,
                                      "create_new": args.new}
        evidence.write()
        runner = Runner(evidence=evidence)
        inputs = select_target(runner, args.inputs, raw, inputs, reuse=args.reuse, create_new=args.new)
        receipt = None
        if not (args.full or args.new):
            from scripts.apps_only import redeploy_images

            receipt = redeploy_images(runner, inputs, composition)
        if not receipt:
            with paused(runner, inputs):
                receipt = deploy(runner, inputs, composition)
        evidence.finish("succeeded")
        demo_url = derived(receipt["outputs"])["PRESENTER_URL"] if inputs.voice_demo else "disabled"
        admin_url = receipt["outputs"]["ADMIN_CONSOLE_URL"]
        print(json.dumps({"readiness": receipt["readiness"], "evaluation": "not_run", "outputs": receipt["outputs"]}))
        print(f"Demo URL: {demo_url}")
        print(f"Admin URL: {admin_url}")
        return 0
    except KeyboardInterrupt:
        if evidence is not None:
            evidence.failure("interrupted")
        print("Delivery interrupted; local command stopped. Any submitted Azure operation may still be running; "
              "inspect its status before rerunning.", file=sys.stderr)
        return 130
    except (DeliveryError, EnvError, OSError, ValueError, TypeError, KeyError) as error:
        if evidence is not None:
            evidence.failure("failed", error)
        print(f"Delivery failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
