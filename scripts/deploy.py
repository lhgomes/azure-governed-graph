"""Deliver an explicit gg-dev composition; deployment readiness is not evaluation or acceptance."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
import sys
from pathlib import Path
from urllib.request import urlopen

from scripts.delivery import AGENT_NAMES, AGENT_ROLES, ROOT, Composition, DeliveryError, Runner, build, provision, run_job, save_json
from scripts.azd_env import EnvError, derived
from scripts.deployment_target import new_target, read_inputs, same_scope, select_target
from scripts.delivery_evidence import Evidence, admin_readiness, network_snapshot, readiness_observation, stage

ISSUER = "https://gg-dev.synthetic.invalid"
SEQUENCE = ("foundation", "immutable images", "bounded bootstrap", "complete workloads", "readiness")


def image_artifacts(runner, foundation: dict, composition, *, subscription: str) -> dict:
    registry = foundation["compute"]["registryName"]
    core = [ROOT / "src", ROOT / "requirements.txt"]
    framework = {
        "runtime": build(runner, registry, "runtime", "Dockerfile", core + [ROOT / "requirements-azure.txt"], {},
                         subscription=subscription),
        "p2": build(runner, registry, "p2", "Dockerfile.p2", core + [ROOT / "requirements-p2.txt"], {},
                    subscription=subscription),
    }
    args = {"FRAMEWORK_RUNTIME": framework["runtime"]["image"]}
    p2_args = {"FRAMEWORK_P2": framework["p2"]["image"]}
    data = composition.data_directory
    domain = [data / "catalogue", data / "content", data / "fairness/thresholds_v1.json",
              data.parent / "__init__.py"]
    images = {
        "framework-runtime": framework["runtime"], "framework-p2": framework["p2"],
        "runtime": build(runner, registry, "runtime", composition.dockerfile,
                         domain + [data / "synthetic/members.json", data.parent / "member_values_api",
                                   data / "personas/personas.json", data.parent / "demo/build_simulations.py"], args,
                         subscription=subscription),
        "p2": build(runner, registry, "p2", composition.p2_dockerfile, domain, p2_args,
                    subscription=subscription),
        "fixtures": build(runner, registry, "fixtures", composition.p2_dockerfile, domain + [
            data / "synthetic/outcome_aggregates_illustrative.csv", data / "synthetic/uplift_window_illustrative.json",
            data / "fairness/cohort_aggregates_illustrative.csv"], p2_args, subscription=subscription),
        "probe": build(runner, registry, "probe", composition.dockerfile, domain + [
            data / "synthetic/members.json", data.parent / "member_values_api", data / "personas/personas.json",
            data.parent / "demo/build_simulations.py",             data.parent / "evaluation/datasets"], args, subscription=subscription),
    }
    return images


def environment(runner, inputs, composition) -> None:
    directory = ROOT / ".azure" / inputs.environment
    if not directory.exists():
        runner.run(["azd", "env", "new", inputs.environment, "--subscription", inputs.subscription,
                    "--location", inputs.location, "--no-prompt"])
    else:
        from scripts.azd_env import load

        env_file = directory / ".env"
        if not env_file.is_file() or not same_scope(load(env_file=str(env_file)), inputs):
            raise DeliveryError("saved azd environment scope differs; select the deployment target again "
                                "with --reuse RESOURCE_GROUP or --new")
    values = {
        "AZURE_ENV_NAME": inputs.environment, "AZURE_SUBSCRIPTION_ID": inputs.subscription,
        "AZURE_LOCATION": inputs.location, "AZURE_SEARCH_LOCATION": inputs.search_location,
        "AZURE_RESOURCE_GROUP": inputs.resource_group, "GOVERNEDGRAPH_NAMING_SEED": inputs.naming_seed,
        "AZURE_PRINCIPAL_ID": inputs.operator_id, "GOVERNEDGRAPH_ON_CALL_EMAIL": inputs.on_call_email,
        "GOVERNEDGRAPH_SAMPLE": composition.name, "GOVERNEDGRAPH_DATA_DIR": str(composition.data_directory),
    }
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
    from scripts.bootstrap import agents, approve_search_link, gap_review_agent, persona_agent, release_approval, entra, initial_priors, member_public_jwk
    from scripts.create_agents import provision as agent_plan

    plans = {role: agent_plan(None, apply=False, role=role, agent_name=AGENT_NAMES[role]) for role in AGENT_ROLES}
    approval = release_approval(composition, plans)
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
    foundation = provision(runner, inputs, "infra/main.bicep", base)["foundation"]
    network_snapshot(runner, inputs, foundation, "foundation")
    print("Preparing immutable images...", flush=True)
    stage(runner, "immutable images")
    images = image_artifacts(runner, foundation, composition, subscription=inputs.subscription)
    print("Running bounded bootstrap...", flush=True)
    stage(runner, "bounded bootstrap", images=images)
    os.environ["AZURE_AI_PROJECT_ENDPOINT"] = foundation["ai"]["projectEndpoint"]
    try:
        registrations = entra(inputs, foundation)
    except GraphError as error:
        guidance = ""
        if "AADSTS53003" in error.detail:
            guidance = (" Entra Conditional Access blocked this operation; it was not retried. "
                        "Have a tenant administrator review the corresponding Entra sign-in event, "
                        "then rerun from an approved sign-in context using the same resource group.")
        raise DeliveryError(f"Entra bootstrap failed: {error}.{guidance}") from None
    pins, approval = agents(composition, foundation["ai"]["projectEndpoint"], subscription=inputs.subscription)
    persona_version = (persona_agent(foundation["ai"]["projectEndpoint"], subscription=inputs.subscription)
                       if inputs.voice_demo else "")
    approve_search_link(runner, inputs, foundation)
    jobs = provision(runner, inputs, "infra/bootstrap.bicep", {
        "resourceGroupName": inputs.resource_group, "location": inputs.location, "foundation": foundation,
        "runtimeImage": images["runtime"]["image"], "probeImage": images["probe"]["image"],
        "fixturesImage": images["fixtures"]["image"], "domainEnv": composition.domain_environment,
        "memberTokenIssuer": ISSUER, "deployDevIssuer": True,
    })
    public_key = member_public_jwk(runner, inputs, jobs["issuerJob"], run_job(runner, inputs, jobs["issuerJob"]))
    initial = None
    if not catalogue_upgrade:
        initial = initial_priors(runner, inputs, composition, foundation, images)
    executions = {"content": run_job(runner, inputs, jobs["contentJob"])}
    executions["fixtures"] = run_job(runner, inputs, jobs["fixturesJob"])
    workload = {
        **base, **registrations, "foundation": foundation, "domainEnv": composition.domain_environment,
        "memberValuesModule": composition.member_values_module,
        "orchestratorImage": images["runtime"]["image"], "strategyServiceImage": images["runtime"]["image"],
        "memberValuesImage": images["runtime"]["image"], "contactCentreImage": images["runtime"]["image"],
        "adminImage": images["runtime"]["image"], "p2ContainerImage": images["p2"]["image"],
        "counsellorAgentVersion": pins["counsellor"]["agent_version"],
        "voiceCompactAgentVersion": pins["voice-compact"]["agent_version"],
        "writerApproval": approval,
        "personaAgentVersion": persona_version,
        "gapReviewAgentVersion": gap_review_agent(foundation["ai"]["projectEndpoint"], subscription=inputs.subscription),
        "memberTokenIssuer": ISSUER, "memberTokenPublicJwk": public_key, "onCallEmail": inputs.on_call_email,
        "deployVoiceDemo": inputs.voice_demo, "traceSyntheticConversations": inputs.voice_demo,
        "adminTraceLookup": inputs.voice_demo,
    }
    print("Deploying complete workloads...", flush=True)
    stage(runner, "complete workloads", agent_pins={
        role: {key: pin[key] for key in ("agent_name", "agent_version", "definition_sha256") if key in pin}
        for role, pin in pins.items()})
    outputs = provision(runner, inputs, composition.workload_template, {"configuration": workload})["deployment"]
    network_snapshot(runner, inputs, foundation, "workloads")
    if catalogue_upgrade:
        print("Upgrading governed catalogue state...", flush=True)
        stage(runner, "signed catalogue upgrade")
        initial = initial_priors(runner, inputs, composition, foundation, images, catalogue_upgrade=True)
    environment_values = {**outputs, **{AGENT_ROLES[role]: pin["agent_version"] for role, pin in pins.items()},
                          **({"AZURE_AI_PERSONA_VERSION": persona_version} if persona_version else {}),
                          "AZURE_AI_GAP_REVIEW_VERSION": workload["gapReviewAgentVersion"],
                          "GOVERNEDGRAPH_WRITER_APPROVAL_JSON": json.dumps(approval),
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
               "images": images, "agent_pins": pins, "bootstrap": {"priors": initial, "executions": executions},
               "outputs": outputs, "readiness": "passed", "evaluation": "not_run"}
    evidence = getattr(runner, "evidence", None)
    if evidence is not None:
        receipt.update(run_id=evidence.run_id, evidence_path=str(evidence.path))
    save_json(ROOT / ".run/delivery.json", receipt)
    return receipt


def readiness(runner, inputs, outputs) -> None:
    apps = json.loads(runner.run(["az", "containerapp", "list", "-g", inputs.resource_group,
                                 "--subscription", inputs.subscription, "-o", "json"]))
    readiness_observation(runner, "apps", apps)
    required = {"orchestrator", "strategy-service", "member-values", "contact-centre", "admin"}
    ready = {app.get("tags", {}).get("azd-service-name") for app in apps
             if app["properties"]["provisioningState"] == "Succeeded" and app["properties"].get("runningStatus") == "Running"}
    if not required <= ready:
        raise DeliveryError("complete workloads are not ready: " + ", ".join(sorted(required - ready)))
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
