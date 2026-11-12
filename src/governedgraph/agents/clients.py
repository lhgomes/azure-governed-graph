"""Clients the agents use. Local mode calls in-process components; Azure mode
calls the same contracts through the APIM facade (managed identity)."""
from __future__ import annotations

import logging
from urllib.parse import quote

from governedgraph.common.config import settings
from governedgraph.common.contracts import MemberValue, MemberValues, RankedStrategy, StrategyResponse
from governedgraph.common.member_auth import verify_member_token
from governedgraph.common.tracing import traceparent

log = logging.getLogger("governedgraph.member_values")


class ApimValuesClient:
    """GET {APIM}/members/{ref}/values. APIM: circuit breaker, retry, rate limit, wiretap."""

    def __init__(self, member_token: str = ""):
        self.member_token = member_token

    def get(self, member_ref: str, authenticated_member_ref: str, *, journey_id: str = "",
            trace_parent: str = "") -> MemberValues:
        import httpx
        from governedgraph.observability import headers

        identity = verify_member_token(self.member_token, require_step_up=True)
        if identity.member_ref != authenticated_member_ref or member_ref != identity.member_ref:
            log.error("member_scope_violation", extra={"journey_id": journey_id})
            raise PermissionError("member scoping violation")
        parent = traceparent(trace_parent)
        from governedgraph.agents.connections import http_client

        r = http_client().get(f"{settings.member_values_url.rstrip('/')}/members/{quote(member_ref, safe='')}/values",
                      headers={"Authorization": f"Bearer {self.member_token}",
                               **headers(journey_id, parent)},
                      timeout=15)
        log.info("member_values_call", extra={"journey_id": journey_id, "traceparent": parent,
                                              "status_code": r.status_code})
        r.raise_for_status()
        d = r.json()
        if d["member_ref"] != identity.member_ref:
            log.error("member_scope_violation", extra={"journey_id": journey_id})
            raise PermissionError("administrator returned another member's values")
        return MemberValues(d["member_ref"], [MemberValue(**v) for v in d["values"]])


class HttpStrategyClient:
    def get_ranked(self, request: dict) -> StrategyResponse:
        import httpx

        from governedgraph.agents.connections import http_client
        from governedgraph.observability import headers

        r = http_client().post(f"{settings.strategy_service_url}/strategies/ranked", json=request, timeout=5,
                               headers=headers(request["journey_id"]))
        r.raise_for_status()
        d = r.json()
        d["ranked"] = [RankedStrategy(**x) for x in d["ranked"]]
        return StrategyResponse(**d)


def default_values_client(member_token: str = ""):
    if not settings.member_values_url:
        raise ValueError("MEMBER_VALUES_API_URL is required in Azure mode")
    return ApimValuesClient(member_token)


def default_strategy_client(service=None):
    if not settings.strategy_service_url:
        raise ValueError("STRATEGY_SERVICE_URL is required in Azure mode")
    return HttpStrategyClient()
