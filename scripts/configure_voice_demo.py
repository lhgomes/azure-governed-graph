"""Plan/apply the opt-in gg-dev presenter registration. No credentials are created."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from urllib.parse import quote, urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

from scripts.configure_contact_centre import graph

CLI_CLIENT = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"


def configure(environment: str, runtime: str, operator: str, origin: str, *,
              deploy: bool = False, apply: bool = False, naming_seed: str = "") -> dict:
    suffix = "-" + UUID(naming_seed).hex if naming_seed else ""
    name = "GovernedGraph gg-dev Voice Demo" + suffix
    if not deploy:
        if apply and environment == "gg-dev":
            apps = graph("GET", "applications?$filter=" + quote(f"displayName eq '{name}'"))["value"]
            if len(apps) > 1:
                raise ValueError("ambiguous presenter registration")
            if apps:
                principals = graph("GET", "servicePrincipals?$filter=" + quote(f"appId eq '{apps[0]['appId']}'"))["value"]
                for principal in principals:
                    graph("PATCH", f"servicePrincipals/{principal['id']}", {"accountEnabled": False})
        return {"enabled": False, "operations": []}
    if environment != "gg-dev":
        raise ValueError("presenter deployment is restricted to gg-dev")
    UUID(runtime)
    UUID(operator)
    url = urlsplit(origin)
    if url.scheme != "https" or not url.hostname or url.netloc != url.hostname or url.path or url.query or url.fragment:
        raise ValueError("an exact HTTPS demo origin is required")
    scope_id, presenter_id, issuer_id = (
        str(uuid5(NAMESPACE_URL, f"governedgraph:gg-dev:{value}"))
        for value in ("VoiceDemo.Access", "VoiceDemo.Presenter", "VoiceDemo.Issue")
    )
    if not apply:
        return {"enabled": True, "apply": False, "origin": origin, "operator": operator,
                "runtime": runtime, "credentials": "none",
                "operations": ["dedicated single-tenant SPA/API, PKCE, delegated VoiceDemo.Access",
                               "assignment required; only operator assigned VoiceDemo.Presenter",
                               "only runtime assigned application VoiceDemo.Issue",
                               "preauthorize SPA and Azure CLI; user assignment still required"]}
    apps = graph("GET", "applications?$filter=" + quote(f"displayName eq '{name}'"))["value"]
    if len(apps) > 1:
        raise ValueError("ambiguous presenter registration")
    app = apps[0] if apps else graph("POST", "applications", {
        "displayName": name, "signInAudience": "AzureADMyOrg",
    })
    if (app.get("passwordCredentials") or app.get("keyCredentials")
            or app.get("signInAudience") != "AzureADMyOrg"
            or any(role["id"] not in {presenter_id, issuer_id} for role in app.get("appRoles", []))):
        raise ValueError("unexpected presenter credentials, tenancy or roles")
    client_id = app["appId"]
    roles = [
        {"id": presenter_id, "allowedMemberTypes": ["User"], "displayName": "Dev presenter",
         "description": "Approved synthetic demo operator only.", "isEnabled": True, "value": "VoiceDemo.Presenter"},
        {"id": issuer_id, "allowedMemberTypes": ["Application"], "displayName": "Synthetic issuer caller",
         "description": "Runtime bridge only; fixed synthetic member.", "isEnabled": True, "value": "VoiceDemo.Issue"},
    ]
    graph("PATCH", f"applications/{app['id']}", {
        "identifierUris": [f"api://{client_id}"], "signInAudience": "AzureADMyOrg",
        "spa": {"redirectUris": [origin + "/demo/"]},
        "api": {"requestedAccessTokenVersion": 2, "oauth2PermissionScopes": [{
            "id": scope_id, "value": "VoiceDemo.Access", "isEnabled": True, "type": "User",
            "adminConsentDisplayName": "Use the synthetic voice demo",
            "adminConsentDescription": "Use only the gg-dev synthetic presenter channel.",
            "userConsentDisplayName": "Use the synthetic voice demo",
            "userConsentDescription": "Use only the gg-dev synthetic presenter channel.",
        }]},
        "appRoles": roles,
        "optionalClaims": {"accessToken": [{"name": "idtyp", "essential": False, "additionalProperties": []}]},
    })
    graph("PATCH", f"applications/{app['id']}", {"api": {"preAuthorizedApplications": [
        {"appId": value, "delegatedPermissionIds": [scope_id]} for value in (client_id, CLI_CLIENT)
    ]}})
    principals = graph("GET", "servicePrincipals?$filter=" + quote(f"appId eq '{client_id}'"))["value"]
    if len(principals) > 1:
        raise ValueError("ambiguous presenter service principal")
    principal = principals[0] if principals else graph("POST", "servicePrincipals", {"appId": client_id})
    resource = principal["id"]
    graph("PATCH", f"servicePrincipals/{resource}", {"appRoleAssignmentRequired": True, "accountEnabled": True})
    expected = {(operator, presenter_id), (runtime, issuer_id)}
    grants = graph("GET", f"servicePrincipals/{resource}/appRoleAssignedTo")["value"]
    actual = {(row["principalId"], row["appRoleId"]) for row in grants}
    if actual - expected:
        raise ValueError("unexpected presenter assignment; do not widen or delete it")
    for who, role in expected - actual:
        graph("POST", f"servicePrincipals/{resource}/appRoleAssignedTo",
              {"principalId": who, "resourceId": resource, "appRoleId": role})
    return {"enabled": True, "apply": True, "client_id": client_id, "principal_id": resource,
            "operator": operator, "runtime": runtime, "origin": origin}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", default=os.getenv("AZURE_ENV_NAME", "gg-dev"))
    parser.add_argument("--runtime", default=os.getenv("STRATEGY_RUNTIME_PRINCIPAL_ID", ""))
    parser.add_argument("--operator", default=os.getenv("VOICE_DEMO_OPERATOR_ID", ""))
    parser.add_argument("--origin", default=os.getenv("APIM_GATEWAY_URL", "").rstrip("/"))
    parser.add_argument("--deploy-voice-demo", action=argparse.BooleanOptionalAction,
                        default=os.getenv("DEPLOY_VOICE_DEMO") == "true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--azd-env", action="store_true")
    parser.add_argument("--naming-seed", default=os.getenv("GOVERNEDGRAPH_NAMING_SEED", ""))
    args = parser.parse_args()
    try:
        result = configure(args.environment, args.runtime, args.operator, args.origin,
                           deploy=args.deploy_voice_demo, apply=args.apply, naming_seed=args.naming_seed)
        if args.azd_env and args.apply and result["enabled"]:
            subprocess.run(["azd", "env", "set", "VOICE_DEMO_CLIENT_ID", result["client_id"],
                            "--environment", args.environment], check=True)
        print(json.dumps(result, indent=2))
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.stderr) from None
