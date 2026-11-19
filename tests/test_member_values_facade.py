import base64
import json
import logging
import time
from dataclasses import fields
from pathlib import Path
from unittest.mock import Mock
from xml.etree import ElementTree

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

from governedgraph.agents.clients import ApimValuesClient
from counsellor_doubles import install, new_session
from governedgraph.agents.counsellor import DISCLOSURE, Counsellor
from governedgraph.agents.foundry_tracing import journey_trace_id
from governedgraph.contact_centre.service import Handover, SyntheticContactCentre
from governedgraph.common.config import settings
from governedgraph.common.contracts import MemberValue
from governedgraph.common.member_auth import verify_member_token
from governedgraph.common.tracing import traceparent
from governedgraph.dev.issue_member_token import issue_token
from pension_wtp.member_values_api.app import app as values_app
from governedgraph.orchestrator import api as journeys
from governedgraph.orchestrator.consumer import InboxRecord, JourneyConsumer
from governedgraph.orchestrator.messaging import FileJourneyPublisher
from governedgraph.orchestrator.service import OrchestratorService
from governedgraph.orchestrator.store import FileJourneyStore, FileRecordStore
from governedgraph.orchestrator.worker import FileJourneyWorker
from strategy_doubles import local_strategy_service, run_cycle

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def member_tokens(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())
    monkeypatch.setattr(settings.__class__, "member_token_issuer",
                        property(lambda self: "https://synthetic.example/gg-dev"))
    monkeypatch.setattr(settings.__class__, "member_token_public_jwk", property(lambda self: public))
    monkeypatch.setattr(settings.__class__, "member_token_audience", property(lambda self: "gg-apim"))
    monkeypatch.setenv("GOVERNEDGRAPH_ENABLE_DEV_MEMBER_ISSUER", "true")

    class Signer:
        def sign(self, data: bytes) -> str:
            signature = key.sign(data, padding.PKCS1v15(), hashes.SHA256())
            return base64.urlsafe_b64encode(signature).rstrip(b"=").decode()

    def token(member="MBR-7Q2K", **kwargs):
        return issue_token(Signer(), member, settings.member_token_issuer, **kwargs)

    token.sign_claims = lambda claims: jwt.encode(claims, key, algorithm="RS256")
    return token


@pytest.fixture
def local_runtime(tmp_path, monkeypatch, isolated_conversations):
    """The journey API over file-backed stores and a synthetic contact centre instead of Azure storage and Service Bus."""
    install(monkeypatch, tmp_path)
    strategies = local_strategy_service(tmp_path / "strategy", control_share=0)
    run_cycle(strategies)
    centre = SyntheticContactCentre(FileRecordStore(tmp_path / "handovers", Handover.decode))
    consumer = JourneyConsumer(FileRecordStore(tmp_path / "inbox", InboxRecord.decode), centre)

    def factory(journey, auth_ref, **kwargs):
        return Counsellor(journey, auth_ref, strategy_service=strategies, **kwargs)

    monkeypatch.setattr(journeys, "create_runtime", lambda: OrchestratorService(
        FileJourneyStore(tmp_path / "journeys"), FileJourneyPublisher(tmp_path / "queues"), factory,
        contact_centre=centre, worker=FileJourneyWorker(tmp_path / "queues", consumer),
        continuity=isolated_conversations))


def test_b1_real_rsa_token_requires_verified_stepup_and_short_expiry(member_tokens):
    assert verify_member_token(member_tokens(), require_step_up=True).member_ref == "MBR-7Q2K"
    assert verify_member_token(member_tokens("MBR-3H8D"), require_step_up=True).member_ref == "MBR-3H8D"
    assert not verify_member_token(member_tokens(step_up=False)).step_up_verified
    for token in (
        member_tokens(step_up=False),
        member_tokens(now=int(time.time()) - 1800),
        member_tokens(audience="wrong"),
        member_tokens()[:-5] + "wrong",
    ):
        with pytest.raises(jwt.InvalidTokenError):
            verify_member_token(token, require_step_up=True)


def test_b1_issuer_is_disabled_unless_explicitly_enabled(monkeypatch, member_tokens):
    monkeypatch.delenv("GOVERNEDGRAPH_ENABLE_DEV_MEMBER_ISSUER")
    with pytest.raises(PermissionError):
        member_tokens()


def test_b1_dev_issuer_refuses_long_lived_tokens(member_tokens):
    with pytest.raises(ValueError):
        member_tokens(lifetime=901)


@pytest.mark.parametrize("change", [
    {"member_ref": ""}, {"member_ref": None}, {"member_ref": ["MBR-7Q2K"]},
    {"iss": "https://wrong.invalid"}, {"exp": None}, {"iat": None},
    {"exp": "not-a-timestamp"}, {"age_band": "75+"},
])
def test_b1_b7_invalid_or_demographic_identity_claims_are_rejected(member_tokens, change):
    now = int(time.time())
    claims = {"iss": settings.member_token_issuer, "aud": "gg-apim", "member_ref": "MBR-7Q2K",
              "iat": now, "exp": now + 300, "acr": "stepup"}
    claims.update(change)
    with pytest.raises(jwt.InvalidTokenError):
        verify_member_token(member_tokens.sign_claims(claims), require_step_up=True)


def test_b1_signed_overlong_lifetime_is_rejected(member_tokens):
    now = int(time.time())
    claims = {"iss": settings.member_token_issuer, "aud": "gg-apim", "member_ref": "MBR-7Q2K",
              "iat": now, "exp": now + 901, "acr": "stepup"}
    with pytest.raises(jwt.InvalidTokenError):
        verify_member_token(member_tokens.sign_claims(claims), require_step_up=True)


def test_b1_apim_client_forwards_token_not_member_header(member_tokens, monkeypatch, caplog):
    token = member_tokens()
    monkeypatch.setattr(settings.__class__, "member_values_url", property(lambda self: "https://synthetic.example"))
    response = httpx.Response(200, json={"member_ref": "MBR-7Q2K", "values": []},
                              request=httpx.Request("GET", "https://synthetic.example"))
    get = Mock(return_value=response)
    monkeypatch.setattr("governedgraph.agents.connections.http_client", lambda: Mock(get=get))
    parent = traceparent()
    with caplog.at_level(logging.INFO):
        ApimValuesClient(token).get("MBR-7Q2K", "MBR-7Q2K", journey_id="J-1", trace_parent=parent)
    headers = get.call_args.kwargs["headers"]
    assert headers["Authorization"] == f"Bearer {token}"
    assert "X-Authenticated-Member" not in headers
    assert headers["X-Journey-ID"] == "J-1"
    assert headers["traceparent"] == f"00-{journey_trace_id('J-1'):032x}-{parent.split('-')[2]}-01"
    assert any(record.journey_id == "J-1" for record in caplog.records)
    assert token not in caplog.text


def test_b1_values_client_rejects_cross_member_and_foreign_response(member_tokens, monkeypatch, caplog):
    client = ApimValuesClient(member_tokens())
    with caplog.at_level(logging.ERROR), pytest.raises(PermissionError):
        client.get("MBR-3H8D", "MBR-7Q2K", journey_id="J-CROSS")
    assert caplog.records[-1].message == "member_scope_violation"
    assert caplog.records[-1].journey_id == "J-CROSS"
    response = httpx.Response(200, json={"member_ref": "MBR-3H8D", "values": []},
                              request=httpx.Request("GET", "https://synthetic.example"))
    monkeypatch.setattr("governedgraph.agents.connections.http_client", lambda: Mock(get=Mock(return_value=response)))
    with pytest.raises(PermissionError):
        client.get("MBR-7Q2K", "MBR-7Q2K", journey_id="J-FOREIGN")


def test_b1_backend_two_members_403_incident_and_post_405(caplog):
    with TestClient(values_app) as client:
        for member in ("MBR-7Q2K", "MBR-3H8D"):
            response = client.get(f"/members/{member}/values", headers={"X-Authenticated-Member": member})
            assert response.status_code == 200 and response.json()["member_ref"] == member
        parent = traceparent()
        with caplog.at_level(logging.ERROR):
            response = client.get("/members/MBR-3H8D/values",
                                  headers={"X-Authenticated-Member": "MBR-7Q2K",
                                           "X-Journey-ID": "J-SCOPE", "traceparent": parent})
        assert response.status_code == 403
        incident = next(record for record in caplog.records if record.message == "member_scope_violation")
        assert incident.journey_id == "J-SCOPE" and incident.traceparent == parent
        assert "MBR-" not in caplog.text
        assert client.post("/members/MBR-7Q2K/values").status_code == 405


def test_b1_b9_circuit_open_escalates_without_estimation(tmp_path, monkeypatch):
    install(monkeypatch, tmp_path)
    counsellor = new_session("MBR-7Q2K", channel="chat")
    counsellor.tools.values_client = Mock()
    counsellor.tools.values_client.get.side_effect = httpx.HTTPStatusError(
        "circuit open", request=httpx.Request("GET", "https://synthetic.example"),
        response=httpx.Response(503))
    counsellor.tools.strategy_client = Mock()
    counsellor.start()
    result = counsellor.handle("Will the transition change what I receive?")
    assert result.escalated and result.strategy_id == "S6"
    assert result.state == "escalation_requested"
    assert counsellor.tools.trace.values is None
    assert counsellor.tools.trace.escalations[-1].reason_code == "component_failure"
    assert not any(character.isdigit() for character in result.text)
    counsellor.tools.strategy_client.get_ranked.assert_not_called()


def test_b1_without_stepup_never_reads_personal_values(tmp_path, monkeypatch):
    install(monkeypatch, tmp_path)
    counsellor = new_session("MBR-7Q2K", step_up_verified=False)
    counsellor.tools.values_client = Mock()
    counsellor.start()
    result = counsellor.handle("Will the transition change what I receive?")
    counsellor.tools.values_client.get.assert_not_called()
    assert "step-up" in result.text and "advisor" in result.text
    assert counsellor.tools.trace.values is None
    assert not any(character.isdigit() for character in result.text)


def test_b1_journey_identity_from_token_and_no_cross_member_turn(member_tokens, local_runtime, caplog):
    with TestClient(journeys.app) as client:
        headers = {"Authorization": f"Bearer {member_tokens()}"}
        assert client.post("/journeys", json={}).status_code == 401
        assert client.post("/journeys", json={"member_ref": "MBR-3H8D"}, headers=headers).status_code == 422
        response = client.post("/journeys", json={}, headers=headers)
        assert response.status_code == 200
        journey_id = response.json()["journey_id"]
        with client.app.state.runtime.store.transaction(journey_id) as transaction:
            assert transaction.record.journey.member_ref == "MBR-7Q2K"
        with caplog.at_level(logging.WARNING):
            response = client.post(f"/journeys/{journey_id}/turns", json={"utterance": "Hello"},
                                   headers={"Authorization": f"Bearer {member_tokens('MBR-3H8D')}"})
        assert response.status_code == 403
        assert any(record.message == "customer_context_access_denied" and record.journey_id == journey_id
                   for record in caplog.records)


def test_b1_openapi_is_get_only_and_matches_official_value_contract():
    spec = json.loads((ROOT / "examples/pension_wtp/docs/api/member-values.openapi.yaml").read_text())
    assert spec["openapi"] == "3.1.0"
    assert set(spec["paths"]["/members/{memberRef}/values"]) == {"get"}
    schema = spec["components"]["schemas"]["MemberValue"]
    assert set(schema["required"]) == {field.name for field in fields(MemberValue)}
    assert schema["additionalProperties"] is False
    assert spec["components"]["schemas"]["MemberValues"]["additionalProperties"] is False


def test_b1_durable_api_restart_advisor_queue_is_member_scoped(member_tokens, local_runtime, monkeypatch):

    async def paused_recovery(runtime, stopped):
        await stopped.wait()

    monkeypatch.setattr(journeys, "recover", paused_recovery)
    headers = {"Authorization": f"Bearer {member_tokens()}"}
    with TestClient(journeys.app) as client:
        started = client.post("/journeys", json={}, headers=headers).json()
        journey_id = started["journey_id"]
        assert started["text"] == DISCLOSURE
        advice = client.post(f"/journeys/{journey_id}/turns",
                             json={"utterance": "Which pension option should I choose?"},
                             headers={**headers, "Idempotency-Key": "synthetic-advice"})
        assert advice.status_code == 200 and advice.json()["escalated"]
        client.app.state.runtime.recover()
    with TestClient(journeys.app) as restarted:
        replay = restarted.post(f"/journeys/{journey_id}/turns",
                                json={"utterance": "not persisted"},
                                headers={**headers, "Idempotency-Key": "synthetic-advice"})
        assert replay.json()["duplicate"] is True
        response = restarted.get(f"/journeys/{journey_id}/advisor-queue", headers=headers)
        assert response.status_code == 200 and len(response.json()["handovers"]) == 1
        html_view = restarted.get(f"/journeys/{journey_id}/advisor-queue",
                                  headers={**headers, "Accept": "text/html"})
        assert html_view.status_code == 200 and "No live callbacks" in html_view.text
        wrong = restarted.get(f"/journeys/{journey_id}/advisor-queue",
                               headers={"Authorization": f"Bearer {member_tokens('MBR-3H8D')}"})
        assert wrong.status_code == 403
        assert restarted.get(f"/journeys/{journey_id}/advisor-queue").status_code == 401


def test_b1_apim_policy_overwrites_spoofed_header_and_logs_cross_member_incident():
    inbound = ElementTree.parse(ROOT / "infra/policies/member-values.xml").getroot().find("inbound")
    header = inbound.find("set-header[@name='X-Authenticated-Member']")
    assert header.attrib["exists-action"] == "override"
    assert 'context.Variables["jwt"]' in header.find("value").text
    assert "context.Request.Headers" not in header.find("value").text
    cross_member = next(when for when in inbound.findall("choose/when")
                        if "MatchedParameters" in when.attrib["condition"])
    assert cross_member.find("return-response/set-status").attrib["code"] == "403"
    assert cross_member.find("trace/message").text == "member_scope_violation"
    assert cross_member.find("trace/metadata").attrib["name"] == "journey_id"
    validation = inbound.find("validate-jwt")
    assert validation.attrib["clock-skew"] == "0"
    assert validation.find("required-claims/claim[@name='acr']/value").text == "stepup"


def test_b1_retry_is_get_only_and_telemetry_never_captures_payloads_or_tokens():
    policy = ElementTree.parse(ROOT / "infra/policies/member-values.xml").getroot()
    retry = policy.find("backend/retry")
    assert 'context.Request.Method == "GET"' in retry.attrib["condition"]
    assert "context.Response == null" in retry.attrib["condition"]
    source = (ROOT / "infra/modules/gateway.bicep").read_text()
    diagnostic = source.split("resource valuesDiagnostics ", 1)[1].split("\nresource ", 1)[0]
    assert "percentage: 100" in diagnostic
    assert "httpCorrelationProtocol: 'W3C'" in diagnostic
    assert diagnostic.count("body: { bytes: 0 }") == 4
    assert "Authorization" not in diagnostic and "X-Authenticated-Member" not in diagnostic
    assert "identityClientId: 'SystemAssigned'" in source
    assert "count: 5" in source and "tripDuration: 'PT1M'" in source
    issuer = (ROOT / "infra/modules/dev-member-issuer.bicep").read_text()
    assert "{ name: 'AZURE_CLIENT_ID', value: issuerClientId }" in issuer
    assert "'${issuerIdentityId}': {}" in issuer
    assert "pipelineIdentity" not in issuer
    assert "runtimeIdentity" not in issuer


def test_b1_trace_context_rejects_zero_and_invalid_identifiers():
    for parent in ("", "not-a-trace", f"00-{'0' * 32}-{'1' * 16}-01"):
        assert traceparent(parent) != parent
    parent = f"00-{'a' * 32}-{'b' * 16}-01"
    assert traceparent(parent) == parent
