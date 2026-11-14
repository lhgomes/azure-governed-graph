"""Plan (default) or apply the gg-dev admin console sign-in and its reader, operator and owner groups.

Mirrors the voice presenter registration in bounded delivery bootstrap. It creates separate access groups and registers a
single-page app (PKCE, no secret, no API permissions) and assigns both groups so they appear in sign-in tokens.
Supplied groups are used as supplied and their membership is left untouched. Managed reader/operator groups contain
the configured operator; the managed owner group contains the signed-in user performing the deployment.
Set GOVERNEDGRAPH_ADMIN_ACCESS_CONTROL=false to skip all of this and leave the console open (banner shown).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from urllib.parse import quote, urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

from scripts.configure_contact_centre import graph
from governedgraph.strategy_service.provision_auth import GraphError

DEFAULT_ROLE = "00000000-0000-0000-0000-000000000000"
SCOPE = "Admin.Access"
GROUP_CLAIMS = {"ApplicationGroup", "SecurityGroup"}
ENV_KEYS = {
    "client": "GOVERNEDGRAPH_ADMIN_CLIENT_ID",
    "reader": "GOVERNEDGRAPH_ADMIN_READER_GROUP_ID",
    "operator": "GOVERNEDGRAPH_ADMIN_OPERATOR_GROUP_ID",
    "owner": "GOVERNEDGRAPH_ADMIN_OWNER_GROUP_ID",
}


def names(environment: str, naming_seed: str) -> dict[str, str]:
    suffix = "-" + UUID(naming_seed).hex if naming_seed else ""
    return {"app": f"GovernedGraph {environment} admin console{suffix}",
            "reader": f"GovernedGraph {environment} admin readers{suffix}",
            "operator": f"GovernedGraph {environment} admin operators{suffix}",
            "owner": f"GovernedGraph {environment} platform owners{suffix}"}


def validate_origin(origin: str) -> None:
    url = urlsplit(origin)
    if url.scheme != "https" or not url.hostname or url.netloc != url.hostname or url.path or url.query or url.fragment:
        raise ValueError("an exact HTTPS gateway origin is required")


def odata(value: str) -> str:
    return quote(value.replace("'", "''"))


def find_group(call, name: str) -> dict | None:
    rows = call("GET", f"groups?$filter=displayName eq '{odata(name)}'&$select=id,displayName,securityEnabled,"
                       "mailEnabled,groupTypes,isAssignableToRole")["value"]
    if len(rows) > 1:
        raise ValueError(f"ambiguous admin group {name!r}; remove the duplicate or set the group IDs explicitly")
    if rows and (not rows[0].get("securityEnabled") or rows[0].get("mailEnabled") or rows[0].get("isAssignableToRole")
                 or "DynamicMembership" in rows[0].get("groupTypes", [])):
        raise ValueError(f"existing group {name!r} is not a plain security group; do not reuse it")
    return rows[0] if rows else None


def ensure_group(call, name: str, description: str) -> tuple[str, bool]:
    existing = find_group(call, name)
    if existing:
        return existing["id"], False
    created = call("POST", "groups", {
        "displayName": name, "description": description, "mailEnabled": False, "securityEnabled": True,
        "mailNickname": "gg-" + uuid5(NAMESPACE_URL, "governedgraph:" + name).hex[:20],
    })
    return created["id"], True


def ensure_member(call, group_id: str, member: str) -> None:
    try:
        call("POST", f"groups/{group_id}/members/$ref",
             {"@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{member}"})
    except (GraphError, subprocess.CalledProcessError) as error:
        detail = error.detail if isinstance(error, GraphError) else error.stderr or ""
        if "already exist" not in detail or isinstance(error, GraphError) and error.status != 400:
            raise
        existing = call("GET", f"groups/{group_id}/members/{member}?$select=id")
        if existing.get("id", "").casefold() != member.casefold():
            raise ValueError("the deploying user's existing group membership could not be verified") from error


def signed_in_user(call=graph) -> str:
    """Resolve the human caller in the selected subscription's Graph tenant."""
    user = call("GET", "me?$select=id")
    member = user.get("id")
    if not isinstance(member, str):
        raise ValueError("a signed-in deploying user is required for the platform owner group")
    UUID(member)
    return member


def configure(environment: str, origin: str, member: str, *, apply: bool = False, naming_seed: str = "",
              reader_group: str = "", operator_group: str = "", group_claim: str = "ApplicationGroup",
              owner_group: str = "", owner_member: str = "", call=graph) -> dict:
    if environment != "gg-dev":
        raise ValueError("admin console sign-in registration is restricted to gg-dev")
    validate_origin(origin)
    UUID(member)
    owner_member = owner_member or member
    UUID(owner_member)
    for supplied in (reader_group, operator_group, owner_group):
        if supplied:
            UUID(supplied)
    if group_claim not in GROUP_CLAIMS:
        raise ValueError("group claim must be ApplicationGroup or SecurityGroup")
    label = names(environment, naming_seed)
    redirect = origin + "/admin/"
    if not apply:
        return {"apply": False, "redirect_uri": redirect, "display_names": label, "member": member, "owner_member": owner_member,
                "operations": [
                    "create or reuse distinct reader, operator and owner security groups unless group IDs are supplied",
                    "enroll the configured operator in managed reader/operator groups and the deploying user in the managed owner group",
                    "leave supplied group memberships unchanged",
                    "create or reuse a single-tenant single-page app with PKCE that exposes its own Admin.Access scope",
                    "pre-authorize the app for that scope, so sign-in needs no consent or admin approval",
                    f"emit groups in sign-in tokens ({group_claim})"
                    + (" and assign all three groups to the app" if group_claim == "ApplicationGroup" else ""),
                    "require assignment, so only group members can sign in" if group_claim == "ApplicationGroup"
                    else "the console still enforces group membership on every request"],
                "credentials": "none", "api_permissions": "none"}

    groups, created = {}, {}
    supplied_groups = [group for group in (reader_group, operator_group, owner_group) if group]
    if len(set(supplied_groups)) != len(supplied_groups):
        raise ValueError("reader, operator and owner groups must be different")
    for role, supplied in (("reader", reader_group), ("operator", operator_group), ("owner", owner_group)):
        if supplied:
            call("GET", f"groups/{supplied}?$select=id")
            groups[role], created[role] = supplied, False
            continue
        descriptions = {
            "reader": "View the GovernedGraph gg-dev administration console.",
            "operator": "View and apply protective actions in the GovernedGraph gg-dev administration console.",
            "owner": "Authorize recorded human decisions in the GovernedGraph gg-dev administration console.",
        }
        groups[role], created[role] = ensure_group(call, label[role], descriptions[role])
        ensure_member(call, groups[role], owner_member if role == "owner" else member)
    if len(set(groups.values())) != len(groups):
        raise ValueError("reader, operator and owner groups must be different")

    apps = call("GET", "applications?$filter=" + quote(f"displayName eq '{label['app']}'"))["value"]
    if len(apps) > 1:
        raise ValueError("ambiguous admin console application")
    if apps and (apps[0].get("passwordCredentials") or apps[0].get("keyCredentials")
                 or apps[0].get("signInAudience") != "AzureADMyOrg"):
        raise ValueError("existing admin console application has unexpected credentials or tenancy")
    application = apps[0] if apps else call("POST", "applications", {
        "displayName": label["app"], "signInAudience": "AzureADMyOrg"})
    client_id = application["appId"]
    # Like the voice presenter: the app exposes its own scope and pre-authorizes itself for it, so signing in needs no
    # consent and no Microsoft Graph permission (tenants that block user consent otherwise demand admin approval).
    scope_id = str(uuid5(NAMESPACE_URL, f"governedgraph:{environment}:{SCOPE}"))
    call("PATCH", f"applications/{application['id']}", {
        "identifierUris": [f"api://{client_id}"], "signInAudience": "AzureADMyOrg",
        "spa": {"redirectUris": [redirect]}, "groupMembershipClaims": group_claim,
        "web": {"implicitGrantSettings": {"enableIdTokenIssuance": False, "enableAccessTokenIssuance": False}},
        "api": {"requestedAccessTokenVersion": 2, "oauth2PermissionScopes": [{
            "id": scope_id, "value": SCOPE, "isEnabled": True, "type": "User",
            "adminConsentDisplayName": "Use the GovernedGraph admin console",
            "adminConsentDescription": "Open the gg-dev admin console as the signed-in user.",
            "userConsentDisplayName": "Use the GovernedGraph admin console",
            "userConsentDescription": "Open the gg-dev admin console as you."}]},
        "optionalClaims": {"accessToken": [{"name": "idtyp", "essential": False, "additionalProperties": []}]},
    })
    call("PATCH", f"applications/{application['id']}", {"api": {"preAuthorizedApplications": [
        {"appId": client_id, "delegatedPermissionIds": [scope_id]}]}})
    principals = call("GET", "servicePrincipals?$filter=" + quote(f"appId eq '{application['appId']}'"))["value"]
    if len(principals) > 1:
        raise ValueError("ambiguous admin console service principal")
    principal = principals[0] if principals else call("POST", "servicePrincipals", {"appId": application["appId"]})
    # Group assignment (and gating sign-in on it) needs Entra ID P1/P2; the SecurityGroup fallback skips both.
    gated = group_claim == "ApplicationGroup"
    call("PATCH", f"servicePrincipals/{principal['id']}", {"appRoleAssignmentRequired": gated, "accountEnabled": True})
    assigned = {row["principalId"] for row in call("GET", f"servicePrincipals/{principal['id']}/appRoleAssignedTo")["value"]}
    try:
        for group in groups.values() if gated else []:
            if group not in assigned:
                call("POST", f"servicePrincipals/{principal['id']}/appRoleAssignedTo",
                     {"principalId": group, "resourceId": principal["id"], "appRoleId": DEFAULT_ROLE})
    except subprocess.CalledProcessError as error:
        raise ValueError(
            "could not assign the groups to the sign-in app; group assignment needs Microsoft Entra ID P1 or P2. "
            "Set GOVERNEDGRAPH_ADMIN_GROUP_CLAIM=SecurityGroup to use security-group claims instead. "
            + (error.stderr or "").strip()[:200]) from error
    return {"apply": True, "client_id": application["appId"], "redirect_uri": redirect, "group_claim": group_claim,
            "reader_group_id": groups["reader"], "operator_group_id": groups["operator"],
            "owner_group_id": groups["owner"], "owner_member": owner_member,
            "created_groups": [role for role, was_created in created.items() if was_created], "member": member}


def deploying_user() -> str:
    for name in ("GOVERNEDGRAPH_ADMIN_MEMBER_ID", "STRATEGY_RECOVERY_OPERATOR_ID", "VOICE_DEMO_OPERATOR_ID"):
        if os.getenv(name):
            return os.environ[name]
    result = subprocess.run(["az", "ad", "signed-in-user", "show", "--query", "id", "-o", "tsv"],
                            capture_output=True, text=True, check=False)
    if result.returncode or not re.fullmatch(r"[0-9a-fA-F-]{36}", result.stdout.strip()):
        raise SystemExit("could not identify the user running the deployment; set GOVERNEDGRAPH_ADMIN_MEMBER_ID")
    return result.stdout.strip()


def enabled() -> bool:
    return (os.getenv("GOVERNEDGRAPH_DEPLOY_ADMIN_CONSOLE", "true").lower() != "false"
            and os.getenv("GOVERNEDGRAPH_ADMIN_ACCESS_CONTROL", "true").lower() != "false")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", default=os.getenv("AZURE_ENV_NAME", "gg-dev"))
    parser.add_argument("--origin", default=os.getenv("APIM_GATEWAY_URL", "").rstrip("/"))
    parser.add_argument("--member", default="", help="Object ID of the user to add (default: the deploying user)")
    parser.add_argument("--reader-group", default=os.getenv(ENV_KEYS["reader"], ""))
    parser.add_argument("--operator-group", default=os.getenv(ENV_KEYS["operator"], ""))
    parser.add_argument("--owner-group", default=os.getenv(ENV_KEYS["owner"], ""))
    parser.add_argument("--group-claim", default=os.getenv("GOVERNEDGRAPH_ADMIN_GROUP_CLAIM", "ApplicationGroup"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--azd-env", action="store_true", help="Record the results in the azd environment")
    parser.add_argument("--naming-seed", default=os.getenv("GOVERNEDGRAPH_NAMING_SEED", ""))
    args = parser.parse_args()
    if not enabled():
        print(json.dumps({"apply": False, "enabled": False, "reason": "admin console sign-in is disabled"}))
        raise SystemExit(0)
    try:
        result = configure(args.environment, args.origin, args.member or deploying_user(), apply=args.apply,
                           naming_seed=args.naming_seed, reader_group=args.reader_group,
                           operator_group=args.operator_group, owner_group=args.owner_group,
                           owner_member=signed_in_user() if args.apply else args.member or deploying_user(),
                           group_claim=args.group_claim)
        if args.azd_env and args.apply:
            for key, value in ((ENV_KEYS["client"], result["client_id"]), (ENV_KEYS["reader"], result["reader_group_id"]),
                               (ENV_KEYS["operator"], result["operator_group_id"]), (ENV_KEYS["owner"], result["owner_group_id"])):
                subprocess.run(["azd", "env", "set", key, value, "--environment", args.environment], check=True)
        print(json.dumps(result, indent=2))
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.stderr) from None
