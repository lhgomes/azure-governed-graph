"""Plan or explicitly configure delegated human dev recovery on the existing Strategy API."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from urllib.parse import quote
from uuid import UUID, NAMESPACE_URL, uuid5

from configure_contact_centre import graph

AZURE_CLI_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"  # Microsoft's public Azure CLI application ID
CLI_ID = AZURE_CLI_CLIENT_ID
DEFAULT_ROLE = "00000000-0000-0000-0000-000000000000"


def configure(api_client_id: str, human_id: str, *, apply: bool = False, environ=os.environ) -> dict:
    UUID(api_client_id)
    UUID(human_id)
    environment = environ.get("AZURE_ENV_NAME", "")
    if environment != "gg-dev":
        raise ValueError("delegated recovery can only be configured for the gg-dev environment")
    if (not environ.get("STRATEGY_API_CLIENT_ID") or not environ.get("STRATEGY_RECOVERY_OPERATOR_ID")
            or api_client_id != environ["STRATEGY_API_CLIENT_ID"]
            or human_id != environ["STRATEGY_RECOVERY_OPERATOR_ID"]):
        raise ValueError("this approval is restricted to the deployment's Strategy API and its configured recovery operator "
                         "(STRATEGY_API_CLIENT_ID and STRATEGY_RECOVERY_OPERATOR_ID from the azd environment)")
    scope_id = str(uuid5(NAMESPACE_URL, f"governedgraph:{environment}:{api_client_id}:Strategy.Recover"))
    plan = {"environment": environment, "api_client_id": api_client_id, "human_id": human_id,
            "scope": "Strategy.Recover", "scope_id": scope_id, "client_id": CLI_ID,
            "operations": ["add delegated-only recovery scope", "preauthorize Azure CLI for that scope only",
                           "assign default enterprise application access to approved human only"],
            "new_application_roles": False, "credentials": "none", "apply": apply}
    if not apply:
        return plan
    apps = graph("GET", "applications?$filter=" + quote(f"appId eq '{api_client_id}'"))["value"]
    principals = graph("GET", "servicePrincipals?$filter=" + quote(f"appId eq '{api_client_id}'"))["value"]
    if len(apps) != 1 or len(principals) != 1:
        raise ValueError("existing Strategy API registration is ambiguous or missing")
    application, principal = apps[0], principals[0]
    if (application["signInAudience"] != "AzureADMyOrg" or not principal["appRoleAssignmentRequired"]
            or application.get("passwordCredentials") or application.get("keyCredentials")):
        raise ValueError("existing API security configuration must not be weakened")
    api = dict(application["api"])
    scopes = list(api.get("oauth2PermissionScopes", []))
    scope = {"id": scope_id, "value": "Strategy.Recover", "type": "Admin", "isEnabled": True,
             "adminConsentDisplayName": "Human recovery of the gg-dev watchdog proof freeze",
             "adminConsentDescription": "Delegated human only; backend additionally requires the approved human object ID.",
             "userConsentDisplayName": "Human dev recovery", "userConsentDescription": "Approved dev recovery only."}
    existing = [item for item in scopes if item["id"] == scope_id or item["value"] == scope["value"]]
    if existing and existing != [scope]:
        raise ValueError("existing recovery scope conflicts with the reviewed scope")
    if not existing:
        scopes.append(scope)
        graph("PATCH", f"applications/{application['id']}", {"api": {**api, "oauth2PermissionScopes": scopes}})
        confirmed = graph("GET", f"applications/{application['id']}")
        if scope not in confirmed["api"]["oauth2PermissionScopes"]:
            raise ValueError("new delegated recovery scope was not persisted")
    clients = list(api.get("preAuthorizedApplications", []))
    cli = [item for item in clients if item["appId"] == CLI_ID]
    if len(cli) > 1:
        raise ValueError("ambiguous preauthorized CLI")
    if cli:
        cli[0]["delegatedPermissionIds"] = sorted(set(cli[0]["delegatedPermissionIds"]) | {scope_id})
    else:
        clients.append({"appId": CLI_ID, "delegatedPermissionIds": [scope_id]})
    graph("PATCH", f"applications/{application['id']}", {
        "api": {**api, "oauth2PermissionScopes": scopes, "preAuthorizedApplications": clients},
    })
    grants = graph("GET", f"users/{human_id}/appRoleAssignments")["value"]
    if not any(item["resourceId"] == principal["id"] for item in grants):
        graph("POST", f"servicePrincipals/{principal['id']}/appRoleAssignedTo", {
            "principalId": human_id, "resourceId": principal["id"], "appRoleId": DEFAULT_ROLE,
        })
    return plan


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-client-id", required=True)
    parser.add_argument("--human-id", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        result = configure(args.api_client_id, args.human_id, apply=args.apply)
    except subprocess.CalledProcessError as error:
        print(error.stderr, file=sys.stderr)
        raise SystemExit(error.returncode) from error
    print(json.dumps(result, indent=2))
