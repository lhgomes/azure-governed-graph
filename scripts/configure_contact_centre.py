"""Plan (default) or explicitly provision the dev contact-centre Entra API; no secrets."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from urllib.parse import quote
from uuid import UUID, NAMESPACE_URL, uuid5


def graph(method: str, path: str, body: dict | None = None) -> dict:
    command = ["az", "rest", "--method", method, "--url", f"https://graph.microsoft.com/v1.0/{path}"]
    if body is not None:
        command += ["--body", json.dumps(body), "--headers", "Content-Type=application/json"]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout) if result.stdout.strip() else {}


def configure(environment: str, runtime_principal: str, *, apply: bool = False, naming_seed: str = "") -> dict:
    UUID(runtime_principal)
    if environment != "gg-dev":
        raise ValueError("synthetic contact-centre registration is restricted to gg-dev")
    suffix = "-" + UUID(naming_seed).hex if naming_seed else ""
    name = f"GovernedGraph {environment} synthetic contact centre{suffix}"
    role_id = str(uuid5(NAMESPACE_URL, f"governedgraph:{environment}:ContactCentre.Access"))
    role = {"id": role_id, "allowedMemberTypes": ["Application"], "displayName": "Contact centre access",
            "description": "Runtime-only synthetic handover and read-only advisor queue.",
            "isEnabled": True, "value": "ContactCentre.Access"}
    if not apply:
        return {"data": "ILLUSTRATIVE - synthetic data", "apply": False, "display_name": name,
                "operations": ["create/reuse single-tenant API application and service principal",
                               "require assigned app role; access tokens v2 with idtyp",
                               "assign ContactCentre.Access to the runtime identity only"],
                "runtime_principal": runtime_principal, "role_id": role_id,
                "audience": "api://<new-application-client-id>", "credentials": "none"}
    apps = graph("GET", "applications?$filter=" + quote(f"displayName eq '{name}'"))["value"]
    if len(apps) > 1:
        raise ValueError("ambiguous existing contact-centre application")
    if apps:
        application = apps[0]
        if (application.get("passwordCredentials") or application.get("keyCredentials")
                or application.get("signInAudience") != "AzureADMyOrg"
                or any(any(item.get(field) != value for field, value in role.items())
                       for item in application.get("appRoles", []))):
            raise ValueError("existing application has unexpected credentials or tenancy")
    else:
        application = graph("POST", "applications", {"displayName": name, "signInAudience": "AzureADMyOrg"})
    app_id, object_id = application["appId"], application["id"]
    audience = f"api://{app_id}"
    graph("PATCH", f"applications/{object_id}", {
        "identifierUris": [audience], "api": {"requestedAccessTokenVersion": 2},
        "appRoles": [role],
        "optionalClaims": {"accessToken": [{"name": "idtyp", "source": None, "essential": False,
                                            "additionalProperties": []}]},
    })
    principals = graph("GET", "servicePrincipals?$filter=" + quote(f"appId eq '{app_id}'"))["value"]
    if len(principals) > 1:
        raise ValueError("ambiguous API service principal")
    principal = principals[0] if principals else graph("POST", "servicePrincipals", {"appId": app_id})
    resource_id = principal["id"]
    graph("PATCH", f"servicePrincipals/{resource_id}", {"appRoleAssignmentRequired": True})
    grants = graph("GET", f"servicePrincipals/{resource_id}/appRoleAssignedTo")["value"]
    if any(grant["principalId"] != runtime_principal or grant["appRoleId"] != role_id for grant in grants):
        raise ValueError("unexpected API role assignment; review instead of widening access")
    if not grants:
        graph("POST", f"servicePrincipals/{runtime_principal}/appRoleAssignments",
              {"principalId": runtime_principal, "resourceId": resource_id, "appRoleId": role_id})
    return {"data": "ILLUSTRATIVE - synthetic data", "apply": True, "client_id": app_id,
            "audience": audience, "runtime_principal": runtime_principal, "role_id": role_id,
            "service_principal_id": resource_id}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", default="gg-dev")
    parser.add_argument("--runtime-principal", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--naming-seed", default=os.getenv("GOVERNEDGRAPH_NAMING_SEED", ""))
    args = parser.parse_args()
    print(json.dumps(configure(args.environment, args.runtime_principal, apply=args.apply,
                               naming_seed=args.naming_seed), indent=2))
