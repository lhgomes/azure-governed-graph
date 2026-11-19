import base64
import json
import runpy
import sys
import time
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, Mock

import httpx
import pytest

from governedgraph.dev import issue_member_token as issuer

URL = "https://ca-gg-orchestrator-synthetic.internal.example.swedencentral.azurecontainerapps.io"


class SyntheticSigner:
    def sign(self, data: bytes) -> str:
        return "synthetic-not-an-authorizable-signature"


def channel_transport(failure="", control=False):
    journeys, requests, tokens = {}, {}, []

    def handle(request):
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if not token:
            return httpx.Response(200 if failure == "missing_token" else 401)
        tokens.append(token)
        claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
        is_report = request.url.path.endswith("/explainability-report")
        if not is_report:
            assert claims["acr"] == "stepup"
        assert 0 < claims["exp"] - claims["iat"] <= 900
        if claims["exp"] < time.time():
            return httpx.Response(200 if failure == "expired_token" else 401)
        if is_report:
            member = request.url.path.split("/")[-2]
            if member != claims["member_ref"]:
                return httpx.Response(200 if failure == "report_scope" else 403)
            if claims.get("acr") != "stepup":
                return httpx.Response(200 if failure == "report_stepup" else 403)
            rows = [{"journey_id": jid, "member_ref": member, "response_id": f"{2 * index + kind:032x}"}
                    for index, (jid, journey) in enumerate(journeys.items())
                    if journey["member"] == member for kind in (0, 1)]
            return httpx.Response(200, json={
                "member_ref": member, "records": rows[1:] if failure == "report_linkage" else rows,
                "delivered_response_ids": [row["response_id"] for row in rows],
            }, headers={} if failure == "report_cache" else {"Cache-Control": "no-store"})
        if request.url.path == "/journeys":
            posted = json.loads(request.content)
            assert posted in ({"channel": "voice", "accessibility_mode": "standard"},
                              {"channel": "voice", "accessibility_mode": "standard", "contact_type": "second_contact"})
            jid = f"J-PROBE{len(journeys)}"
            journeys[jid] = {"member": claims["member_ref"], "handovers": []}
            return httpx.Response(200, json={"journey_id": jid, "text": "You are speaking with an AI assistant."})
        jid = request.url.path.split("/")[2]
        journey = journeys[jid]
        if journey["member"] != claims["member_ref"]:
            return httpx.Response(200 if failure == "cross_member" else 403)
        if request.method == "GET":
            rows = journey["handovers"] * (2 if failure == "duplicate_handover" else 1)
            return httpx.Response(200, json={"handovers": rows})
        key = request.headers["Idempotency-Key"]
        if key in requests:
            return httpx.Response(200, json={"duplicate": failure != "duplicate_turn"})
        requests[key] = True
        text = json.loads(request.content)["utterance"]
        result = {"strategy_id": "S5", "ranked": ["S5", "S4", "S1"], "suspended": ["S2"], "arm": "treatment",
                  "escalated": False}
        if "recommend" in text:
            result.update(escalated=True, state="escalation_requested")
            journey["handovers"] = [{"reason_code": "advice_intent"}]
        elif "instructions" in text:
            result.update(escalated=True, blocked_reason=None if failure == "injection" else "input blocked: injection")
        elif "later years" in text and failure == "second_contact":
            result.update(strategy_id=None, ranked=[])
        elif "matches my yearly statement" in text and failure == "recap":
            result.update(strategy_id=None, ranked=[])
        else:
            if failure == "s5":
                result["strategy_id"] = "S9"
            if failure == "s2":
                result["suspended"] = []
            if control and jid == "J-PROBE1":
                result.update(arm="control", strategy_id="S1")
        return httpx.Response(200, json=result)

    return httpx.MockTransport(handle), tokens


@pytest.fixture(autouse=True)
def enable_synthetic_issuer(monkeypatch):
    monkeypatch.setenv("GOVERNEDGRAPH_ENABLE_DEV_MEMBER_ISSUER", "true")
    monkeypatch.setattr(issuer.time, "sleep", lambda _: None)


def test_b1_in_network_channel_checks_auth_scope_and_redacts_output(capsys):
    transport, tokens = channel_transport()
    with httpx.Client(transport=transport) as client:
        ids = issuer.probe_channel(SyntheticSigner(), "https://synthetic.invalid", URL, client)
    output = capsys.readouterr().out
    assert set(ids) == {"explanation", "advice", "injection"}
    for check in ("missing_token_rejected", "expired_token_rejected", "cross_member_path_rejected",
                  "served_strategy_selected", "s2_suspended", "exactly_one_advice_handover",
                  "injection_blocked_at_screening", "second_contact_answered", "recap_question_answered",
                  "member_report_response_linkage", "report_stepup_required", "member_report_scoped"):
        assert "PASS " + check in output
    assert all(line.startswith("PASS ") for line in output.splitlines())
    assert not any(token in output for token in tokens)
    assert "Will the transition" not in output and "Which pension" not in output
    assert "Bearer" not in output and "recommend" not in output


@pytest.mark.parametrize("failure", ["missing_token", "expired_token", "cross_member", "s5", "s2",
                                     "duplicate_turn", "duplicate_handover", "injection",
                                     "second_contact", "recap",
                                     "report_scope", "report_stepup", "report_cache", "report_linkage"])
def test_b1_probe_fails_the_exact_acceptance_check(failure, capsys):
    transport, tokens = channel_transport(failure)
    with httpx.Client(transport=transport) as client, pytest.raises(issuer.ProbeFailure):
        issuer.probe_channel(SyntheticSigner(), "https://synthetic.invalid", URL, client)
    output = capsys.readouterr().out
    assert "FAIL " in output
    assert not any(token in output for token in tokens)
    assert "recommend" not in output and "pension values" not in output


def test_b10_probe_observes_control_without_changing_assignment(capsys):
    transport, _ = channel_transport(control=True)
    with httpx.Client(transport=transport) as client:
        issuer.probe_channel(SyntheticSigner(), "https://synthetic.invalid", URL, client)
    output = capsys.readouterr().out
    assert "PASS control_stays_static" in output and "PASS served_strategy_selected" in output


def contended_transport(target, failures, *, status=409, body=None, malformed=False):
    transport, tokens = channel_transport()
    attempts = []

    def handle(request):
        response = transport.handle_request(request)
        if response.status_code != 200:
            return response
        duplicate = request.url.path.endswith("/turns") and response.json().get("duplicate") is True
        selected = duplicate if target == "duplicate" else request.url.path.endswith("/" + target)
        if selected:
            attempts.append(request)
            if len(attempts) <= failures:
                if malformed:
                    return httpx.Response(status, text="PRIVATE malformed error body")
                return httpx.Response(status, json=body or {
                    "detail": {
                        "duplicate": "journey busy; retry with the same Idempotency-Key",
                        "advisor-queue": "journey busy",
                        "explainability-report": "journey busy; retry the report",
                    }[target],
                })
        return response

    return httpx.MockTransport(handle), tokens, attempts


@pytest.mark.parametrize("target", ["duplicate", "advisor-queue", "explainability-report"])
def test_b1_b9_probe_retries_confirmed_busy_without_changing_member_or_request(target, monkeypatch, capsys):
    sleep = Mock()
    monkeypatch.setattr(issuer.time, "sleep", sleep)
    transport, tokens, attempts = contended_transport(target, 2)
    with httpx.Client(transport=transport) as client:
        issuer.probe_channel(SyntheticSigner(), "https://synthetic.invalid", URL, client,
                             apim_url="https://apim-gg-synthetic.azure-api.net")
    assert [call.args[0] for call in sleep.call_args_list] == [1, 2]
    assert len(attempts) == 3
    assert len({request.headers["Authorization"] for request in attempts}) == 1
    assert len({request.content for request in attempts}) == 1
    if target == "duplicate":
        assert len({request.headers["Idempotency-Key"] for request in attempts}) == 1
    output = capsys.readouterr().out
    assert "PASS duplicate_turn_deduplicated" in output
    assert "PASS exactly_one_advice_handover" in output
    assert "PASS member_report_response_linkage" in output
    assert "FAIL " not in output and not any(token in output for token in tokens)


def test_b9_probe_fails_persistent_contention_after_bounded_retries(monkeypatch, capsys):
    sleep = Mock()
    monkeypatch.setattr(issuer.time, "sleep", sleep)
    transport, tokens, attempts = contended_transport("duplicate", 100)
    with httpx.Client(transport=transport) as client, pytest.raises(issuer.ProbeFailure, match="turn_accepted"):
        issuer.probe_channel(SyntheticSigner(), "https://synthetic.invalid", URL, client)
    assert len(attempts) == 6 and [call.args[0] for call in sleep.call_args_list] == [1, 2, 4, 8, 16]
    output = capsys.readouterr().out
    assert "FAIL turn_accepted" in output and "http_status=409" in output
    assert "PASS duplicate_turn_deduplicated" not in output
    assert not any(token in output for token in tokens)


@pytest.mark.parametrize("status,body,malformed", [
    (400, None, False), (401, None, False), (403, None, False), (429, None, False),
    (500, None, False), (503, None, False),
    (409, {"detail": "PRIVATE unrelated conflict"}, False),
    (409, {"detail": ["PRIVATE invalid error schema"]}, False),
    (409, ["PRIVATE non-object error"], False),
    (409, None, True),
])
def test_b1_probe_does_not_retry_other_failures_or_print_error_bodies(status, body, malformed, monkeypatch, capsys):
    sleep = Mock()
    monkeypatch.setattr(issuer.time, "sleep", sleep)
    transport, tokens, attempts = contended_transport("duplicate", 100, status=status, body=body, malformed=malformed)
    with httpx.Client(transport=transport) as client, pytest.raises(issuer.ProbeFailure, match="turn_accepted"):
        issuer.probe_channel(SyntheticSigner(), "https://synthetic.invalid", URL, client)
    assert len(attempts) == 1
    sleep.assert_not_called()
    output = capsys.readouterr().out
    assert f"http_status={status}" in output and "PRIVATE" not in output
    assert not any(token in output for token in tokens)


def test_b7_probe_checks_member_report_through_existing_apim_without_blob_grants(capsys):
    transport, tokens = channel_transport()
    with httpx.Client(transport=transport) as client:
        issuer.probe_channel(SyntheticSigner(), "https://synthetic.invalid", URL, client,
                             apim_url="https://apim-gg-synthetic.azure-api.net")
    output = capsys.readouterr().out
    assert "PASS approved_report_gateway" in output and "PASS member_report_response_linkage" in output
    assert not any(token in output for token in tokens)


@pytest.mark.parametrize("url", ["http://apim-gg-synthetic.azure-api.net",
                                "https://unrelated.azure-api.net",
                                "https://apim-gg-synthetic.azure-api.net/path",
                                "https://apim-gg-synthetic.azure-api.net?query=secret"])
def test_b7_probe_rejects_unapproved_report_gateway_before_issuing_tokens(url):
    signer = MagicMock()
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("must not send"))) as client:
        with pytest.raises(issuer.ProbeFailure):
            issuer.probe_channel(signer, "https://synthetic.invalid", URL, client, apim_url=url)
    signer.sign.assert_not_called()


@pytest.mark.parametrize("url", ["http://ca-gg-orchestrator-example.azurecontainerapps.io",
                                "https://unrelated.example", URL + "/unexpected", URL + "?query=secret"])
def test_b1_probe_never_sends_a_token_to_an_unapproved_target(url, capsys):
    signer = SyntheticSigner()
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("must not send"))) as client:
        with pytest.raises(issuer.ProbeFailure):
            issuer.probe_channel(signer, "https://synthetic.invalid", url, client)
    assert capsys.readouterr().out == "FAIL approved_channel_endpoint\n"


def test_b1_probe_cli_is_hosted_only_and_has_no_token_file_mode(monkeypatch):
    monkeypatch.delenv("IDENTITY_ENDPOINT", raising=False)
    for args in (["--probe-channel", "--orchestrator-url", URL], ["--output", "member.jwt"]):
        monkeypatch.setattr(sys, "argv", ["issue_member_token", *args])
        with pytest.raises(SystemExit) as error:
            issuer.main()
        assert error.value.code == 2


def test_b1_hosted_probe_outputs_only_sanitized_failures(monkeypatch, capsys):
    class SyntheticAzureError(Exception):
        pass

    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://synthetic-identity")
    monkeypatch.setenv("AZURE_CLIENT_ID", "synthetic-dev-issuer")
    monkeypatch.setenv("AZURE_KEY_VAULT_URL", "https://synthetic.vault.azure.net")
    monkeypatch.setattr(sys, "argv", ["issuer", "--probe-channel", "--orchestrator-url", URL])
    identity = ModuleType("azure.identity")
    credential = MagicMock()
    identity.ManagedIdentityCredential = MagicMock(return_value=credential)
    errors = ModuleType("azure.core.exceptions")
    errors.AzureError = SyntheticAzureError
    monkeypatch.setitem(sys.modules, "azure.identity", identity)
    monkeypatch.setitem(sys.modules, "azure.core.exceptions", errors)
    signer = MagicMock(side_effect=SyntheticAzureError("secret token, utterance, figure"))
    monkeypatch.setattr(issuer, "KeyVaultTokenSigner", signer)
    with pytest.raises(SystemExit) as error:
        issuer.main()
    assert error.value.code == 1
    identity.ManagedIdentityCredential.assert_called_once_with(client_id="synthetic-dev-issuer")
    assert signer.call_args.kwargs["credential"] is credential
    assert capsys.readouterr().out == "FAIL channel_probe error_type=SyntheticAzureError\n"


def test_b1_voice_entrypoint_catches_the_canonical_probe_failure(monkeypatch, capsys):
    entrypoint = runpy.run_path(issuer.__file__, run_name="synthetic_issuer_entrypoint")
    assert entrypoint["ProbeFailure"] is issuer.ProbeFailure
    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://synthetic-identity")
    monkeypatch.setenv("AZURE_CLIENT_ID", "synthetic-dev-issuer")
    monkeypatch.setattr(sys, "argv", ["issuer", "--probe-voice", "--orchestrator-url", URL])
    monkeypatch.setattr("azure.identity.ManagedIdentityCredential", MagicMock())
    monkeypatch.setattr("governedgraph.voice.probe.probe_voice", AsyncMock(
        side_effect=issuer.ProbeFailure("PRIVATE utterance or figure")))
    with pytest.raises(SystemExit) as error:
        entrypoint["main"]()
    assert error.value.code == 1
    assert capsys.readouterr().out == "FAIL voice_probe error_type=ProbeFailure\n"


def test_b1_probe_image_and_timeout_are_persistent_without_new_resources():
    from pathlib import Path

    root = Path(__file__).parents[1]
    main = (root / "infra/bootstrap.bicep").read_text()
    module = (root / "infra/modules/dev-member-issuer.bicep").read_text()
    assert "image: probeImage" in main and "param probeImage string" in main
    assert "job-gg-dev-member-issuer" in main
    assert "replicaTimeout: 600" in module
    assert "workloadProfileName: 'Consumption'" in module
    assert "checkFacade" not in module
    assert "args: [ '--public-jwk' ]" in module
