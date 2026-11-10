"""Optional Entra ID group access control for the administration console.

Two levels, each one environment variable holding an Entra group object ID:
  GOVERNEDGRAPH_ADMIN_READER_GROUP_ID    may view evidence
  GOVERNEDGRAPH_ADMIN_OPERATOR_GROUP_ID  may view evidence and apply protective actions
Both blank: access control is off and the app says so. Any misconfiguration fails closed.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from uuid import UUID

import jwt
from fastapi import HTTPException

from governedgraph.common.config import settings
from governedgraph.strategy_service.auth import signing_keys

log = logging.getLogger("governedgraph.admin")
READER_VARIABLE = "GOVERNEDGRAPH_ADMIN_READER_GROUP_ID"
OPERATOR_VARIABLE = "GOVERNEDGRAPH_ADMIN_OPERATOR_GROUP_ID"
OPEN_ACTOR = "local-loopback-operator"
SCOPE = "Admin.Access"


@dataclass(frozen=True)
class Identity:
    actor: str
    display: str
    can_read: bool
    can_act: bool


@dataclass(frozen=True)
class AccessPolicy:
    reader_group: str = ""
    operator_group: str = ""
    client_id: str = ""
    tenant_id: str = ""
    # Open (group-less) consoles may act locally; on Azure they act only when explicitly allowed.
    allow_open_actions: bool = True

    @classmethod
    def from_env(cls) -> AccessPolicy:
        return cls(
            os.getenv(READER_VARIABLE, "").strip(), os.getenv(OPERATOR_VARIABLE, "").strip(),
            os.getenv("GOVERNEDGRAPH_ADMIN_CLIENT_ID", "").strip(),
            (os.getenv("GOVERNEDGRAPH_ADMIN_TENANT_ID") or os.getenv("STRATEGY_TENANT_ID", "")).strip(),
            os.getenv("GOVERNEDGRAPH_ADMIN_ALLOW_OPEN_ACTIONS", "").lower() == "true",
        )

    @property
    def enabled(self) -> bool:
        return bool(self.reader_group or self.operator_group)

    @property
    def problem(self) -> str:
        """Why enforcement cannot work, or an empty string."""
        if not self.enabled:
            return ""
        for name, value in (("reader group", self.reader_group), ("operator group", self.operator_group),
                            ("client", self.client_id), ("tenant", self.tenant_id)):
            if name.endswith("group") and not value:
                continue
            try:
                UUID(value)
            except ValueError:
                return (f"Access control is misconfigured: the {name} ID is missing or not a GUID. "
                        f"Set GOVERNEDGRAPH_ADMIN_CLIENT_ID and a tenant ID, and use group object IDs.")
        return ""

    @property
    def mode(self) -> str:
        return "disabled" if not self.enabled else "misconfigured" if self.problem else "enabled"

    def public(self) -> dict:
        result = {"mode": self.mode}
        if self.mode == "enabled":
            result |= {"client_id": self.client_id, "tenant_id": self.tenant_id,
                       "restricts_reads": bool(self.reader_group), "operator_group_set": bool(self.operator_group)}
        elif self.mode == "misconfigured":
            result["detail"] = self.problem
        else:
            result["open_actions"] = self.allow_open_actions
        return result

    def identify(self, authorization: str) -> Identity:
        if not self.enabled:
            return Identity(OPEN_ACTOR, "Access control off", True, self.allow_open_actions)
        if self.problem:
            raise HTTPException(503, self.problem)
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "Sign in with Microsoft to open the console.")
        token = authorization.removeprefix("Bearer ")
        try:
            key = signing_keys(self.tenant_id).get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token, key.key, algorithms=["RS256"], audience=self.client_id,
                issuer=f"https://login.microsoftonline.com/{self.tenant_id}/v2.0",
                options={"require": ["exp", "iat", "iss", "aud", "tid", "oid"]},
            )
        except jwt.PyJWKClientConnectionError as error:
            log.error("admin_signing_keys_unavailable")
            raise HTTPException(503, "Microsoft sign-in keys are unavailable; retry shortly.") from error
        except (jwt.PyJWTError, ValueError, TypeError) as error:
            raise HTTPException(401, "Your sign-in is invalid or expired. Sign in again.") from error
        # Only a delegated access token for this application's own scope: no ID tokens, app-only tokens or other scopes.
        scopes = claims.get("scp")
        if (claims["tid"] != self.tenant_id or claims.get("idtyp") == "app" or not isinstance(scopes, str)
                or SCOPE not in scopes.split()):
            raise HTTPException(401, "Your sign-in is invalid or expired. Sign in again.")
        if "groups" in claims.get("_claim_names", {}) or claims.get("hasgroups"):
            raise HTTPException(403, "Your token has too many groups to include. Ask an administrator to emit "
                                     "only groups assigned to this application.")
        groups = claims.get("groups", [])
        if not isinstance(groups, list):
            raise HTTPException(403, "Your token carries no usable group list.")
        operator = bool(self.operator_group) and self.operator_group in groups
        reader = operator or (self.reader_group in groups if self.reader_group else True)
        who = claims.get("preferred_username") or claims.get("name") or claims["oid"]
        if not reader:
            log.warning("admin_access_denied", extra={"oid": claims["oid"]})
            raise HTTPException(403, "Your account is not in a group allowed to use this console.")
        return Identity(f"entra:{claims['oid']}", str(who)[:120], True, operator)

    def require(self, authorization: str, *, act: bool) -> Identity:
        identity = self.identify(authorization)
        if act and not identity.can_act:
            if self.enabled:
                log.warning("admin_action_denied", extra={"actor": identity.actor})
                raise HTTPException(403, "Protective actions need membership of the operator group.")
            raise HTTPException(403, "Protective actions are disabled while access control is off. Set the reader "
                                     "and operator group variables, or explicitly allow open actions.")
        return identity
