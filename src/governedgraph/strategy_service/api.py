"""HTTP surface of the Strategy Service (deployed to Azure Container Apps behind APIM).

Run locally:  uvicorn governedgraph.strategy_service.api:app --port 8082
"""
from __future__ import annotations

import logging
import socket
import time
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Response, Header

from governedgraph.common.contracts import ContractError, SuspensionEvent
from governedgraph.common.config import settings
from governedgraph.common.operational_logging import configure_operational_logging
from governedgraph.strategy_service.service import SnapshotRejected, StrategyService
from governedgraph.strategy_service.snapshot import RankingSnapshot, SignatureUnavailable
from governedgraph.strategy_service.state import BlobStateStore, StateUnavailable
from governedgraph.strategy_service.auth import require_operator, require_recovery_operator
from governedgraph.strategy_service.recovery import (
    DecisionReferenceUsed, RecoveryRequest, release, require_unused_decision_ref,
)
from governedgraph.strategy_service.recovery_azure import BlobRecoveryJournal, audit_health
from governedgraph.strategy_service.telemetry import configure_audit
from governedgraph.observability import emit, operation

configure_audit()
if settings.is_azure:
    configure_operational_logging()
log = logging.getLogger("governedgraph.strategy_service")
app = FastAPI(title="GovernedGraph Strategy Service", version="1.0.0")
svc = StrategyService(exploration_enabled=settings.is_azure)


@app.post("/strategies/ranked")
def get_ranked_strategies(payload: dict, response: Response,
                         parent: str = Header("", alias="traceparent")):
    started = time.perf_counter()
    try:
        with operation(str(payload.get("journey_id", "")), "governedgraph.strategy.ranked",
                       agent_name="gg-strategy-service", parent=parent, server=True):
            result = svc.get_ranked(payload).to_dict()
            emit("strategy_snapshot_state", event_id=f"strategy:{uuid4().hex}",
                 journey_id=payload["journey_id"], agent_name="gg-strategy-service",
                 snapshot_id=result["snapshot_id"], suspended_ids=",".join(result["suspended"]),
                 arm=result["arm"])
        response.headers["X-Strategy-Replica"] = socket.gethostname()
        response.headers["Server-Timing"] = f"get_ranked;dur={(time.perf_counter() - started) * 1000:.3f}"
        return result
    except ContractError as e:  # unknown or demographic field -> 400 (B7)
        raise HTTPException(status_code=400, detail=str(e))


@app.exception_handler(StateUnavailable)
@app.exception_handler(SignatureUnavailable)
async def state_unavailable(request, error):
    from fastapi.responses import JSONResponse

    log.error("strategy_mutation_failed", extra={"path": request.url.path})
    return JSONResponse(status_code=503, content={"detail": "strategy state unavailable; retry or escalate"})


@app.get("/snapshots")
def snapshot_state():
    """Read-only signed snapshot and global overlay for offline clients."""
    return svc.read_state()


@app.post("/snapshots", dependencies=[Depends(require_operator("publish"))])
def publish_snapshot(payload: dict):
    """Called only by the P2 pipeline identity (APIM policy + Entra role)."""
    try:
        expected = None
        if "snapshot" in payload:
            if (set(payload) != {"snapshot", "expected_snapshot_id"}
                    or not isinstance(payload["expected_snapshot_id"], str)
                    or not payload["expected_snapshot_id"]):
                raise ValueError("invalid conditional publication contract")
            expected = payload["expected_snapshot_id"]
            payload = payload["snapshot"]
        svc.load_snapshot(RankingSnapshot.from_dict(payload), expected_snapshot_id=expected)
    except (SnapshotRejected, ContractError, ValueError, TypeError, KeyError) as e:
        raise HTTPException(status_code=422, detail=f"snapshot rejected: {e}")
    return {"loaded": payload["snapshot_id"]}


@app.post("/suspensions", dependencies=[Depends(require_operator("suspend"))])
def suspend(payload: dict):
    """Called only by the Fairness & Explainability Agent identity. Protective, always autonomous."""
    try:
        svc.suspend(SuspensionEvent.from_dict(payload))
    except (ContractError, TypeError, ValueError) as error:
        raise HTTPException(400, str(error)) from error
    return {"suspended": payload["strategy_id"], "journey_state": payload["journey_state"]}


@app.post("/freeze", dependencies=[Depends(require_operator("freeze"))])
def freeze():
    svc.freeze()
    return {"frozen": True}


@app.post("/reinstatements", dependencies=[Depends(require_operator("reinstate"))])
def reinstate(payload: dict):
    if set(payload) != {"strategy_id", "journey_state", "governance_decision_ref"}:
        raise HTTPException(400, "invalid reinstatement contract")
    try:
        svc.reinstate(**payload)
    except (PermissionError, TypeError) as error:
        raise HTTPException(403, str(error)) from error
    return {"reinstated": payload["strategy_id"]}


@app.get("/freeze-recovery")
def recovery_status(decision_ref: str | None = None, actor_id: str = Depends(require_recovery_operator)):
    if decision_ref is not None:
        if not isinstance(svc.store, BlobStateStore):
            raise StateUnavailable("human dev recovery requires the durable Azure strategy store")
        try:
            require_unused_decision_ref(decision_ref, BlobRecoveryJournal(svc.store.container))
        except ContractError as error:
            raise HTTPException(400, str(error)) from error
        except DecisionReferenceUsed as error:
            raise HTTPException(409, str(error)) from error
    svc.frozen_revision()
    return {"frozen": True, "audit_execution": audit_health(),
            **({"decision_ref": decision_ref} if decision_ref is not None else {})}


@app.post("/freeze-recovery")
def recover(payload: dict, actor_id: str = Depends(require_recovery_operator)):
    try:
        request = RecoveryRequest.from_dict(payload)
    except (ContractError, TypeError) as error:
        raise HTTPException(400, str(error)) from error
    if not isinstance(svc.store, BlobStateStore):
        raise StateUnavailable("human dev recovery requires the durable Azure strategy store")
    try:
        return release(svc, request, actor_id, audit_health, BlobRecoveryJournal(svc.store.container))
    except DecisionReferenceUsed as error:
        raise HTTPException(409, str(error)) from error


@app.post("/revert-to-static", dependencies=[Depends(require_operator("freeze"))])
def revert_to_static():
    svc.revert_to_static()
    return {"frozen": True, "ranking": "expert_priors"}


@app.get("/healthz")
def healthz():
    state = svc.read_state()
    snapshot_id = state["current"]["snapshot_id"] if state["current"] else None
    if state["frozen"]:
        log.warning("strategy_frozen", extra={"snapshot_id": snapshot_id or "none",
                                              "frozen": True})
    return {"ok": True, "snapshot": snapshot_id, "frozen": state["frozen"]}
