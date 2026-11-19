"""Dev-only issuer metadata and in-network synthetic-channel acceptance.

ILLUSTRATIVE - synthetic data. Never install this as a production identity provider.
Private signing material never leaves Key Vault. Live member tokens exist only
in the hosted probe's memory; output is check outcomes and IDs, never tokens.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import time
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from governedgraph.common import domain
from governedgraph.dev import ProbeFailure

if TYPE_CHECKING:
    import httpx
    from azure.core.credentials import TokenCredential


class RemoteSigner(Protocol):
    def sign(self, data: bytes) -> str: ...


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def issue_token(signer: RemoteSigner, member_ref: str, issuer: str, *, audience: str = "gg-apim",
                step_up: bool = True, lifetime: int = 900, now: int | None = None) -> str:
    if os.getenv("GOVERNEDGRAPH_ENABLE_DEV_MEMBER_ISSUER") != "true":
        raise PermissionError("synthetic development issuer is disabled")
    if not member_ref.startswith("MBR-") or not issuer or not 0 < lifetime <= 900:
        raise ValueError("synthetic member, issuer, and lifetime of at most fifteen minutes required")
    issued_at = int(time.time()) if now is None else now
    claims: dict[str, str | int] = {
        "iss": issuer, "aud": audience, "member_ref": member_ref,
        "iat": issued_at, "exp": issued_at + lifetime,
    }
    if step_up:
        claims["acr"] = "stepup"
    header = _encode(b'{"alg":"RS256","typ":"JWT"}')
    payload = _encode(json.dumps(claims, separators=(",", ":")).encode())
    message = f"{header}.{payload}"
    signature = signer.sign(message.encode("ascii"))
    return f"{message}.{signature}"


class KeyVaultTokenSigner:
    def __init__(self, vault_url: str, key_name: str, *, credential: TokenCredential | None = None):
        from azure.identity import AzureCliCredential
        from azure.keyvault.keys import KeyClient
        from azure.keyvault.keys.crypto import CryptographyClient

        if credential is None:
            credential = AzureCliCredential()
        key = KeyClient(vault_url, credential).get_key(key_name)
        self.client = CryptographyClient(key.id, credential)
        self.public_jwk = {"kty": "RSA", "alg": "RS256", "use": "sig",
                           "n": _encode(key.key.n), "e": _encode(key.key.e)}

    def sign(self, data: bytes) -> str:
        from azure.keyvault.keys.crypto import SignatureAlgorithm

        return _encode(self.client.sign(SignatureAlgorithm.rs256, hashlib.sha256(data).digest()).signature)


def probe_channel(signer: RemoteSigner, issuer: str, orchestrator: str,
                  client: httpx.Client, *, apim_url: str | None = None) -> dict[str, str]:
    def require(name: str, condition: bool, journey_id: str = "", *, http_status: int | None = None) -> None:
        print(f"{'PASS' if condition else 'FAIL'} {name}"
              + (f" journey_id={journey_id}" if journey_id else "")
              + (f" http_status={http_status}" if not condition and http_status is not None else ""), flush=True)
        if not condition:
            raise ProbeFailure(name)

    target = urlsplit(orchestrator)
    require("approved_channel_endpoint",
            target.scheme == "https" and target.hostname is not None
            and target.hostname.startswith("ca-gg-orchestrator-")
            and target.hostname.endswith(".azurecontainerapps.io")
            and target.netloc == target.hostname and target.path in ("", "/")
            and not target.query and not target.fragment)
    if apim_url:
        gateway = urlsplit(apim_url)
        require("approved_report_gateway",
                gateway.scheme == "https" and gateway.hostname is not None
                and gateway.hostname.startswith("apim-gg-")
                and gateway.hostname.endswith(".azure-api.net")
                and gateway.netloc == gateway.hostname and gateway.path in ("", "/")
                and not gateway.query and not gateway.fragment)
    demo = domain.demo()
    common = {"issuer": issuer, "lifetime": 900}
    valid = issue_token(signer, demo["member_ref"], **common)
    other = issue_token(signer, demo["other_member_ref"], **common)
    expired = issue_token(signer, demo["member_ref"], issuer, lifetime=60, now=int(time.time()) - 900)
    base = orchestrator.rstrip("/")
    body = {"channel": "voice", "accessibility_mode": "standard"}

    def send(method: str, url: str, **kwargs):
        response = client.request(method, url, **kwargs)
        for delay in (1, 2, 4, 8, 16):
            if response.status_code != 409:
                return response
            try:
                error = response.json()
            except ValueError:
                return response
            if not isinstance(error, dict) or error.get("detail") not in (
                "journey busy; retry with the same Idempotency-Key",
                "journey busy; retry the report",
                "journey busy",
            ):
                return response
            time.sleep(delay)
            response = client.request(method, url, **kwargs)
        return response

    def request(method: str, path: str, token: str = valid, **kwargs):
        headers = kwargs.pop("headers", {})
        if token:
            headers["Authorization"] = "Bearer " + token
        return send(method, base + path, headers=headers, **kwargs)

    def start(token: str = valid, contact_type: str = "") -> str:
        response = request("POST", "/journeys", token,
                           json={**body, **({"contact_type": contact_type} if contact_type else {})})
        require("journey_started", response.status_code == 200)
        result = response.json()
        journey_id = result["journey_id"]
        require("ai_disclosure", "AI" in result["text"], journey_id)
        return journey_id

    def turn(journey_id: str, utterance: str, key: str):
        response = request("POST", f"/journeys/{journey_id}/turns", json={"utterance": utterance},
                           headers={"Idempotency-Key": key})
        require("turn_accepted", response.status_code == 200, journey_id, http_status=response.status_code)
        return response.json()

    print("PASS ILLUSTRATIVE - synthetic data", flush=True)
    require("missing_token_rejected", request("POST", "/journeys", "", json=body).status_code == 401)
    require("expired_token_rejected", request("POST", "/journeys", expired, json=body).status_code == 401)
    foreign = start(other)
    require("cross_member_path_rejected",
            request("GET", f"/journeys/{foreign}/advisor-queue").status_code == 403, foreign)
    for _ in range(5):
        explanation = start()
        result = turn(explanation, demo["proof_explanation_question"], uuid4().hex)
        require("explanation_generated", not result["escalated"], explanation)
        require("s2_suspended", "S2" in result["suspended"], explanation)
        if result["arm"] != "control":
            break
        require("control_stays_static", result["strategy_id"] == "S1", explanation)
    require("treatment_journey", result["arm"] == "treatment", explanation)
    # B3: the runtime selects only from the served ranking, whichever strategy the approved priors rank first.
    require("served_strategy_selected",
            result["strategy_id"] in result["ranked"] and not result["escalated"], explanation)

    advice = start()
    question = demo["proof_advice_question"]
    key = uuid4().hex
    result = turn(advice, question, key)
    require("advice_escalated", result["escalated"] and result["state"] == "escalation_requested", advice)
    require("duplicate_turn_deduplicated", turn(advice, question, key).get("duplicate") is True, advice)
    handovers = []
    for _ in range(15):
        response = request("GET", f"/journeys/{advice}/advisor-queue")
        require("advisor_queue_authorized", response.status_code == 200, advice, http_status=response.status_code)
        handovers = response.json()["handovers"]
        if handovers:
            break
        time.sleep(2)
    require("exactly_one_advice_handover",
            len(handovers) == 1 and handovers[0]["reason_code"] == "advice_intent", advice)
    injection = start()
    result = turn(injection, demo["proof_injection_question"], uuid4().hex)
    require("injection_blocked_at_screening",
            result["escalated"] and str(result.get("blocked_reason", "")).startswith("input blocked:"),
            injection)
    # A returning member and a recap that also asks something must each get an explanation, never a decline or
    # the closing acknowledgement (both carry no selected strategy).
    second = start(contact_type="second_contact")
    result = turn(second, demo["proof_second_contact_question"], uuid4().hex)
    require("second_contact_answered", not result["escalated"] and result["strategy_id"] is not None, second)
    recap = start()
    turn(recap, demo["proof_explanation_question"], uuid4().hex)
    result = turn(recap, demo["proof_recap_question"], uuid4().hex)
    require("recap_question_answered", not result["escalated"] and result["strategy_id"] is not None, recap)
    report_base = apim_url.rstrip("/") + "/journey" if apim_url else base
    report_path = f"/members/{demo['member_ref']}/explainability-report"

    def report_request(token: str, path: str = report_path):
        return send("GET", report_base + path, headers={"Authorization": "Bearer " + token} if token else {})

    require("report_missing_token_rejected", report_request("").status_code == 401)
    require("report_expired_token_rejected", report_request(expired).status_code == 401)
    require("report_cross_member_rejected",
            report_request(valid, f"/members/{demo['other_member_ref']}/explainability-report").status_code == 403)
    weak = issue_token(signer, demo["member_ref"], step_up=False, **common)
    require("report_stepup_required", report_request(weak).status_code == 403)
    response = report_request(valid)
    require("member_report_authorized", response.status_code == 200, http_status=response.status_code)
    require("member_report_not_cached", response.headers.get("cache-control") == "no-store")
    report = response.json()
    require("member_report_scoped",
            report["member_ref"] == demo["member_ref"]
            and all(row["member_ref"] == demo["member_ref"] for row in report["records"]))
    proof_ids = {explanation, advice, injection}
    rows = [row for row in report["records"] if row["journey_id"] in proof_ids]
    references = [row.get("response_id") for row in rows]
    require("member_report_response_linkage",
            len(rows) == 6 and len(set(references)) == 6
            and all(ref and ref in report["delivered_response_ids"] for ref in references))
    return {"explanation": explanation, "advice": advice, "injection": injection}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--public-jwk", action="store_true")
    mode.add_argument("--probe-channel", action="store_true")
    mode.add_argument("--probe-evals", action="store_true")
    mode.add_argument("--probe-voice", action="store_true")
    parser.add_argument("--orchestrator-url")
    parser.add_argument("--apim-url")
    args = parser.parse_args()
    if os.getenv("GOVERNEDGRAPH_ENABLE_DEV_MEMBER_ISSUER") != "true":
        parser.error("set GOVERNEDGRAPH_ENABLE_DEV_MEMBER_ISSUER=true only for the synthetic development environment")
    credential: TokenCredential | None = None
    if (args.probe_channel or args.probe_voice or args.probe_evals) and (not os.getenv("IDENTITY_ENDPOINT") or not args.orchestrator_url):
        parser.error("channel proofs require the existing in-network dev-issuer job and --orchestrator-url")
    if os.getenv("IDENTITY_ENDPOINT"):
        from azure.identity import ManagedIdentityCredential

        credential = ManagedIdentityCredential(client_id=os.environ["AZURE_CLIENT_ID"])
    if args.public_jwk:
        signer = KeyVaultTokenSigner(os.environ["AZURE_KEY_VAULT_URL"], "dev-member-token-signing",
                                    credential=credential)
        print(json.dumps(signer.public_jwk), flush=True)
    else:
        import httpx
        from azure.core.exceptions import AzureError
        from governedgraph.voice.azure_voice import VoiceAuthorizationFailure
        from governedgraph.voice.voice_live_bridge import VoiceError

        try:
            if args.probe_voice:
                import asyncio
                from governedgraph.voice.probe import probe_voice

                asyncio.run(probe_voice(args.orchestrator_url))
                return
            signer = KeyVaultTokenSigner(os.environ["AZURE_KEY_VAULT_URL"], "dev-member-token-signing",
                                        credential=credential)
            with httpx.Client(timeout=120, follow_redirects=False) as client:
                if args.probe_evals:
                    from governedgraph.dev.eval_probe import probe_evaluations

                    probe_evaluations(signer, os.environ["MEMBER_TOKEN_ISSUER"], args.orchestrator_url, client,
                                      token_issuer=issue_token)
                else:
                    probe_channel(signer, os.environ["MEMBER_TOKEN_ISSUER"], args.orchestrator_url, client,
                                  apim_url=args.apim_url)
        except (AzureError, httpx.HTTPError, ProbeFailure, ValueError, KeyError, TypeError, VoiceError,
                TimeoutError, OSError) as error:
            name = ("voice_probe" if args.probe_voice else
                    "azure_evaluation" if args.probe_evals else "channel_probe")
            if isinstance(error, VoiceAuthorizationFailure):
                name = f"voice_authorization_http{error.status_code}"
            print(f"FAIL {name} error_type={type(error).__name__}", flush=True)
            raise SystemExit(1) from None


if __name__ == "__main__":
    main()
