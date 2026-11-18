"""Provision a governed prompt agent once; default is a read-only plan (IMP-11)."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from uuid import UUID

from governedgraph.agents.foundry_runtime import agent_definition
from governedgraph.agents.counsellor import INSTRUCTIONS
from governedgraph.common.config import settings
from scripts.delivery import AGENT_NAMES, DeliveryError, Runner

if TYPE_CHECKING:
    from azure.ai.projects import AIProjectClient
    from azure.core.credentials import AccessToken, TokenCredential
    from azure.core.pipeline.transport import RequestsTransport


def _project_transport() -> RequestsTransport:
    import ssl

    from azure.core.pipeline.transport import RequestsTransport
    from requests import PreparedRequest, Session
    from requests.adapters import HTTPAdapter

    # This delivery path rejects OpenSSL 3.5's hybrid key-share offer.
    # P-256 retains TLS 1.3; Requests still supplies the selected CA bundle.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.set_ecdh_curve("prime256v1")

    class VerifiedAdapter(HTTPAdapter):
        def build_connection_pool_key_attributes(
            self, request: PreparedRequest, verify: bool | str | None,
            cert: tuple[str, str] | str | None = None,
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            if verify is False:
                raise DeliveryError("Foundry delivery requires certificate verification")
            host, pool = super().build_connection_pool_key_attributes(request, verify, cert)
            pool["ssl_context"] = context
            return host, pool

    session = Session()
    session.mount("https://", VerifiedAdapter())
    return RequestsTransport(session=session, session_owner=True)


class _TenantCredential:
    def __init__(self, credential: TokenCredential, tenant_id: str):
        self.credential = credential
        self.tenant_id = tenant_id

    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        if kwargs.get("tenant_id") not in (None, self.tenant_id):
            raise DeliveryError("Foundry authentication cannot leave the selected deployment tenant")
        kwargs["tenant_id"] = self.tenant_id
        return self.credential.get_token(*scopes, **kwargs)


@contextmanager
def project_client(endpoint: str, *, subscription: str) -> Generator[AIProjectClient]:
    from azure.ai.projects import AIProjectClient
    from azure.core.exceptions import ServiceRequestError, ServiceResponseError
    from azure.identity import DefaultAzureCredential

    if not subscription:
        raise DeliveryError("Foundry authentication requires the selected AZURE_SUBSCRIPTION_ID")
    tenant_id = Runner().run(["az", "account", "show", "--subscription", subscription,
                              "--query", "tenantId", "-o", "tsv"])
    try:
        UUID(tenant_id)
    except ValueError as error:
        raise DeliveryError("selected subscription returned an invalid Foundry tenant") from error
    try:
        with DefaultAzureCredential(process_timeout=120) as credential, _project_transport() as transport:
            with AIProjectClient(endpoint=endpoint, credential=_TenantCredential(credential, tenant_id),
                                 transport=transport) as project:
                yield project
    except (ServiceRequestError, ServiceResponseError) as error:
        raise DeliveryError(
            f"Foundry transport failed for {endpoint} ({type(error).__name__}): {error}"
        ) from error


def definition_sha256(definition: dict[str, object]) -> str:
    return hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()


def pin_fairness_endpoint(project, version: str, *, agent_name: str | None = None) -> None:
    from azure.ai.projects.models import AgentEndpointConfig
    from governedgraph.agents.fairness_runtime import endpoint_selector

    selector = endpoint_selector(version)
    expected = selector.as_dict()
    name = settings.fairness_agent_name if agent_name is None else agent_name
    current = project.agents.get(name).as_dict()
    if current["agent_endpoint"]["version_selector"] == expected:
        return
    updated = project.agents.update_details(
        agent_name=name, agent_endpoint=AgentEndpointConfig(version_selector=selector),
    ).as_dict()
    if updated["agent_endpoint"]["version_selector"] != expected:
        raise RuntimeError("fairness endpoint did not acknowledge the approved immutable version")


def provision(project, *, apply: bool, role: str = "counsellor", agent_name: str | None = None) -> dict[str, str]:
    model_deployment = settings.model_deployment if role == "counsellor" else settings.compact_deployment
    if role == "fairness":
        from governedgraph.agents.fairness_runtime import agent_definition as fairness_definition

        definition = fairness_definition()
        name, decision = settings.fairness_agent_name, "IMP-34"
    elif role in ("intent", "understanding", "answer-review"):
        from governedgraph.agents.decision_agents import agent_definition as decision_definition, deployment_for

        definition = decision_definition(role)
        model_deployment = deployment_for(role)
        name, decision = AGENT_NAMES[role], "IMP-57"
    elif role == "persona":
        from governedgraph.agents.live_persona import (
            LIVE_PERSONA_AGENT_NAME, live_persona_definition, persona_deployment,
        )

        model_deployment = persona_deployment()
        definition = live_persona_definition(model_deployment)
        name, decision = LIVE_PERSONA_AGENT_NAME, "IMP-persona-demo"
    elif role == "evaluation":
        model_deployment = "gpt-modern"
        definition = agent_definition(INSTRUCTIONS, model=model_deployment, reasoning_effort="none")
        name, decision = "gg-counsellor-modern", "IMP-69"
    elif role in ("counsellor", "voice-compact"):
        compact = role == "voice-compact"
        definition = (agent_definition(INSTRUCTIONS, model=settings.compact_deployment) if compact
                      else agent_definition(INSTRUCTIONS))
        name = AGENT_NAMES[role] if compact else settings.agent_name
        decision = "IMP-57"
    else:
        raise ValueError("unknown governed agent role")
    if agent_name is not None:
        name = agent_name
    if not name:
        raise DeliveryError("agent provisioning requires an explicit nonempty agent name")
    digest = definition_sha256(definition.as_dict())
    result = {"agent_name": name,
              "model_deployment": model_deployment,
              "definition_sha256": digest, "decision": decision}
    if not apply:
        return {**result, "action": "plan only; no Azure changes"}
    from azure.core.exceptions import ResourceNotFoundError

    try:
        versions = list(project.agents.list_versions(name))
    except ResourceNotFoundError:
        logging.getLogger("governedgraph.agent_provision").info("approved_agent_does_not_exist")
        versions = []
    matching = [v for v in versions if (v.metadata or {}).get("definition_sha256") == digest
                and v.definition.as_dict() == definition.as_dict()]
    if matching:
        version = matching[-1]
        action = "reuse immutable matching version"
    else:
        version = project.agents.create_version(
            agent_name=name, definition=definition,
            metadata={"definition_sha256": digest, "decision": decision, "data": "ILLUSTRATIVE - synthetic data"},
        )
        action = "created; pin this version before deployment"
    if role == "fairness":
        pin_fairness_endpoint(project, version.version, agent_name=name)
        result["endpoint_version"] = version.version
    return {**result, "agent_version": version.version, "action": action}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--role", choices=("counsellor", "fairness", "voice-compact", "intent", "understanding",
                                        "answer-review", "evaluation", "persona"),
                        default="counsellor")
    args = parser.parse_args()
    if args.apply:
        with project_client(settings.project_endpoint,
                            subscription=os.environ.get("AZURE_SUBSCRIPTION_ID", "")) as project:
            result = provision(project, apply=True, role=args.role)
    else:
        result = provision(None, apply=False, role=args.role)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
