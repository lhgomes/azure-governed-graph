"""Bounded bootstrap functions; no provisioning loop, learning cycle or human recovery."""
from __future__ import annotations

import base64
import json
import os
import re
import time

from scripts.delivery import AGENT_NAMES, AGENT_ROLES, DeliveryError, provision, public_result, run_job


def unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    fields = {}
    for key, value in pairs:
        if key in fields:
            raise DeliveryError("duplicate field in release approval")
        fields[key] = value
    return fields


def release_approval(composition) -> dict:
    approval = json.loads(composition.release_approval.read_text(), object_pairs_hook=unique_fields)
    if set(approval) != {"tier", "approved", "decision_ref", "evidence_ref"}:
        raise DeliveryError("human release approval has an invalid shape")
    if (approval["tier"] != "tier2" or approval["approved"] is not True
            or not isinstance(approval["decision_ref"], str)
            or not re.fullmatch(r"(?:GB|HR)-[A-Za-z0-9][A-Za-z0-9._-]{0,127}", approval["decision_ref"])
            or not isinstance(approval["evidence_ref"], str) or not approval["evidence_ref"].strip()
            or len(approval["evidence_ref"]) > 256):
        raise DeliveryError("release requires an explicit human Tier-2 decision and evidence")
    return approval


def agents(endpoint: str, *, subscription: str) -> dict:
    from scripts.create_agents import project_client, provision as create

    with project_client(endpoint, subscription=subscription) as project:
        results = {role: create(project, apply=True, role=role, agent_name=AGENT_NAMES[role]) for role in AGENT_ROLES}
    if any(result.get("agent_name") != AGENT_NAMES[role] for role, result in results.items()):
        raise DeliveryError("Foundry returned a different deployment agent name")
    if any(not re.fullmatch(r"[1-9][0-9]*", result.get("agent_version", ""))
           or not re.fullmatch(r"[a-f0-9]{64}", result.get("definition_sha256", "")) for result in results.values()):
        raise DeliveryError("Foundry returned a missing agent version or definition digest")
    return results


def persona_agent(endpoint: str, *, subscription: str) -> None:
    """Publish the demo persona's tool-free agent; the caller grants its own identity access to it."""
    from scripts.create_agents import project_client, provision as create

    with project_client(endpoint, subscription=subscription) as project:
        result = create(project, apply=True, role="persona")
    if not re.fullmatch(r"[1-9][0-9]*", result.get("agent_version", "")):
        raise DeliveryError("Foundry returned no published version for the persona agent")


def gap_review_agent(endpoint: str, *, subscription: str) -> None:
    """Publish the gap review's tool-free drafting agent; the job identity is granted that one agent only."""
    from scripts.create_agents import project_client, provision as create

    with project_client(endpoint, subscription=subscription) as project:
        result = create(project, apply=True, role="gap-review")
    if not re.fullmatch(r"[1-9][0-9]*", result.get("agent_version", "")):
        raise DeliveryError("Foundry returned no published version for the gap-review agent")


def publish_agents(inputs, endpoint: str) -> dict:
    """The counsellor-side agents, then the demo persona (when enabled) and the gap-review drafting agent."""
    published = agents(endpoint, subscription=inputs.subscription)
    if inputs.voice_demo:
        persona_agent(endpoint, subscription=inputs.subscription)
    gap_review_agent(endpoint, subscription=inputs.subscription)
    return published


def entra(inputs, foundation: dict) -> dict:
    if os.environ.get("AZURE_SUBSCRIPTION_ID") != inputs.subscription:
        raise DeliveryError("Entra bootstrap requires the selected delivery subscription in AZURE_SUBSCRIPTION_ID")
    from governedgraph.strategy_service.provision_auth import configure as strategy
    from scripts.configure_admin_console import configure as admin, signed_in_user
    from scripts.configure_contact_centre import configure as contact
    from scripts.configure_strategy_recovery import configure as recovery
    from scripts.configure_voice_demo import configure as voice

    security = foundation["security"]
    os.environ["STRATEGY_RECOVERY_OPERATOR_ID"] = inputs.operator_id
    client = strategy(inputs.environment, security["pipelinePrincipalId"], security["fairnessPrincipalId"],
                      security["runtimePrincipalId"], apply=True, naming_seed=inputs.naming_seed,
                      admin=security["adminPrincipalId"])
    if not client:
        raise DeliveryError("Strategy Entra registration returned no client")
    os.environ["STRATEGY_API_CLIENT_ID"] = client
    recovery(client, inputs.operator_id, apply=True)
    contact_app = contact(inputs.environment, security["runtimePrincipalId"], apply=True, naming_seed=inputs.naming_seed)
    console = admin(inputs.environment, foundation["gateway"]["gatewayUrl"], inputs.operator_id,
                    apply=True, naming_seed=inputs.naming_seed, owner_member=signed_in_user())
    presenter = voice(inputs.environment, security["runtimePrincipalId"], inputs.operator_id,
                      foundation["gateway"]["gatewayUrl"], deploy=inputs.voice_demo, apply=True,
                      naming_seed=inputs.naming_seed)
    return {"strategyApiClientId": client, "strategyRecoveryOperatorId": inputs.operator_id,
            "contactCentreApiClientId": contact_app["client_id"], "contactCentreAudience": contact_app["audience"],
            "adminClientId": console["client_id"], "adminReaderGroupId": console["reader_group_id"],
            "adminOperatorGroupId": console["operator_group_id"],
            "adminOwnerGroupId": console["owner_group_id"],
            "voiceDemoClientId": presenter.get("client_id", ""), "voiceDemoOperatorId": inputs.operator_id}


def register_identities(inputs, foundation: dict) -> dict:
    from governedgraph.strategy_service.provision_auth import GraphError

    try:
        return entra(inputs, foundation)
    except GraphError as error:
        guidance = ""
        if "AADSTS53003" in error.detail:
            guidance = (" Entra Conditional Access blocked this operation; it was not retried. "
                        "Have a tenant administrator review the corresponding Entra sign-in event, "
                        "then rerun from an approved sign-in context using the same resource group.")
        raise DeliveryError(f"Entra bootstrap failed: {error}.{guidance}") from None


def approve_search_link(runner, inputs, foundation: dict) -> None:
    storage = foundation["data"]["contentStorageId"]
    for _ in range(60):
        rows = json.loads(runner.run(["az", "network", "private-endpoint-connection", "list", "--id", storage,
                                     "--subscription", inputs.subscription, "-o", "json"]))
        own = [row for row in rows if "approved-content indexer" in
               row["properties"]["privateLinkServiceConnectionState"].get("description", "")]
        if own:
            for row in own:
                state = row["properties"]["privateLinkServiceConnectionState"]["status"]
                if state == "Pending":
                    runner.run(["az", "network", "private-endpoint-connection", "approve", "--id", row["id"],
                                "--subscription", inputs.subscription, "--description", "approved-content indexer"])
                elif state != "Approved":
                    raise DeliveryError("Search private link rejected/disconnected; no approval inferred")
            return
        time.sleep(10)
    raise DeliveryError("exact Search indexer private-link request unavailable")


def member_public_jwk(runner, inputs, job: str, execution: str) -> dict:
    for _ in range(12):
        logs = runner.run(["az", "containerapp", "job", "logs", "show", "--subscription", inputs.subscription,
                          "-g", inputs.resource_group, "-n", job, "--execution", execution, "--container", "issuer",
                          "--format", "text", "--tail", "50"])
        for candidate in re.findall(r"\{[^\n]+\}", logs):
            try:
                value = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if value.get("kty") != "RSA":
                continue
            if set(value) != {"kty", "alg", "use", "n", "e"} or value["alg"] != "RS256" or value["use"] != "sig":
                raise DeliveryError("issuer output is not a pure public RSA verification key")
            for field in ("n", "e"):
                if not re.fullmatch(r"[A-Za-z0-9_-]+", value[field]):
                    raise DeliveryError("invalid public key encoding")
                base64.urlsafe_b64decode(value[field] + "=" * (-len(value[field]) % 4))
            return value
        time.sleep(5)
    raise DeliveryError("issuer public verification key unavailable; no member token is requested")


def initial_priors(runner, inputs, composition, foundation: dict, images: dict, *, catalogue_upgrade: bool = False) -> dict:
    if catalogue_upgrade:
        from governedgraph.strategy_service.source_upgrade import load_upgrade

        upgrade = load_upgrade(composition.data_directory)
        if upgrade is None:
            raise DeliveryError("explicit catalogue upgrade requires a reviewed source upgrade plan")
        upgrade.validate_approval(json.loads(composition.release_approval.read_text()))
    security = foundation["security"]
    env = {**composition.domain_environment,
           "AZURE_KEY_VAULT_URL": security["keyVaultUri"], "SNAPSHOT_SIGNING_KEY_NAME": security["signingKeyName"],
           "RANKING_STORAGE_URL": foundation["data"]["contentBlobEndpoint"]}
    results = {}
    for role in ("sign", "initialize"):
        identity = "pipeline" if role == "sign" else "strategy"
        job_env = dict(env)
        if role == "sign" and catalogue_upgrade:
            job_env["GOVERNEDGRAPH_SOURCE_UPGRADE_APPROVAL"] = composition.release_approval.read_text()
        if role == "initialize":
            job_env.update({
                "APPLICATIONINSIGHTS_CONNECTION_STRING": foundation["monitoring"]["appInsightsRouting"],
                "AZURE_AI_PROJECT_RESOURCE_ID": foundation["ai"]["projectResourceId"],
                "GOVERNEDGRAPH_SERVICE_NAME": "gg-bootstrap-initialize",
                "GOVERNEDGRAPH_TRACE_SYNTHETIC_CONVERSATIONS": "false",
            })
        registry = foundation["compute"]["registryName"]
        name = "job-gg-bootstrap-" + role
        command_role = ({"sign": "sign-upgrade", "initialize": "upgrade"}[role]
                        if catalogue_upgrade else role)
        provision(runner, inputs, "infra/bootstrap-job.bicep", {
            "location": inputs.location, "jobName": name, "environmentId": foundation["compute"]["environmentId"],
            "image": images["p2" if role == "sign" else "runtime"]["image"],
            "identityId": security[identity + "IdentityId"], "identityClientId": security[identity + "ClientId"],
            "identityPrincipalId": security[identity + "PrincipalId"], "registryName": registry,
            "command": ["python", "-m", "governedgraph.strategy_service.bootstrap", command_role], "env": job_env,
        }, group=True)
        execution = run_job(runner, inputs, name)
        result = public_result(runner, inputs, name, execution)
        results[role] = {"execution_id": execution, **result}
        if role == "sign":
            env["GOVERNEDGRAPH_INITIAL_SNAPSHOT"] = json.dumps(result["snapshot"])
            if catalogue_upgrade:
                env["GOVERNEDGRAPH_SOURCE_UPGRADE_SIGNATURE"] = json.dumps(result["upgrade"])
    if not isinstance(results["initialize"].get("initialized"), bool) or not results["initialize"].get("snapshot_id"):
        raise DeliveryError("initial-prior job returned an invalid acknowledgement")
    return results

