"""Managed-identity safety adapters; no conversation content in diagnostic logs."""
from __future__ import annotations

import logging
import time
from threading import Lock

from governedgraph.guardrails.prompt_safety import LocalPromptScreen, ScreenResult

log = logging.getLogger("governedgraph.safety")


class AzurePromptShields:
    def __init__(self, endpoint: str, *, credential=None, client=None):
        self.url = f"{endpoint.rstrip('/')}/contentsafety/text:shieldPrompt?api-version=2024-09-01"
        self.endpoint = endpoint
        self.credential = credential
        self.client = client
        self.local = LocalPromptScreen()
        self.journey_id = ""
        self.total_duration_ms = 0.0
        self._timing_lock = Lock()

    def screen(self, utterance: str, authenticated_member_ref: str,
               documents: list[str] | None = None) -> ScreenResult:
        started = time.perf_counter()
        try:
            return self._screen(utterance, authenticated_member_ref, documents)
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            with self._timing_lock:
                self.total_duration_ms += elapsed

    def _screen(self, utterance: str, authenticated_member_ref: str,
                documents: list[str] | None) -> ScreenResult:
        import httpx
        from azure.core.exceptions import AzureError
        from governedgraph.agents.connections import credential, http_client

        local = self.local.screen(utterance, authenticated_member_ref)
        if not local.allowed:
            return local
        documents = documents or []
        try:
            if not self.endpoint:
                raise ValueError("Content Safety endpoint is required")
            if self.credential is None:
                self.credential = credential()
            token = self.credential.get_token("https://cognitiveservices.azure.com/.default").token
            response = (self.client or http_client()).post(
                self.url, json={"userPrompt": utterance, "documents": documents},
                headers={"Authorization": f"Bearer {token}"}, timeout=8,
            )
            response.raise_for_status()
            body = response.json()
            user = False
            if utterance or "userPromptAnalysis" in body:
                user = body["userPromptAnalysis"]["attackDetected"]
            analyses = body["documentsAnalysis"] if documents else body.get("documentsAnalysis", [])
            if type(user) is not bool or not isinstance(analyses, list) or len(analyses) != len(documents):
                raise ValueError("incomplete Prompt Shields analysis")
            if any(not isinstance(d, dict) or type(d.get("attackDetected")) is not bool for d in analyses):
                raise ValueError("invalid Prompt Shields document analysis")
            if user:
                return ScreenResult(False, "prompt_shields_user_attack")
            if any(d["attackDetected"] for d in analyses):
                return ScreenResult(False, "prompt_shields_document_attack")
            return ScreenResult(True)
        except (AzureError, httpx.HTTPError, ValueError, TypeError, KeyError) as error:
            log.error("prompt_shields_unavailable",
                      extra={"journey_id": self.journey_id, "error_type": type(error).__name__})
            return ScreenResult(False, "screening_unavailable_fail_closed")
