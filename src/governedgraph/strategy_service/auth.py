"""Entra authorization for strategy mutations; roles never come from headers."""
from __future__ import annotations

import logging
from functools import lru_cache
from uuid import UUID

import jwt
from fastapi import Header, HTTPException

from governedgraph.common.config import settings

log = logging.getLogger("governedgraph.strategy_service")


@lru_cache(maxsize=4)
def signing_keys(tenant_id: str) -> jwt.PyJWKClient:
    UUID(tenant_id)
    return jwt.PyJWKClient(
        f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys",
        timeout=5,
    )


def authorize(action: str, authorization: str) -> dict:
    if not settings.strategy_api_client_id or not settings.strategy_tenant_id:
        log.error("strategy_auth_not_configured")
        raise HTTPException(503, "strategy authorization is not configured")
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Entra bearer token required")
    token = authorization.removeprefix("Bearer ")
    try:
        key = signing_keys(settings.strategy_tenant_id).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token, key.key, algorithms=["RS256"],
            audience=settings.strategy_api_client_id,
            issuer=f"https://login.microsoftonline.com/{settings.strategy_tenant_id}/v2.0",
            options={"require": ["exp", "iat", "iss", "aud", "tid", "oid"]},
        )
    except jwt.PyJWKClientConnectionError as error:
        log.error("strategy_signing_keys_unavailable")
        raise HTTPException(503, "Entra signing keys unavailable") from error
    except (jwt.PyJWTError, ValueError, TypeError) as error:
        raise HTTPException(401, "invalid Entra access token") from error
    if claims["tid"] != settings.strategy_tenant_id:
        raise HTTPException(401, "invalid Entra tenant")
    if action == "recover":
        if (settings.environment_name != "gg-dev" or not settings.strategy_recovery_operator_id
                or claims["oid"] != settings.strategy_recovery_operator_id
                or claims.get("idtyp") == "app" or not isinstance(claims.get("scp"), str)
                or "Strategy.Recover" not in claims["scp"].split()
                or claims.get("azp") != "04b07795-8ddb-461a-bbee-02f9e1bf7b46"):
            log.warning("strategy_recovery_denied")
            raise HTTPException(403, "delegated access by the approved human operator is required")
        return claims
    if action == "reinstate":
        if (not settings.strategy_governance_group or claims.get("idtyp") == "app"
                or not claims.get("scp") or not isinstance(claims.get("groups"), list)
                or settings.strategy_governance_group not in claims["groups"]):
            raise HTTPException(403, "human governance group required; reinstatement disabled otherwise")
        return claims
    principals = {
        "publish": {settings.strategy_publisher_id},
        "suspend": {settings.strategy_fairness_id},
        "freeze": {settings.strategy_publisher_id, settings.strategy_fairness_id, settings.strategy_runtime_id},
    }
    roles = {"publish": "Strategy.Publish", "suspend": "Strategy.Suspend", "freeze": "Strategy.Freeze"}
    if (claims.get("idtyp") != "app" or claims.get("scp")
            or not claims["oid"] or claims["oid"] not in principals[action]
            or not isinstance(claims.get("roles"), list) or roles[action] not in claims["roles"]):
        log.warning("strategy_mutation_denied", extra={"action": action})
        raise HTTPException(403, "identity and application role do not permit this mutation")
    return claims


def require_recovery_operator(authorization: str = Header(default="")) -> str:
    return authorize("recover", authorization)["oid"]


def require_operator(action: str):
    def dependency(authorization: str = Header(default="")) -> None:
        if settings.is_azure:
            authorize(action, authorization)

    return dependency
