"""Production-shaped managed-identity port; dev routes it to the synthetic stand-in."""
from __future__ import annotations

from dataclasses import asdict
from urllib.parse import urlparse

import httpx

from governedgraph.common.contracts import EscalationRequest
from governedgraph.contact_centre.service import validate_escalation
from governedgraph.orchestrator.store import validate_id


class ContactCentreClient:
    def __init__(self, url: str, audience: str, *, credential=None, client=None):
        if urlparse(url).scheme != "https" or not audience:
            raise ValueError("contact-centre HTTPS URL and audience are required")
        self.url, self.audience = url.rstrip("/"), audience
        self.credential = credential
        self.client = client or httpx

    def _headers(self) -> dict:
        if self.credential is None:
            from azure.identity import DefaultAzureCredential

            self.credential = DefaultAzureCredential()
        token = self.credential.get_token(f"{self.audience}/.default").token
        return {"Authorization": f"Bearer {token}"}

    def handover(self, request: EscalationRequest, idempotency_key: str) -> str:
        validate_escalation(request)
        if request.escalation_id != idempotency_key:
            raise ValueError("handover idempotency key mismatch")
        response = self.client.post(
            f"{self.url}/handovers", json=asdict(request),
            headers={**self._headers(), "Idempotency-Key": idempotency_key}, timeout=15,
        )
        response.raise_for_status()
        body = response.json()
        if (not isinstance(body, dict) or set(body) != {"handover_id", "data"}
                or not isinstance(body["handover_id"], str) or not body["handover_id"]):
            raise ValueError("invalid contact-centre acknowledgment")
        validate_id(body["handover_id"])
        return body["handover_id"]

    def advisor_queue(self, journey_id: str) -> list[dict]:
        validate_id(journey_id)
        response = self.client.get(f"{self.url}/advisor-queue", params={"journey_id": journey_id},
                                   headers=self._headers(), timeout=15)
        response.raise_for_status()
        body = response.json()
        if (not isinstance(body, dict) or set(body) != {"data", "handovers"}
                or not isinstance(body["handovers"], list)
                or any(row.get("journey_id") != journey_id for row in body["handovers"])):
            raise ValueError("invalid advisor queue response")
        return body["handovers"]
