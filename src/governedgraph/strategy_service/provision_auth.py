"""Explicit operator step for Graph app roles; default is a read-only plan."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from uuid import NAMESPACE_URL, UUID, uuid5
from typing import Any


ROLES = {name: str(uuid5(NAMESPACE_URL, "governedgraph/strategy/" + name))
         for name in ("Strategy.Publish", "Strategy.Suspend", "Strategy.Freeze")}


def az(*args: str) -> Any:
    result = subprocess.run(["az", *args, "-o", "json"], text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Azure CLI command failed")
    return json.loads(result.stdout) if result.stdout.strip() else None


def graph(method: str, path: str, body: dict | None = None):
    args = ["rest", "--method", method, "--url", "https://graph.microsoft.com/v1.0/" + path]
    if body is not None:
        args += ["--headers", "Content-Type=application/json", "--body", json.dumps(body)]
    return az(*args)


def role_assignments(pipeline: str, fairness: str, runtime: str, admin: str = "") -> set[tuple[str, str]]:
    principals = (pipeline, fairness, runtime, *([admin] if admin else []))
    for principal in principals:
        UUID(principal)
    if len(set(principals)) != len(principals):
        raise ValueError("runtime, P2, fairness and administration identities must be distinct")
    assignments = {
        (pipeline, ROLES["Strategy.Publish"]), (fairness, ROLES["Strategy.Suspend"]),
        (pipeline, ROLES["Strategy.Freeze"]), (fairness, ROLES["Strategy.Freeze"]),
        (runtime, ROLES["Strategy.Freeze"]),
    }
    if admin:
        # Protective actions only: the console can never publish a snapshot or reinstate a strategy.
        assignments |= {(admin, ROLES["Strategy.Suspend"]), (admin, ROLES["Strategy.Freeze"])}
    return assignments


def configure(environment: str, pipeline: str, fairness: str, runtime: str, *, apply: bool = False,
              naming_seed: str = "", admin: str = "") -> str | None:
    expected = role_assignments(pipeline, fairness, runtime, admin)
    suffix = "-" + UUID(naming_seed).hex if naming_seed else ""
    name = "gg-strategy-" + environment + suffix
    apps = az("ad", "app", "list", "--display-name", name)
    if len(apps) > 1:
        raise RuntimeError("multiple Strategy API registrations; select and reconcile explicitly")
    configuration = {
        "displayName": name,
        "signInAudience": "AzureADMyOrg",
        "api": {"requestedAccessTokenVersion": 2},
        "optionalClaims": {"accessToken": [
            {"name": "idtyp", "source": None, "essential": False, "additionalProperties": []},
        ]},
        "appRoles": [
            {"id": role_id, "value": role, "displayName": role,
             "description": role + " for the governed GovernedGraph Strategy Service",
             "allowedMemberTypes": ["Application"], "isEnabled": True}
            for role, role_id in ROLES.items()
        ],
    }
    print(json.dumps({"application": name, "roles": ROLES, "assignments": sorted(expected),
                      "human_reinstatement": "disabled", "apply": apply}))
    if not apply:
        return apps[0]["appId"] if apps else None
    if apps:
        app = apps[0]
        existing = {r["id"] for r in app.get("appRoles", [])}
        if existing - set(ROLES.values()):
            raise RuntimeError("unexpected pre-existing roles; review before changing the registration")
        configuration["api"] = {**app.get("api", {}), "requestedAccessTokenVersion": 2}
        graph("PATCH", f"applications/{app['id']}", configuration)
    else:
        app = graph("POST", "applications", configuration)
    app_id = app["appId"]
    graph("PATCH", f"applications/{app['id']}", {"identifierUris": ["api://" + app_id]})
    principals = az("ad", "sp", "list", "--filter", f"appId eq '{app_id}'")
    if len(principals) > 1:
        raise RuntimeError("multiple API service principals")
    resource = principals[0] if principals else graph("POST", "servicePrincipals", {"appId": app_id})
    graph("PATCH", f"servicePrincipals/{resource['id']}", {"appRoleAssignmentRequired": True})
    assigned = graph("GET", f"servicePrincipals/{resource['id']}/appRoleAssignedTo")
    if assigned.get("@odata.nextLink"):
        raise RuntimeError("unexpected assignment pagination; operator review required")
    current = {(row["principalId"], row["appRoleId"]) for row in assigned["value"]}
    operator = os.environ.get("STRATEGY_RECOVERY_OPERATOR_ID", "") if environment == "gg-dev" else ""
    if operator:
        UUID(operator)
    recovery_access = {(row["principalId"], row["appRoleId"]) for row in assigned["value"]
                       if operator and row["principalId"] == operator and row.get("principalType") == "User"
                       and row["appRoleId"] == "00000000-0000-0000-0000-000000000000"}
    if current - expected - recovery_access:
        raise RuntimeError("unexpected API app-role grants; explicitly review and revoke them")
    for principal, role in sorted(expected - current):
        graph("POST", f"servicePrincipals/{resource['id']}/appRoleAssignedTo",
              {"principalId": principal, "resourceId": resource["id"], "appRoleId": role})
    print("STRATEGY_API_CLIENT_ID=" + app_id)
    return app_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--pipeline", required=True)
    parser.add_argument("--fairness", required=True)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--admin", default="", help="Optional administration console identity (freeze and suspend only)")
    parser.add_argument("--naming-seed", default=os.getenv("GOVERNEDGRAPH_NAMING_SEED", ""))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    configure(args.environment, args.pipeline, args.fairness, args.runtime, apply=args.apply,
              naming_seed=args.naming_seed, admin=args.admin)


if __name__ == "__main__":
    main()
