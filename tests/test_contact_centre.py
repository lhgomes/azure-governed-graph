"""ILLUSTRATIVE - synthetic data: runtime-only auth and duplicate-safe handovers."""
from dataclasses import asdict, replace
import importlib.util
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient

from governedgraph.common.config import settings
from governedgraph.common.contracts import EscalationRequest
from governedgraph.common.operational_logging import OperationalFormatter
from governedgraph.contact_centre import api
from governedgraph.contact_centre.client import ContactCentreClient
from governedgraph.contact_centre.service import HANDOVER_SUMMARY, Handover, SyntheticContactCentre, advisor_queue_html
from governedgraph.orchestrator.store import FileRecordStore

ROOT = Path(__file__).resolve().parents[1]


def escalation():
    return EscalationRequest("J-synthetic", "advice_intent", HANDOVER_SUMMARY, ["current_monthly_pension"],
                             escalation_id="J-synthetic:escalation:1")


def test_b9_synthetic_http_handover_reuses_original_and_is_read_only(monkeypatch, tmp_path):
    monkeypatch.setattr(settings.__class__, "run_dir", property(lambda self: tmp_path))
    api.app.dependency_overrides[api.require_runtime] = lambda: None
    try:
        with TestClient(api.app) as client:
            request = escalation()
            headers = {"Idempotency-Key": request.escalation_id}
            first = client.post("/handovers", json=asdict(request), headers=headers)
            second = client.post("/handovers", json=asdict(request), headers=headers)
            assert first.status_code == second.status_code == 200 and first.json() == second.json()
            rows = client.get("/advisor-queue", params={"journey_id": request.journey_id}).json()["handovers"]
            assert len(rows) == 1 and rows[0]["status"] == "synthetic_pending"
            assert rows[0]["value_ids"] == ["current_monthly_pension"]
            assert "ILLUSTRATIVE - synthetic data" in rows[0]["data"]
            assert client.post("/advisor-queue", json={}).status_code == 405
            assert client.post("/handovers", headers=headers,
                               json=asdict(replace(request, reason_code="safety"))).status_code == 400
    finally:
        api.app.dependency_overrides.clear()
    centre = SyntheticContactCentre(FileRecordStore(tmp_path / "handovers", Handover.decode))
    assert len(centre.advisor_queue("J-synthetic")) == 1


@pytest.mark.parametrize("change", [
    {"context_summary": "Member utterance with EUR 1,412.50"},
    {"value_ids": ["EUR 1,412.50"]},
    {"callback_preference": "infer from demographic profile"},
    {"created_at": "age 75"}, {"created_at": None}, {"reason_code": []},
    {"escalation_id": None},
    {"age_band": "75+"}, {"utterance": "private"}, {"member_ref": "MBR-7Q2K"},
])
def test_b1_b4_b7_handover_rejects_raw_content_figures_and_unknown_fields(change, tmp_path):
    centre = SyntheticContactCentre(FileRecordStore(tmp_path, Handover.decode))
    body = {**asdict(escalation()), **change}
    with pytest.raises(ValueError):
        centre.handover(EscalationRequest.from_dict(body), body["escalation_id"])
    assert list(tmp_path.glob("*.json")) == []


def test_only_runtime_application_token_can_access_mock_contact_centre(monkeypatch):
    import time

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    tenant, client, runtime = str(uuid4()), str(uuid4()), str(uuid4())
    monkeypatch.setattr(api, "settings", SimpleNamespace(contact_centre_client_id=client,
                                                        strategy_tenant_id=tenant, strategy_runtime_id=runtime))
    monkeypatch.setattr(api, "signing_keys", lambda tenant: SimpleNamespace(
        get_signing_key_from_jwt=lambda token: SimpleNamespace(key=key.public_key())))
    now = int(time.time())
    claims = {"exp": now + 300, "iat": now, "aud": client,
              "iss": f"https://login.microsoftonline.com/{tenant}/v2.0",
              "tid": tenant, "oid": runtime, "idtyp": "app", "roles": ["ContactCentre.Access"]}

    def auth(changes):
        return api.require_runtime("Bearer " + jwt.encode({**claims, **changes}, key, algorithm="RS256"))

    auth({})
    for changes, status in [
        ({"oid": str(uuid4())}, 403), ({"aud": str(uuid4())}, 401),
        ({"iss": "https://untrusted.invalid"}, 401), ({"exp": now - 10}, 401),
        ({"roles": []}, 403), ({"roles": "ContactCentre.Access"}, 403),
        ({"idtyp": "user", "scp": "ContactCentre.Access"}, 403),
    ]:
        with pytest.raises(HTTPException) as error:
            auth(changes)
        assert error.value.status_code == status
    with pytest.raises(HTTPException) as error:
        api.require_runtime("")
    assert error.value.status_code == 401


def test_contact_centre_adapter_uses_managed_token_and_domain_idempotency_key():
    credential = Mock()
    credential.get_token.return_value.token = "synthetic-test-only"
    client = Mock()
    client.post.return_value.json.return_value = {"handover_id": "HO-synthetic",
                                                 "data": "ILLUSTRATIVE - synthetic data"}
    adapter = ContactCentreClient("https://synthetic.internal", "api://synthetic",
                                   credential=credential, client=client)
    request = escalation()
    assert adapter.handover(request, request.escalation_id) == "HO-synthetic"
    assert client.post.call_args.kwargs["headers"]["Idempotency-Key"] == request.escalation_id
    credential.get_token.assert_called_once_with("api://synthetic/.default")
    assert set(client.post.call_args.kwargs["json"]) == set(asdict(request))


def test_b9_alert_rules_preserve_queue_properties_and_route_to_oncall():
    data = (ROOT / "infra/modules/data.bicep").read_text()
    for resource in ("qJourney", "qEsc"):
        queue = data.split(f"resource {resource} ", 1)[1].split("\nresource ", 1)[0]
        assert "requiresDuplicateDetection: false" in queue
    alerts = (ROOT / "infra/modules/alerts.bicep").read_text()
    assert "emailAddress: onCallEmail" in alerts
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", alerts), "no literal address may be committed"
    assert "severity: queue == 'escalations' ? 1 : 2" in alerts
    for signal in ("DeadletteredMessages", "EntityName", "age_seconds", "> 300",
                   "explainability_record_write_failed", "journey_outbox_delivery_failed",
                   "prompt_shields_unavailable", "strategy_frozen", "strategy_static_fallback",
                   '!= "control"'):
        assert signal in alerts
    assert "actionGroupId: onCall.id" in alerts and "actionGroups: [ onCall.id ]" in alerts
    compute = (ROOT / "infra/modules/compute.bicep").read_text()
    contact = compute.split("resource contact ", 1)[1].split("\noutput ", 1)[0]
    assert "external: false" in contact and "allowInsecure: false" in contact
    assert "userAssignedIdentities: { '${contactCentreIdentityId}': {} }" in contact
    assert "AZURE_KEY_VAULT_URL" not in contact and "MEMBER_VALUES_API_URL" not in contact


def test_b4_operational_logs_exclude_utterances_tokens_and_figures():
    import logging

    record = logging.LogRecord("governedgraph", logging.ERROR, "synthetic", 1,
                               "journey_outbox_delivery_failed", (), None)
    record.journey_id, record.queue = "J-synthetic", "escalations"
    record.utterance, record.member_values, record.authorization = "private words", "EUR 1,412.50", "token"
    text = OperationalFormatter().format(record)
    assert json.loads(text)["queue"] == "escalations"
    assert not any(value in text for value in ("private words", "1,412", "authorization", "token"))
    assert "&lt;script&gt;" in advisor_queue_html([{
        "handover_id": "<script>", "escalation_id": "E", "journey_id": "J", "reason_code": "safety", "status": "pending",
    }])


def test_entra_registration_defaults_to_plan_without_graph_writes(monkeypatch):
    spec = importlib.util.spec_from_file_location("configure_contact_centre", ROOT / "scripts/configure_contact_centre.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    graph = Mock(side_effect=AssertionError("Graph called during plan"))
    monkeypatch.setattr(module, "graph", graph)
    result = module.configure("gg-dev", str(uuid4()))
    assert result["apply"] is False and result["credentials"] == "none"
    graph.assert_not_called()


def test_fresh_contact_centre_registration_has_a_stable_seed_scoped_name(monkeypatch):
    from scripts import configure_contact_centre

    monkeypatch.setattr(configure_contact_centre, "graph", lambda *args: pytest.fail("plan must not write"))
    runtime = str(uuid4())
    seed = "a" * 32
    first = configure_contact_centre.configure("gg-dev", runtime, naming_seed=seed)
    assert first["display_name"].endswith("-" + seed)
    assert configure_contact_centre.configure("gg-dev", runtime, naming_seed=seed) == first
    assert configure_contact_centre.configure("gg-dev", runtime, naming_seed="b" * 32)["display_name"] != first["display_name"]
