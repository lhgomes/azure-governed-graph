"""Public-key verification of channel-issued member tokens; never mint tokens here."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

import jwt

from governedgraph.common.config import settings


@dataclass(frozen=True)
class MemberIdentity:
    member_ref: str
    step_up_verified: bool


class MemberAuthenticator(Protocol):
    """Domain adapter: assert step-up only after verified member authentication."""

    def verify(self, token: str) -> MemberIdentity: ...


class SignedClaimAuthenticator:
    """Reference stub: trust only the configured issuer's signed step-up claim."""

    def verify(self, token: str) -> MemberIdentity:
        return verify_member_token(token)


def verify_member_token(token: str, *, require_step_up: bool = False) -> MemberIdentity:
    if not settings.member_token_issuer or not settings.member_token_public_jwk:
        raise jwt.InvalidTokenError("member identity verification is not configured")
    public_jwk = json.loads(settings.member_token_public_jwk)
    if (set(public_jwk) - {"kty", "n", "e", "alg", "use", "kid", "key_ops"}
            or set(public_jwk.get("key_ops", ["verify"])) - {"verify"}):
        raise jwt.InvalidTokenError("only public RSA verification material is permitted")
    key = jwt.PyJWK.from_dict(public_jwk, algorithm="RS256")
    try:
        claims = jwt.decode(
            token, key.key, algorithms=["RS256"], audience=settings.member_token_audience,
            issuer=settings.member_token_issuer,
            options={"require": ["exp", "iat", "iss", "aud", "member_ref"]},
        )
    except (TypeError, ValueError) as error:
        raise jwt.InvalidTokenError("invalid member claim type") from error
    if set(claims) - {"iss", "aud", "iat", "exp", "nbf", "sub", "jti", "member_ref", "acr"}:
        raise jwt.InvalidTokenError("unknown member identity claim")
    member_ref = claims["member_ref"]
    if not isinstance(member_ref, str) or not member_ref.strip():
        raise jwt.InvalidTokenError("member_ref must be a nonempty string")
    if not isinstance(claims["iat"], int) or not isinstance(claims["exp"], int):
        raise jwt.InvalidTokenError("token timestamps must be integers")
    if not 0 < claims["exp"] - claims["iat"] <= 900:
        raise jwt.InvalidTokenError("member token lifetime must not exceed fifteen minutes")
    step_up = claims.get("acr") == "stepup"
    if require_step_up and not step_up:
        raise jwt.InvalidTokenError("step-up authentication required")
    return MemberIdentity(member_ref, step_up)
