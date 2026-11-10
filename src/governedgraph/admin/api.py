"""Operator console for governed strategy evidence and protective actions.

Hosted internally behind APIM, reading deployed Azure stores and acting only
through the role-protected Strategy API.
"""
from __future__ import annotations

import hmac
import logging
import math
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from governedgraph.admin.access import AccessPolicy, Identity
from governedgraph.admin.data import WATERMARK, revision
from governedgraph.admin.sources import AdminSourceError, AzureSources
from governedgraph.admin.traces import SyntheticTraceReader, TraceUnavailable, monitor_runner
from governedgraph.common.config import settings
from governedgraph.common.contracts import ContractError, SuspensionEvent, _strict, utcnow
from governedgraph.strategy_service.catalogue import ESCALATION_STRATEGY
from governedgraph.strategy_service.state import StateUnavailable

log = logging.getLogger("governedgraph.admin")
STATIC = Path(__file__).with_name("static")
BASE_PATH = re.compile(r"/(?:[A-Za-z0-9_-]+/)*")


@dataclass(frozen=True)
class ActionRequest:
    action: str
    expected_revision: str
    reason: str
    suspension: dict | None = None

    @classmethod
    def from_dict(cls, payload: dict) -> ActionRequest:
        result = _strict(cls, payload)
        if result.action not in {"freeze", "revert_to_static", "suspend"}:
            raise ContractError("only protective administration actions are supported")
        if not isinstance(result.expected_revision, str) or len(result.expected_revision) != 64:
            raise ContractError("a current state revision is required")
        if not isinstance(result.reason, str) or not 8 <= len(result.reason.strip()) <= 500:
            raise ContractError("provide a reason of 8 to 500 characters; no member details")
        if result.action != "suspend" and result.suspension is not None:
            raise ContractError("this action accepts no suspension payload")
        if result.action == "suspend" and not isinstance(result.suspension, dict):
            raise ContractError("suspension evidence is required")
        return result


def create_app(trace_reader: SyntheticTraceReader | None = None, access: AccessPolicy | None = None,
               sources: AzureSources | None = None) -> FastAPI:
    application = FastAPI(title="GovernedGraph Administration", docs_url=None, redoc_url=None, openapi_url=None)
    policy = access or AccessPolicy.from_env()
    public_origin = os.getenv("GOVERNEDGRAPH_ADMIN_PUBLIC_ORIGIN", "").strip().rstrip("/")
    base_path = os.getenv("GOVERNEDGRAPH_ADMIN_BASE_PATH", "/").strip() or "/"
    if not BASE_PATH.fullmatch(base_path):
        raise ValueError("GOVERNEDGRAPH_ADMIN_BASE_PATH must start and end with '/'")
    session = secrets.token_urlsafe(32)
    lock = RLock()
    trace_lookup = os.getenv("GOVERNEDGRAPH_ADMIN_TRACE_LOOKUP", "").lower() == "true"
    workspace = os.getenv("GOVERNEDGRAPH_ADMIN_TRACE_WORKSPACE") or (
        os.getenv("LOG_ANALYTICS_WORKSPACE_ID", "") if trace_lookup else "")
    project = os.getenv("GOVERNEDGRAPH_ADMIN_TRACE_PROJECT") or (
        settings.project_resource_id if trace_lookup else "")
    trace_environment = os.getenv("GOVERNEDGRAPH_ADMIN_TRACE_ENVIRONMENT") or settings.environment_name
    reader = trace_reader or SyntheticTraceReader(
        workspace, project, trace_environment,
        runner=monitor_runner(workspace) if workspace else None,
    )

    def enabled() -> None:
        if os.getenv("GOVERNEDGRAPH_ADMIN_ENABLED") != "true" or not public_origin.startswith("https://"):
            raise HTTPException(404, "administration is disabled")

    def backend() -> AzureSources:
        nonlocal sources
        if sources is None:
            sources = AzureSources.from_settings()
        return sources

    sign_in = " https://login.microsoftonline.com" if policy.mode == "enabled" else ""

    @application.middleware("http")
    async def protect(request: Request, call_next):
        origin = request.headers.get("origin")
        # The app is reachable only through the APIM gateway inside the virtual network.
        if origin is not None and origin != public_origin:
            return JSONResponse({"detail": "same-origin administration required"}, status_code=403)
        response = await call_next(request)
        response.headers.update({
            "Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; "
                                       f"connect-src 'self'{sign_in}; img-src 'self'; base-uri 'self'; "
                                       "frame-ancestors 'none'; form-action 'none'",
        })
        return response

    def operator(request: Request) -> None:
        enabled()
        if not hmac.compare_digest(request.headers.get("x-admin-session", ""), session):
            raise HTTPException(403, "reload the console to authorize this session")

    def require_read(request: Request) -> Identity:
        operator(request)
        return policy.require(request.headers.get("authorization", ""), act=False)

    def require_act(request: Request) -> Identity:
        operator(request)
        return policy.require(request.headers.get("authorization", ""), act=True)

    @application.exception_handler(StateUnavailable)
    @application.exception_handler(TraceUnavailable)
    @application.exception_handler(AdminSourceError)
    @application.exception_handler(OSError)
    @application.exception_handler(ValueError)
    @application.exception_handler(KeyError)
    @application.exception_handler(TypeError)
    async def unavailable(request: Request, error: Exception):
        log.error("admin_source_unavailable", extra={"path": request.url.path, "error_type": type(error).__name__})
        safe = isinstance(error, TraceUnavailable | AdminSourceError)
        detail = str(error) if safe else "Administration data unavailable; check operator diagnostics."
        return JSONResponse({"detail": detail}, status_code=503)

    @application.get("/healthz")
    def healthz():
        return {"ok": True}

    @application.get("/", dependencies=[Depends(enabled)])
    def index():
        page = (STATIC / "index.html").read_text()
        return HTMLResponse(page.replace("{{ADMIN_SESSION}}", session).replace("{{ADMIN_BASE}}", base_path))

    @application.get("/assets/{name}", dependencies=[Depends(enabled)])
    def asset(name: str):
        if name not in {"admin.css", "admin.js", "guide.js"}:
            raise HTTPException(404)
        return FileResponse(STATIC / name, media_type="text/css" if name.endswith(".css") else "text/javascript")

    @application.get("/api/access", dependencies=[Depends(operator)])
    def access_state():
        return policy.public()

    @application.get("/api/overview")
    def dashboard(identity: Identity = Depends(require_read)):  # noqa: B008
        source = backend()
        return {**source.overview(), "source_profile": source.profile, "trace_lookup_enabled": reader.enabled,
                "reinstatement_enabled": False,
                "access": {"mode": policy.mode, "user": identity.display, "can_act": identity.can_act,
                           "open_actions": policy.allow_open_actions}}

    @application.get("/api/journeys", dependencies=[Depends(require_read)])
    def journeys():
        grouped: dict[str, dict] = {}
        records, truncated = backend().audit_records()
        for row in records:
            item = grouped.setdefault(row["journey_id"], {
                "journey_id": row["journey_id"], "member_ref": row["member_ref"],
                "responses": 0, "last_activity": row["created_at"], "strategies": [],
            })
            item["responses"] += 1
            item["last_activity"] = max(item["last_activity"], row["created_at"])
            if row["strategy_id"] and row["strategy_id"] not in item["strategies"]:
                item["strategies"].append(row["strategy_id"])
        return {"journeys": sorted(grouped.values(), key=lambda row: row["last_activity"], reverse=True),
                "truncated": truncated}

    @application.get("/api/journeys/{journey_id}", dependencies=[Depends(require_read)])
    def journey(journey_id: str):
        from governedgraph.orchestrator.store import validate_id

        try:
            validate_id(journey_id)
        except ValueError as error:
            raise HTTPException(400, "invalid journey identifier") from error
        records = [row for row in backend().audit_records()[0] if row["journey_id"] == journey_id]
        if not records:
            raise HTTPException(404, "no audit evidence for this journey")
        return {"journey_id": journey_id, "records": sorted(records, key=lambda row: row["created_at"]),
                "transcript_notice": "Audit records contain no conversation text. Captured synthetic turns are a separate diagnostic source."}

    @application.get("/api/traces/{journey_id}", dependencies=[Depends(require_read)])
    def traces(journey_id: str):
        from governedgraph.orchestrator.store import validate_id

        try:
            validate_id(journey_id)
        except ValueError as error:
            raise HTTPException(400, "invalid journey identifier") from error
        log.info("admin_synthetic_trace_read", extra={"journey_id": journey_id})
        return reader.read(journey_id)

    @application.post("/api/actions")
    def action(payload: dict, identity: Identity = Depends(require_act)):  # noqa: B008
        try:
            request = ActionRequest.from_dict(payload)
        except (ContractError, TypeError) as error:
            raise HTTPException(400, str(error)) from error
        source = backend()
        with lock:
            state = source.state()
            context = source.context()
            if revision(state) != request.expected_revision:
                raise HTTPException(409, "strategy state changed; refresh and review the action again")
            event = None
            if request.action == "suspend":
                try:
                    event = SuspensionEvent.from_dict(request.suspension)
                    text_fields = (event.strategy_id, event.strategy_version, event.journey_state, event.audit_id,
                                   event.threshold_version, event.governance_case_id, event.created_at)
                    if not all(isinstance(value, str) and 0 < len(value) <= 150 and value == value.strip()
                               for value in text_fields):
                        raise ContractError("suspension references must be nonempty bounded strings")
                    if (type(event.gap_metric) not in {int, float} or not math.isfinite(event.gap_metric)
                            or event.gap_metric < 0):
                        raise ContractError("gap metric must be a nonnegative finite number")
                    if event.strategy_id == ESCALATION_STRATEGY:
                        raise ContractError("human escalation cannot be suspended")
                    if not context.catalogue.is_active(event.strategy_id, event.strategy_version):
                        raise ContractError("suspension requires an active catalogue version")
                    keys = set(context.priors["rankings"]) | set(state["current"]["rankings"] if state["current"] else {})
                    states = {key.split("|")[0] for key in keys}
                    if event.journey_state not in states | {"*"}:
                        raise ContractError("unknown suspension journey state")
                    if event.threshold_version != context.thresholds["threshold_version"]:
                        raise ContractError("use the current threshold version")
                except (ContractError, TypeError) as error:
                    raise HTTPException(400, str(error)) from error
            record = {"action_id": "ADMIN-" + uuid4().hex, "action": request.action, "reason": request.reason.strip(),
                      "actor": identity.actor, "created_at": utcnow(), "watermark": WATERMARK,
                      "expected_revision": request.expected_revision,
                      "suspension": request.suspension}
            source.journal({**record, "status": "requested"})
            try:
                source.apply(request.action, event)
            except (StateUnavailable, ValueError, OSError, AdminSourceError):
                source.journal({**record, "status": "failed"})
                raise
            try:
                source.journal({**record, "status": "applied", "completed_at": utcnow(),
                                "result_revision": revision(source.state())})
            except (OSError, AdminSourceError) as error:
                log.error("admin_action_completion_unavailable", extra={"action_id": record["action_id"]})
                raise HTTPException(503, f"Protection may have applied but completion could not be recorded. "
                                    f"Refresh and investigate {record['action_id']} before retrying.") from error
            log.info("admin_protective_action", extra={"action_id": record["action_id"], "action": request.action})
            return {"action_id": record["action_id"], "status": "applied"}

    return application


app = create_app()
