"""Authenticated, synthetic selected-style sessions using the existing Azure channel."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import os
import time

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from governedgraph.admin.access import AccessPolicy
from governedgraph.common.config import settings
from governedgraph.common.contracts import CONTACT_TYPES, ContractError, RequestKey, _strict
from governedgraph.agents.clients import HttpStrategyClient
from governedgraph.voice.presenter import SessionRequest, serve
from governedgraph.voice.simulations import load_simulations
from governedgraph.voice.voice_live_bridge import JourneyStart, OrchestratorChannel, VoiceError

router = APIRouter()
COOKIE = "__Secure-gg-admin-test"
log = logging.getLogger("governedgraph.admin")


def require_testing() -> None:
    if settings.environment_name != "gg-dev" or os.getenv("GOVERNEDGRAPH_ADMIN_TESTING") != "true":
        raise HTTPException(404, "Synthetic admin testing is disabled.")
    if not all(os.getenv(name) for name in
               ("AZURE_CLIENT_ID", "VOICE_DEMO_ORIGIN", "VOICE_DEMO_CLIENT_ID", "VOICE_DEMO_ISSUER_URL",
                "GOVERNEDGRAPH_ORCHESTRATOR_URL")):
        raise HTTPException(503, "The Azure synthetic testing channel is not configured.")


def require_origin(origin: str) -> None:
    require_testing()
    if origin != os.environ["VOICE_DEMO_ORIGIN"]:
        raise HTTPException(403, "The administration origin is required.")


def require_style(strategy_id: str) -> None:
    catalogue, _ = HttpStrategyClient().get_catalogue()
    if not isinstance(strategy_id, str) or strategy_id not in {
            style.strategy_id for style in catalogue.all() if style.status == "active"}:
        raise ContractError("choose an approved explanation style")


@dataclass(frozen=True)
class TestSession:
    strategy_id: str
    mode: str
    simulation_id: str | None = None
    contact_type: str = "first_contact"

    def __post_init__(self):
        if self.mode not in ("chat", "presenter", "simulator"):
            raise ContractError("choose chat, voice or a persona")
        require_style(self.strategy_id)
        SessionRequest(self.mode, simulation_id=self.simulation_id, contact_type=self.contact_type)
        if self.mode == "simulator" and self.simulation_id is None:
            raise ContractError("choose an approved synthetic persona")

    @classmethod
    def from_dict(cls, payload: dict) -> TestSession:
        return _strict(cls, payload)


@dataclass(frozen=True)
class TestJourney:
    strategy_id: str
    channel: str
    accessibility_mode: str
    contact_type: str = "first_contact"

    def __post_init__(self):
        require_style(self.strategy_id)
        if not isinstance(self.contact_type, str) or self.contact_type not in CONTACT_TYPES:
            raise ContractError("unsupported contact type")
        RequestKey(self.contact_type, "unclassified", self.channel, self.accessibility_mode)
        if self.channel not in ("chat", "voice"):
            raise ContractError("unsupported synthetic test channel")

    @classmethod
    def from_dict(cls, payload: dict) -> TestJourney:
        return _strict(cls, payload)


class TestChannel(OrchestratorChannel):
    def __init__(self, client, token, strategy_id: str, admin_token: str):
        super().__init__(client, token)
        self.strategy_id = strategy_id
        self.admin_token = admin_token

    def _headers(self) -> dict[str, str]:
        return {**super()._headers(), "X-Admin-Authorization": "Bearer " + self.admin_token}

    async def start(self, accessibility_mode: str = "standard", *, channel: str = "voice",
                    contact_type: str = "first_contact") -> JourneyStart:
        body = TestJourney(self.strategy_id, channel, accessibility_mode, contact_type)
        result = await self.client.post("admin-test/journeys", headers=self._headers(),
                                        json={"strategy_id": body.strategy_id, "channel": body.channel,
                                              "accessibility_mode": body.accessibility_mode,
                                              **({"contact_type": body.contact_type}
                                                 if body.contact_type != "first_contact" else {})})
        result.raise_for_status()
        return JourneyStart.from_dict(result.json())


@router.get("/admin-testing/config")
def config(authorization: str = Header("")):
    require_testing()
    AccessPolicy.from_env().require(authorization, act=True)
    return {"simulations": [simulation.summary() for simulation in load_simulations()],
            "watermark": "ILLUSTRATIVE - synthetic data"}


@router.post("/admin-testing/authorize")
def authorize(request: Request, authorization: str = Header("")):
    require_origin(request.headers.get("origin", ""))
    identity = AccessPolicy.from_env().require(authorization, act=True)
    response = JSONResponse({"authorized": True}, headers={"Cache-Control": "no-store"})
    response.set_cookie(COOKIE, authorization.removeprefix("Bearer "),
                        max_age=min(850, max(0, identity.expires_at - int(time.time()))),
                        secure=True, httponly=True, samesite="strict", path="/admin-test-stream")
    return response


@router.post("/admin-test/journeys")
async def start(request: Request, authorization: str = Header(""),
                admin_authorization: str = Header("", alias="X-Admin-Authorization")):
    from governedgraph.common import domain
    from governedgraph.orchestrator.api import authenticate

    require_testing()
    AccessPolicy.from_env().require(admin_authorization, act=True)
    _, identity = authenticate(authorization)
    permitted = {domain.demo()["member_ref"], *(simulation.member_ref for simulation in load_simulations())}
    if not identity.step_up_verified or identity.member_ref not in permitted:
        raise HTTPException(403, "Synthetic step-up verified members only.")
    try:
        body = TestJourney.from_dict(await request.json())
    except (ContractError, TypeError, json.JSONDecodeError):
        raise HTTPException(400, "Invalid closed selected-style test request.") from None
    return await asyncio.to_thread(request.app.state.runtime.start, identity, body.channel,
                                   body.accessibility_mode, test_strategy_id=body.strategy_id,
                                   contact_type=body.contact_type)


@router.websocket("/admin-test-stream")
async def stream(socket: WebSocket):
    try:
        require_origin(socket.headers.get("origin", ""))
        if socket.query_params:
            raise HTTPException(400, "Query credentials are forbidden.")
        token = socket.cookies.get(COOKIE, "")
        identity = await asyncio.to_thread(AccessPolicy.from_env().require, "Bearer " + token, act=True)
    except HTTPException:
        await socket.close(code=1008)
        return
    await socket.accept()
    try:
        selected = TestSession.from_dict(await asyncio.wait_for(socket.receive_json(), 20))
        request = SessionRequest(selected.mode, simulation_id=selected.simulation_id,
                                 contact_type=selected.contact_type)
        await serve(socket, {"exp": identity.expires_at}, request=request,
                    channel_factory=lambda client, bearer: TestChannel(client, bearer, selected.strategy_id, token))
    except (VoiceError, httpx.HTTPError, ContractError, TimeoutError, OSError,
            WebSocketDisconnect, json.JSONDecodeError, KeyError, ValueError) as error:
        log.error("admin_test_failed", extra={"error_type": type(error).__name__})
        if socket.client_state.name == "CONNECTED":
            await socket.send_json({"kind": "error", "message": "The selected-style test failed. Check operator diagnostics."})
    finally:
        if socket.client_state.name == "CONNECTED":
            await socket.close()
