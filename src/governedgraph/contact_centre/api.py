"""Internal, Entra-protected synthetic advisor queue. No real callbacks."""
from __future__ import annotations

from contextlib import asynccontextmanager
from functools import lru_cache
import logging
from uuid import UUID

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request

from governedgraph.common.config import settings
from governedgraph.common.contracts import ContractError, EscalationRequest
from governedgraph.common.operational_logging import configure_operational_logging
from governedgraph.contact_centre.service import Handover, SyntheticContactCentre
from governedgraph.orchestrator.store import BlobRecordStore, FileRecordStore, JourneyBusy

log = logging.getLogger("governedgraph.contact_centre")


@lru_cache(maxsize=4)
def signing_keys(tenant: str):
    UUID(tenant)
    return jwt.PyJWKClient(f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys", timeout=5)


def require_runtime(authorization: str = Header("")) -> None:
    if not settings.contact_centre_client_id or not settings.strategy_tenant_id:
        raise HTTPException(503, "contact-centre authorization is not configured")
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Entra bearer token required")
    try:
        token = authorization.removeprefix("Bearer ")
        key = signing_keys(settings.strategy_tenant_id).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token, key.key, algorithms=["RS256"], audience=settings.contact_centre_client_id,
            issuer=f"https://login.microsoftonline.com/{settings.strategy_tenant_id}/v2.0",
            options={"require": ["exp", "iat", "aud", "iss", "tid", "oid"]},
        )
    except jwt.PyJWKClientConnectionError:
        log.error("contact_centre_signing_keys_unavailable")
        raise HTTPException(503, "Entra signing keys unavailable") from None
    except (jwt.PyJWTError, ValueError, TypeError):
        raise HTTPException(401, "invalid Entra access token") from None
    if (claims["tid"] != settings.strategy_tenant_id or claims["oid"] != settings.strategy_runtime_id
            or claims.get("idtyp") != "app" or claims.get("scp")
            or not isinstance(claims.get("roles"), list)
            or "ContactCentre.Access" not in claims.get("roles", [])):
        log.warning("contact_centre_caller_denied")
        raise HTTPException(403, "runtime application identity required")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.is_azure:
        configure_operational_logging()
        if not settings.contact_centre_storage_url:
            raise RuntimeError("private contact-centre storage required")
        store = BlobRecordStore(settings.contact_centre_storage_url, "handover-records", Handover.decode)
    else:
        store = FileRecordStore(settings.run_dir / "handovers", Handover.decode)
    app.state.centre = SyntheticContactCentre(store)
    yield


app = FastAPI(title="GovernedGraph synthetic contact centre", lifespan=lifespan)


@app.post("/handovers", dependencies=[Depends(require_runtime)])
def handover(body: dict, request: Request, idempotency_key: str = Header("", alias="Idempotency-Key")):
    try:
        escalation = EscalationRequest.from_dict(body)
        result = request.app.state.centre.handover(escalation, idempotency_key)
        return {"handover_id": result, "data": "ILLUSTRATIVE - synthetic data"}
    except (ContractError, ValueError):
        raise HTTPException(400, "invalid governed handover or idempotency key") from None
    except JourneyBusy:
        raise HTTPException(409, "handover busy; retry the same Idempotency-Key") from None
    except Exception as error:
        log.error("synthetic_handover_write_failed", extra={"error_type": type(error).__name__})
        raise HTTPException(503, "handover storage unavailable") from None


@app.get("/advisor-queue", dependencies=[Depends(require_runtime)])
def advisor_queue(request: Request, journey_id: str = Query(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")):
    try:
        return {"data": "ILLUSTRATIVE - synthetic data",
                "handovers": request.app.state.centre.advisor_queue(journey_id)}
    except JourneyBusy:
        raise HTTPException(409, "advisor queue busy; retry") from None
    except Exception as error:
        log.error("synthetic_advisor_queue_unavailable", extra={"error_type": type(error).__name__})
        raise HTTPException(503, "advisor queue storage unavailable") from None


@app.get("/healthz")
def healthz():
    return {"ok": True, "data": "ILLUSTRATIVE - synthetic data"}
