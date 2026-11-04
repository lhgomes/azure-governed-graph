"""Read-only Member Values API, published through the APIM facade (anti-corruption layer).

GET only. The authenticated member reference arrives from APIM (validated
token claim) in X-Authenticated-Member; APIM strips any client-supplied copy.
Run locally: uvicorn pension_wtp.member_values_api.app:app --port 8081
"""
from __future__ import annotations

from dataclasses import asdict
import logging

from fastapi import FastAPI, Header, HTTPException, Request
from governedgraph.common.tracing import traceparent

from pension_wtp.member_values_api.data import MemberScopeError, get_member_values

app = FastAPI(title="GovernedGraph Mock Member Values API (read-only, synthetic)", version="1.0.0")
log = logging.getLogger("pension_wtp.member_values_api")


@app.middleware("http")
async def log_call(request: Request, call_next):
    parent = traceparent(request.headers.get("traceparent", ""))
    request.state.trace_parent = parent
    request.state.journey_id = request.headers.get("x-journey-id", "")
    response = await call_next(request)
    log.info("member_values_call", extra={"journey_id": request.state.journey_id,
                                          "traceparent": parent, "method": request.method,
                                          "status_code": response.status_code})
    response.headers["traceparent"] = parent
    return response


@app.get("/members/{member_ref}/values")
def member_values(member_ref: str, request: Request, x_authenticated_member: str = Header(...)):
    try:
        return asdict(get_member_values(member_ref, x_authenticated_member))
    except MemberScopeError as e:
        log.error("member_scope_violation", extra={"journey_id": request.state.journey_id,
                                                  "traceparent": request.state.trace_parent})
        raise HTTPException(status_code=403, detail=str(e))
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown member")


@app.get("/healthz")
def healthz():
    return {"ok": True}
