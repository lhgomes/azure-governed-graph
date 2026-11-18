"""Channel-facing durable orchestrator, hosted on Container Apps."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import traceback
import os
from typing import Annotated, Literal
from uuid import uuid4

import jwt
import httpx
from fastapi import FastAPI, HTTPException, Header, Request
from pydantic import BaseModel
from fastapi.responses import HTMLResponse
from fastapi.responses import JSONResponse

from governedgraph.common.config import settings
from governedgraph.common import domain
from governedgraph.common.member_auth import MemberAuthenticator, MemberIdentity, SignedClaimAuthenticator
from governedgraph.common.operational_logging import configure_operational_logging
from governedgraph.observability import configure_events, operation
from governedgraph.contact_centre.client import ContactCentreClient
from governedgraph.contact_centre.service import advisor_queue_html
from governedgraph.explainability.store import AuditUnavailable
from governedgraph.orchestrator.consumer import InboxRecord, JourneyConsumer
from governedgraph.orchestrator.continuity import CustomerConversations
from governedgraph.orchestrator.messaging import ServiceBusJourneyPublisher
from governedgraph.orchestrator.service import (
    DeliveryPending, InvalidAnswerReference, MemberScopeViolation, OrchestratorService, OutcomeConflict,
)
from governedgraph.orchestrator.store import (
    BlobJourneyStore, BlobRecordStore, JourneyBusy, validate_id,
)
from governedgraph.orchestrator.worker import ServiceBusJourneyWorker
from governedgraph.common.contracts import ContractError, OutcomeSubmission, ResponseReference
from governedgraph.voice.hosted import VoiceScriptRequest, run_hosted
from governedgraph.voice.voice_live_bridge import VoiceError
from governedgraph.voice.presenter import router as presenter_router
from governedgraph.admin.testing import router as admin_testing_router

log = logging.getLogger("governedgraph.orchestrator")
member_authenticator: MemberAuthenticator = SignedClaimAuthenticator()


def create_runtime() -> OrchestratorService:
    from governedgraph.learning.feedback_store import default_feedback_store

    configure_operational_logging()
    configure_events()
    if not settings.journey_storage_url or not settings.service_bus_namespace:
        raise RuntimeError("Azure journey storage and Service Bus namespace must be configured")
    centre = ContactCentreClient(settings.contact_centre_url, settings.contact_centre_audience)
    consumer = JourneyConsumer(BlobRecordStore(settings.journey_storage_url, "journey-inbox", InboxRecord.decode), centre)
    return OrchestratorService(
        BlobJourneyStore(settings.journey_storage_url),
        ServiceBusJourneyPublisher(settings.service_bus_namespace), contact_centre=centre,
        worker=ServiceBusJourneyWorker(settings.service_bus_namespace, consumer),
        feedback_store=default_feedback_store(),
        continuity=CustomerConversations.azure(settings.journey_storage_url, settings.project_endpoint),
    )

async def recover(runtime: OrchestratorService, stopped: asyncio.Event) -> None:
    while not stopped.is_set():
        try:
            await asyncio.to_thread(runtime.recover)
        except Exception as error:
            frame = traceback.extract_tb(error.__traceback__)[-1]
            log.error("journey_outbox_recovery_failed", extra={
                "error_type": type(error).__name__,
                "failure_domain": f"{frame.filename.rsplit('/', 1)[-1].removesuffix('.py')}.{frame.name}.L{frame.lineno}"})
        try:
            await asyncio.wait_for(stopped.wait(), timeout=5)
        except TimeoutError:
            continue


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.runtime = create_runtime()
    stopped = asyncio.Event()
    worker = asyncio.create_task(recover(app.state.runtime, stopped))
    try:
        yield
    finally:
        stopped.set()
        await worker
        if isinstance(app.state.runtime.publisher, ServiceBusJourneyPublisher):
            await asyncio.to_thread(app.state.runtime.publisher.close)


app = FastAPI(title="GovernedGraph Member Journey Orchestrator", version="1.1.0", lifespan=lifespan)
app.include_router(presenter_router)
app.include_router(admin_testing_router)


class StartJourney(BaseModel):
    model_config = {"extra": "forbid"}
    channel: Literal["voice", "chat", "portal", "letter_follow_up"] = "chat"
    accessibility_mode: Literal["standard", "plain_language", "english", "slower_pace", "large_text"] = "standard"
    contact_type: Literal["first_contact", "second_contact"] = "first_contact"


class OutcomeBody(BaseModel):
    model_config = {"extra": "forbid"}
    response_id: str
    choice: Literal["yes", "no", "skip"]


class RelatedAnswerBody(BaseModel):
    model_config = {"extra": "forbid"}
    journey_id: str
    response_id: str


class Turn(BaseModel):
    model_config = {"extra": "forbid"}
    utterance: str
    outcome: OutcomeBody | None = None
    related_answer: RelatedAnswerBody | None = None
    playback_response_id: str | None = None


def authenticate(authorization: str) -> tuple[str, MemberIdentity]:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "member token required")
    try:
        return token, member_authenticator.verify(token)
    except jwt.InvalidTokenError:
        log.warning("member_token_rejected")
        raise HTTPException(401, "invalid member token") from None


@app.post("/journeys")
def start(body: StartJourney, request: Request, authorization: str = Header("")):
    _, identity = authenticate(authorization)
    try:
        return request.app.state.runtime.start(
            identity, body.channel, body.accessibility_mode, contact_type=body.contact_type)
    except Exception as error:
        log.error("journey_start_failed", extra={"error_type": type(error).__name__})
        raise HTTPException(503, domain.message("journey_unavailable")) from None


@app.post("/journeys/{journey_id}/turns")
def turn(journey_id: str, body: Turn, request: Request, authorization: str = Header(""),
         traceparent_header: str = Header("", alias="traceparent"),
         idempotency_key: str = Header("", alias="Idempotency-Key"),
         admin_authorization: Annotated[str, Header(alias="X-Admin-Authorization")] = ""):
    received_at = datetime.now(timezone.utc)
    token, identity = authenticate(authorization)
    if admin_authorization:
        from governedgraph.admin.access import AccessPolicy
        from governedgraph.admin.testing import require_testing

        require_testing()
        AccessPolicy.from_env().require(admin_authorization, act=True)
    try:
        validate_id(journey_id)
        request_id = validate_id(idempotency_key) if idempotency_key else uuid4().hex
    except ValueError:
        raise HTTPException(400, "invalid journey or idempotency identifier") from None
    try:
        if body.playback_response_id is not None:
            if body.utterance or body.related_answer is not None or body.outcome is not None:
                raise ContractError("playback confirmation cannot include a turn or feedback")
            playback = ResponseReference(journey_id, body.playback_response_id)
            submission = reference = None
        elif body.outcome is not None:
            if body.utterance or body.related_answer is not None:
                raise ContractError("feedback cannot include an utterance or a related answer")
            submission = OutcomeSubmission.from_dict(body.outcome.model_dump())
            playback = None
            reference = None
        else:
            submission = playback = None
            reference = (ResponseReference.from_dict(body.related_answer.model_dump())
                         if body.related_answer is not None else None)
            if reference is not None and not body.utterance.strip():
                raise ContractError("a related answer requires a question")
    except (ContractError, TypeError):
        raise HTTPException(400, "invalid closed outcome, playback or answer reference") from None
    try:
        with operation(journey_id, "governedgraph.orchestrator.turn", agent_name="gg-orchestrator",
                       parent=traceparent_header, server=True):
            if playback is not None:
                return request.app.state.runtime.confirm_playback(journey_id, request_id, identity,
                                                                  playback.response_id, received_at=received_at)
            if submission is not None:
                return request.app.state.runtime.record_outcome(journey_id, request_id, identity, submission,
                                                                received_at=received_at)
            return request.app.state.runtime.turn(journey_id, request_id, identity, token,
                                              body.utterance, traceparent_header,
                                              **({"related_answer": reference, "received_at": received_at}
                                                 if reference is not None else {}),
                                              **({"voice_model": request.state.voice_candidate}
                                                 if hasattr(request.state, "voice_candidate") else {}),
                                              **({"allow_admin_test": True} if admin_authorization else {}))
    except KeyError:
        raise HTTPException(404, "unknown journey") from None
    except MemberScopeViolation:
        raise HTTPException(403, "member scoping violation") from None
    except InvalidAnswerReference:
        raise HTTPException(400, "answer reference is unavailable or invalid") from None
    except OutcomeConflict:
        raise HTTPException(409, "outcome or idempotency key already used") from None
    except JourneyBusy:
        raise HTTPException(409, "journey busy; retry with the same Idempotency-Key") from None
    except DeliveryPending:
        raise HTTPException(503, "Journey saved; delivery pending. Retry with the same Idempotency-Key.") from None
    except AuditUnavailable:
        raise HTTPException(503, domain.message("audit_paused")) from None
    except Exception as error:
        log.error("journey_persistence_failed", extra={"journey_id": journey_id,
                                                       "error_type": type(error).__name__})
        raise HTTPException(503, domain.message("journey_unavailable")) from None


@app.get("/healthz")
def healthz():
    return {"ok": True}


class BenchmarkTurn(BaseModel):
    model_config = {"extra": "forbid"}
    utterance: str
    model: Literal["gpt-counsellor", "gpt-compact"]
    cold: bool = False


@app.post("/voice/benchmark-turns/{journey_id}", include_in_schema=False)
def benchmark_turn(journey_id: str, body: BenchmarkTurn, request: Request,
                   authorization: str = Header(""),
                   idempotency_key: str = Header("", alias="Idempotency-Key")):
    _, identity = authenticate(authorization)
    if (settings.environment_name != "gg-dev"
            or os.getenv("GOVERNEDGRAPH_DEPLOY_VOICE_DEMO") != "true"
            or identity.member_ref != domain.demo()["member_ref"] or not identity.step_up_verified):
        raise HTTPException(403, "synthetic voice comparison disabled")
    request.state.voice_candidate = body.model
    def measured_turn():
        return turn(journey_id, Turn(utterance=body.utterance), request,
                    authorization=authorization, traceparent_header="", idempotency_key=idempotency_key)
    if body.cold:
        from governedgraph.agents.connections import cold_clients

        with cold_clients():
            return measured_turn()
    return measured_turn()


@app.post("/voice/simulations")
async def voice_simulation(body: dict, authorization: str = Header("")):
    token, identity = authenticate(authorization)
    try:
        script = VoiceScriptRequest.from_dict(body)
    except (ContractError, TypeError):
        raise HTTPException(400, "invalid closed voice script") from None
    try:
        async with asyncio.timeout(180):
            result = await run_hosted(script, token, identity)
        return JSONResponse(result, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    except PermissionError:
        raise HTTPException(403, "hosted voice proof not authorized") from None
    except (VoiceError, httpx.HTTPError, OSError, TimeoutError, KeyError) as error:
        frame = traceback.extract_tb(error.__traceback__)[-1]
        log.error("hosted_voice_failed", extra={
            "error_type": type(error).__name__, "failure_domain": f"voice.{frame.name}.L{frame.lineno}",
        })
        raise HTTPException(503, "Speech unavailable; use the text channel or contact an advisor.") from None


@app.get("/members/{member_ref}/explainability-report")
def explainability_report(member_ref: str, request: Request, authorization: str = Header("")):
    _, identity = authenticate(authorization)
    try:
        report = request.app.state.runtime.explainability_report(member_ref, identity)
        return JSONResponse(report, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    except MemberScopeViolation:
        raise HTTPException(403, "step-up verified access to your own report required") from None
    except JourneyBusy:
        raise HTTPException(409, "journey busy; retry the report") from None
    except Exception as error:
        log.error("explainability_report_unavailable", extra={"error_type": type(error).__name__})
        raise HTTPException(503, "Explainability report temporarily unavailable") from None


@app.get("/journeys/{journey_id}/advisor-queue")
def advisor_queue(journey_id: str, request: Request, authorization: str = Header("")):
    _, identity = authenticate(authorization)
    try:
        validate_id(journey_id)
        result = request.app.state.runtime.advisor_queue(journey_id, identity)
        if "text/html" in request.headers.get("accept", ""):
            return HTMLResponse(advisor_queue_html(result["handovers"]))
        return result
    except ValueError:
        raise HTTPException(400, "invalid journey identifier") from None
    except KeyError:
        raise HTTPException(404, "unknown journey") from None
    except MemberScopeViolation:
        raise HTTPException(403, "member scoping violation") from None
    except JourneyBusy:
        raise HTTPException(409, "journey busy") from None
    except Exception as error:
        log.error("advisor_queue_unavailable", extra={"journey_id": journey_id,
                                                      "error_type": type(error).__name__})
        raise HTTPException(503, "advisor queue unavailable") from None
