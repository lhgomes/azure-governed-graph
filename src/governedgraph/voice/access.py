"""Separate delegated presenter and application-only synthetic issuer authorization."""
import logging
import os

import jwt
from fastapi import HTTPException

from governedgraph.common.config import settings
from governedgraph.strategy_service.auth import signing_keys

log = logging.getLogger("governedgraph.voice")
COOKIE = "__Secure-gg-voice-demo"
CLI_CLIENT = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"


def enabled() -> bool:
    return settings.is_azure and settings.environment_name == "gg-dev" and os.getenv("GOVERNEDGRAPH_DEPLOY_VOICE_DEMO") == "true"


def verify(token: str, *, application: bool = False) -> dict:
    if not enabled():
        raise HTTPException(404, "dev voice demo disabled")
    tenant, audience = settings.strategy_tenant_id, os.getenv("VOICE_DEMO_CLIENT_ID", "")
    if not tenant or not audience:
        raise HTTPException(503, "dev voice authorization not configured")
    if not token:
        raise HTTPException(401, "Entra access token required")
    try:
        key = signing_keys(tenant).get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=audience,
                            issuer=f"https://login.microsoftonline.com/{tenant}/v2.0",
                            options={"require": ["exp", "iat", "iss", "aud", "tid", "oid", "azp"]})
    except jwt.PyJWKClientConnectionError:
        log.error("voice_signing_keys_unavailable")
        raise HTTPException(503, "Entra signing keys unavailable") from None
    except (jwt.PyJWTError, ValueError, TypeError):
        raise HTTPException(401, "invalid Entra access token") from None
    roles, scope = claims.get("roles"), claims.get("scp")
    valid = claims["tid"] == tenant and isinstance(roles, list)
    if application:
        valid = (valid and claims.get("idtyp") == "app" and not scope
                 and claims["oid"] == os.getenv("VOICE_BRIDGE_PRINCIPAL_ID")
                 and claims["azp"] == os.getenv("VOICE_BRIDGE_CLIENT_ID")
                 and "VoiceDemo.Issue" in roles)
    else:
        valid = (valid and claims.get("idtyp") != "app" and isinstance(scope, str)
                 and "VoiceDemo.Access" in scope.split()
                 and claims["oid"] == os.getenv("VOICE_DEMO_OPERATOR_ID")
                 and claims["azp"] in {audience, CLI_CLIENT} and "VoiceDemo.Presenter" in roles)
    if not valid:
        log.warning("voice_caller_denied")
        raise HTTPException(403, "caller is not assigned to this dev operation")
    return claims
