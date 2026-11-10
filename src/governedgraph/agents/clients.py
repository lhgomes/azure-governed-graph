"""Clients the agents use. Local mode calls in-process components; Azure mode
calls the same contracts through the APIM facade (managed identity)."""
from __future__ import annotations

import logging
from urllib.parse import quote

from governedgraph.common.config import LOCAL_VALUES_PROVIDER, settings
from governedgraph.common.contracts import MemberValue, MemberValues, RankedStrategy, StrategyResponse
from governedgraph.common.member_auth import verify_member_token
from governedgraph.common.tracing import traceparent

log = logging.getLogger("governedgraph.member_values")


class LocalValuesClient:
    def get(self, member_ref: str, authenticated_member_ref: str, *, journey_id: str = "",
            trace_parent: str = "") -> MemberValues:
        from importlib import import_module

        module_name, separator, attribute = LOCAL_VALUES_PROVIDER.partition(":")
        if not module_name or not separator or not attribute:
            raise ValueError("Local values require explicit GOVERNEDGRAPH_LOCAL_VALUES_PROVIDER")
        get_member_values = getattr(import_module(module_name), attribute)

        try:
            values = get_member_values(member_ref, authenticated_member_ref)
        except PermissionError:
            log.error("member_scope_violation", extra={"journey_id": journey_id,
                                                       "traceparent": traceparent(trace_parent)})
            raise
        log.info("member_values_call", extra={"journey_id": journey_id,
                                              "traceparent": traceparent(trace_parent), "status_code": 200})
        return values


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
                               **(headers(journey_id, parent) if settings.is_azure else
                                  {"X-Journey-ID": journey_id, "traceparent": parent})},
                      timeout=15)
        log.info("member_values_call", extra={"journey_id": journey_id, "traceparent": parent,
                                              "status_code": r.status_code})
        r.raise_for_status()
        d = r.json()
        if d["member_ref"] != identity.member_ref:
            log.error("member_scope_violation", extra={"journey_id": journey_id})
            raise PermissionError("administrator returned another member's values")
        return MemberValues(d["member_ref"], [MemberValue(**v) for v in d["values"]])


class LocalStrategyClient:
    def __init__(self, service=None):
        from governedgraph.strategy_service.service import StrategyService

        self.service = service or StrategyService()

    def get_ranked(self, request: dict) -> StrategyResponse:
        return self.service.get_ranked(request)


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
    if settings.is_azure:
        if not settings.member_values_url:
            raise ValueError("MEMBER_VALUES_API_URL is required in Azure mode")
        return ApimValuesClient(member_token)
    return LocalValuesClient()


def default_strategy_client(service=None):
    if settings.is_azure:
        if not settings.strategy_service_url:
            raise ValueError("STRATEGY_SERVICE_URL is required in Azure mode")
        return HttpStrategyClient()
    return LocalStrategyClient(service)
