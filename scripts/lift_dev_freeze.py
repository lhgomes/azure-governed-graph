"""Run personally under az login; release only the approved dev watchdog-proof freeze."""
from __future__ import annotations

import argparse
from io import StringIO
import json
import os
import re
import subprocess
import sys
import time
from urllib.parse import quote, urlsplit

import httpx
from dotenv import dotenv_values
from governedgraph.strategy_service.recovery import RecoveryRequest, validate_decision_ref

GATEWAY_HOST = re.compile(r"apim-gg-[a-z0-9]+\.azure-api\.net")
ADMIN_PATH = "/strategy-admin"
REQUIRED_ENDPOINT = f"https://apim-gg-<unique-suffix>.azure-api.net{ADMIN_PATH}"


def _is_gateway_admin_endpoint(parsed) -> bool:
    return (parsed.scheme == "https" and parsed.hostname is not None and GATEWAY_HOST.fullmatch(parsed.hostname) is not None
            and parsed.netloc == parsed.hostname and parsed.path == ADMIN_PATH and not parsed.query and not parsed.fragment)


def _validate_context(values: dict, account: dict, decision_ref: str) -> str:
    environment = values.get("AZURE_ENV_NAME")
    if environment != "gg-dev":
        raise ValueError(f"environment name check failed: detected {environment!r}; required 'gg-dev'")
    validate_decision_ref(decision_ref)
    user_type = (account.get("user") or {}).get("type")
    if user_type != "user":
        raise ValueError(f"credential type check failed: detected {user_type!r}; required interactive az login user")
    tenant, expected = account.get("tenantId"), values.get("STRATEGY_TENANT_ID")
    if not expected or tenant != expected:
        raise ValueError(f"tenant check failed: detected {tenant!r}; required {expected!r}")
    endpoint = values.get("STRATEGY_ADMIN_URL") or ""
    try:
        parsed = urlsplit(endpoint)
        valid = _is_gateway_admin_endpoint(parsed)
        detected = (f"scheme={parsed.scheme!r}, host={parsed.hostname!r}, path={parsed.path!r}, "
                    f"port={parsed.port!r}, userinfo_present={bool(parsed.username or parsed.password)}, "
                    f"query_present={bool(parsed.query)}, fragment_present={bool(parsed.fragment)}")
    except ValueError:
        valid, detected = False, "malformed URL (contents withheld)"
    if not valid:
        raise ValueError(f"endpoint check failed: detected {detected}; required {REQUIRED_ENDPOINT}")
    return endpoint


def _check_server_response(response: httpx.Response, decision_ref: str, operation: str) -> dict:
    if response.status_code == 409:
        detail = response.json().get("detail", "")
        if isinstance(detail, str) and detail.startswith("decision-reference reuse check failed:"):
            raise ValueError(f"decision-reference reuse check failed: detected {decision_ref!r}; already recorded")
    if response.status_code != 200:
        raise RuntimeError(f"human recovery {operation} check failed: detected HTTP {response.status_code}; "
                           "inspect audit/state readiness before retrying")
    result = response.json()
    if result.get("decision_ref") != decision_ref:
        raise RuntimeError(f"server decision-reference check failed: detected {result.get('decision_ref')!r}; "
                           f"required {decision_ref!r}")
    return result


def recover(decision_ref: str) -> dict:
    validate_decision_ref(decision_ref)
    from azure.core.exceptions import ClientAuthenticationError
    from azure.identity import AzureCliCredential

    values = dotenv_values(stream=StringIO(subprocess.check_output(
        ["azd", "env", "get-values", "--environment", "gg-dev"], text=True,
        env={**os.environ, "AZURE_ENV_NAME": "gg-dev"},
    )))
    account = json.loads(subprocess.check_output(["az", "account", "show", "-o", "json"], text=True))
    endpoint = _validate_context(values, account, decision_ref)
    with AzureCliCredential(tenant_id=values["STRATEGY_TENANT_ID"]) as credential:
        retries = 0
        while True:
            try:
                token = credential.get_token(f"api://{values['STRATEGY_API_CLIENT_ID']}/.default")
                break
            except ClientAuthenticationError as error:
                if "AADSTS50105:" not in str(error) or retries >= 5:
                    raise
                retries += 1
                print(f"Entra operator access is not active; retrying in 60 seconds ({retries}/5). "
                      "Persistent failure requires checking the recovery-access assignment.", file=sys.stderr)
                time.sleep(60)
        with httpx.Client(timeout=60, follow_redirects=False,
                          headers={"Authorization": "Bearer " + token.token}) as client:
            readiness = client.get(endpoint + "/freeze-recovery?decision_ref=" + quote(decision_ref, safe=""))
            ready = _check_server_response(readiness, decision_ref, "readiness")
            if ready.get("frozen") is not True:
                raise RuntimeError(f"frozen state check failed: detected {ready.get('frozen')!r}; required True")
            request = RecoveryRequest.from_dict({
                "decision_ref": decision_ref, "expected_audit_execution": ready.get("audit_execution"),
            })
            response = client.post(endpoint + "/freeze-recovery", json={
                "decision_ref": request.decision_ref, "expected_audit_execution": request.expected_audit_execution,
            })
            result = _check_server_response(response, decision_ref, "completion")
            if result.get("frozen") is not False:
                raise RuntimeError(f"recovery acknowledgment check failed: detected frozen={result.get('frozen')!r}; "
                                   "inspect live state before any learning cycle")
            return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision-ref", required=True,
                        help="actual unused GB-DEV-YYYY-NNN reference; YYYY and NNN must be four and three digits")
    args = parser.parse_args()
    print(json.dumps(recover(args.decision_ref), sort_keys=True))
