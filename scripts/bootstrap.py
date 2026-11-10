"""Bounded bootstrap functions; no provisioning loop, learning cycle or human recovery."""
from __future__ import annotations

import base64
import json
import os
import re
import time

from scripts.delivery import AGENT_ROLES, DeliveryError, provision, public_result, run_job


def release_approval(composition, results: dict) -> dict:
    from scripts.writer_approval import unique_fields
    approval = json.loads(composition.release_approval.read_text(), object_pairs_hook=unique_fields)
    if set(approval) != {"tier", "approved", "decision_ref", "evidence_ref", "writers"}:
        raise DeliveryError("human release approval has an invalid shape")
    writers = {"gg-counsellor": "counsellor", "gg-counsellor-voice-compact": "voice-compact"}
    if set(approval["writers"]) != set(writers):
        raise DeliveryError("human release approval must cover both exact writer definitions")
    for name, role in writers.items():
        if approval["writers"][name] != {"definition_sha256": results[role]["definition_sha256"]}:
            raise DeliveryError("human release approval is stale; no generated writer is activated")
    if (approval["tier"] != "tier2" or approval["approved"] is not True
            or not isinstance(approval["decision_ref"], str)
            or not re.fullmatch(r"(?:GB|HR)-[A-Za-z0-9][A-Za-z0-9._-]{0,127}", approval["decision_ref"])
            or not isinstance(approval["evidence_ref"], str) or not approval["evidence_ref"].strip()
            or len(approval["evidence_ref"]) > 256):
        raise DeliveryError("release requires an explicit human Tier-2 decision and evidence")
    return approval


def approved_writers(composition, results: dict, endpoint: str) -> dict:
    from scripts.writer_approval import validate_record
    approval = release_approval(composition, results)
    writers = {"gg-counsellor": "counsellor", "gg-counsellor-voice-compact": "voice-compact"}
    record = {**approval, "project_endpoint": endpoint,
              "writers": {name: {"version": results[role]["agent_version"],
                                "definition_sha256": results[role]["definition_sha256"]} for name, role in writers.items()}}
    validate_record(record, {"AZURE_ENV_NAME": "gg-dev", "AZURE_AI_PROJECT_ENDPOINT": endpoint,
                            **{AGENT_ROLES[role]: result["agent_version"] for role, result in results.items()}})
    return record


def agents(composition, endpoint: str) -> tuple[dict, dict]:
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential
    from scripts.create_agents import provision as create
    from scripts.writer_approval import verify

    with DefaultAzureCredential() as credential, AIProjectClient(endpoint=endpoint, credential=credential) as project:
        results = {role: create(project, apply=True, role=role) for role in AGENT_ROLES}
        if any(not re.fullmatch(r"[1-9][0-9]*", result.get("agent_version", ""))
               or not re.fullmatch(r"[a-f0-9]{64}", result.get("definition_sha256", "")) for result in results.values()):
            raise DeliveryError("Foundry returned missing immutable numeric pins or definition digests")
        approval = approved_writers(composition, results, endpoint)
        verify(project, approval, {"AZURE_ENV_NAME": "gg-dev", "AZURE_AI_PROJECT_ENDPOINT": endpoint,
                                  **{AGENT_ROLES[role]: result["agent_version"] for role, result in results.items()}})
    return results, approval


def entra(inputs, foundation: dict) -> dict:
    from governedgraph.strategy_service.provision_auth import configure as strategy
    from scripts.configure_admin_console import configure as admin
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
                    apply=True, naming_seed=inputs.naming_seed)
    presenter = voice(inputs.environment, security["runtimePrincipalId"], inputs.operator_id,
                      foundation["gateway"]["gatewayUrl"], deploy=inputs.voice_demo, apply=True,
                      naming_seed=inputs.naming_seed)
    return {"strategyApiClientId": client, "strategyRecoveryOperatorId": inputs.operator_id,
            "contactCentreApiClientId": contact_app["client_id"], "contactCentreAudience": contact_app["audience"],
            "adminClientId": console["client_id"], "adminReaderGroupId": console["reader_group_id"],
            "adminOperatorGroupId": console["operator_group_id"],
            "voiceDemoClientId": presenter.get("client_id", ""), "voiceDemoOperatorId": inputs.operator_id}


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


def initial_priors(runner, inputs, composition, foundation: dict, images: dict) -> dict:
    security = foundation["security"]
    env = {**composition.domain_environment,
           "AZURE_KEY_VAULT_URL": security["keyVaultUri"], "SNAPSHOT_SIGNING_KEY_NAME": security["signingKeyName"],
           "RANKING_STORAGE_URL": foundation["data"]["contentBlobEndpoint"]}
    results = {}
    for role in ("sign", "initialize"):
        identity = "pipeline" if role == "sign" else "strategy"
        job_env = dict(env)
        if role == "initialize":
            job_env.update({
                "APPLICATIONINSIGHTS_CONNECTION_STRING": foundation["monitoring"]["appInsightsRouting"],
                "AZURE_AI_PROJECT_RESOURCE_ID": foundation["ai"]["projectResourceId"],
                "GOVERNEDGRAPH_SERVICE_NAME": "gg-bootstrap-initialize",
                "GOVERNEDGRAPH_TRACE_SYNTHETIC_CONVERSATIONS": "false",
            })
        registry = (foundation["learning"]["mlRegistryId"].rsplit("/", 1)[1] if role == "sign"
                    else foundation["compute"]["registryName"])
        name = "job-gg-bootstrap-" + role
        provision(runner, inputs, "infra/bootstrap-job.bicep", {
            "location": inputs.location, "jobName": name, "environmentId": foundation["compute"]["environmentId"],
            "image": images["p2" if role == "sign" else "runtime"]["image"],
            "identityId": security[identity + "IdentityId"], "identityClientId": security[identity + "ClientId"],
            "identityPrincipalId": security[identity + "PrincipalId"], "registryName": registry,
            "command": ["python", "-m", "governedgraph.strategy_service.bootstrap", role], "env": job_env,
        }, group=True)
        execution = run_job(runner, inputs, name)
        result = public_result(runner, inputs, name, execution)
        results[role] = {"execution_id": execution, **result}
        if role == "sign":
            env["GOVERNEDGRAPH_INITIAL_SNAPSHOT"] = json.dumps(result["snapshot"])
    if not isinstance(results["initialize"].get("initialized"), bool) or not results["initialize"].get("snapshot_id"):
        raise DeliveryError("initial-prior job returned an invalid acknowledgement")
    return results


def ml_containers(inputs, foundation: dict) -> list[str]:
    from azure.ai.ml import MLClient
    from azure.ai.ml.entities import AzureBlobDatastore
    from azure.identity import DefaultAzureCredential

    account = foundation["learning"]["mlStorageId"].rsplit("/", 1)[1]
    with DefaultAzureCredential() as credential:
        client = MLClient(credential, inputs.subscription, inputs.resource_group, foundation["learning"]["mlWorkspaceName"])
        stores = list(client.datastores.list(include_secrets=False))
    containers = sorted({store.container_name for store in stores
                         if isinstance(store, AzureBlobDatastore) and store.account_name == account})
    if not containers or any(not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", name) for name in containers):
        raise DeliveryError("ML-owned datastore containers unavailable; no guessed containers or broader grants")
    return containers
