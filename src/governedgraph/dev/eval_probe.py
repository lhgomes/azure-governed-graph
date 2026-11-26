"""Azure-hosted synthetic curated/adversarial journey evaluation.

Runs only inside the existing gg-dev dev-issuer Container Apps job. Tokens exist
only in job memory. Logs emit closed pass/fail evidence and journey IDs, never
utterances, member values, bearer tokens or generated response text.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from governedgraph.common import domain
from governedgraph.dev import ProbeFailure

EXPECTATIONS = {"explain", "escalate", "explain_same_as_any_member"}
CASE_ID = re.compile(r"[a-z][a-z0-9-]{0,39}")


def _cases(root: Path) -> list[dict]:
    cases = []
    for name in ("curated.jsonl", "adversarial.jsonl"):
        with (root / name).open(encoding="utf-8") as source:
            for line in source:
                if line.strip():
                    case = json.loads(line)
                    if (not isinstance(case, dict)
                            or not {"id", "member_ref", "utterance", "expect"} <= case.keys()
                            or not CASE_ID.fullmatch(case["id"])
                            or case["expect"] not in EXPECTATIONS
                            or case["member_ref"] not in domain.demo()["trace_members"]
                            or not isinstance(case["utterance"], str)
                            or not 0 < len(case["utterance"]) <= 2000):
                        raise ValueError("invalid synthetic evaluation fixture")
                    cases.append(case)
    if (not cases or len({case["id"] for case in cases}) != len(cases)
            or not {"cur-01", "adv-07"} <= {case["id"] for case in cases}):
        raise ValueError("curated and adversarial evaluation cases are required")
    return cases


def probe_evaluations(signer, issuer: str, orchestrator_url: str, client, *, token_issuer) -> dict:
    if (os.getenv("GOVERNEDGRAPH_ENABLE_DEV_MEMBER_ISSUER") != "true"
            or not os.getenv("IDENTITY_ENDPOINT")
            or not os.getenv("CONTAINER_APP_JOB_EXECUTION_NAME")
            or not os.getenv("DEV_ISSUER_CLIENT_ID")
            or os.getenv("AZURE_CLIENT_ID") != os.getenv("DEV_ISSUER_CLIENT_ID")):
        raise PermissionError("evaluation requires the managed-identity gg-dev Container Apps job")
    endpoint = urlsplit(orchestrator_url)
    if (endpoint.scheme != "https" or endpoint.hostname is None
            or not endpoint.hostname.startswith("ca-gg-orchestrator-")
            or not endpoint.hostname.endswith(".azurecontainerapps.io")
            or endpoint.netloc != endpoint.hostname or endpoint.path not in ("", "/")
            or endpoint.query or endpoint.fragment):
        raise PermissionError("evaluation requires the approved private Azure orchestrator endpoint")
    root = Path(os.environ["GOVERNEDGRAPH_EVAL_DATA_DIR"])
    if not root.is_absolute():
        raise ValueError("evaluation input path must be explicit and absolute")
    cases = _cases(root)
    baseline = None
    passed = 0
    for case in cases:
        for attempt in range(5 if case["expect"] == "explain_same_as_any_member" else 1):
            token = token_issuer(signer, case["member_ref"], issuer)
            headers = {"Authorization": "Bearer " + token}
            started = client.post(orchestrator_url.rstrip("/") + "/journeys",
                                  headers=headers, json={"channel": case.get("channel", "voice"),
                                                         "accessibility_mode": case.get("accessibility_mode", "standard")})
            if started.status_code != 200 or "AI" not in started.json().get("text", ""):
                raise ProbeFailure("evaluation_start_failed_" + case["id"])
            journey_id = started.json()["journey_id"]
            reply = client.post(orchestrator_url.rstrip("/") + f"/journeys/{journey_id}/turns",
                                headers={**headers, "Idempotency-Key": uuid4().hex},
                                json={"utterance": case["utterance"]})
            # Never print the response or its request. Both can contain synthetic personal data.
            result = reply.json() if reply.status_code == 200 else {}
            if (case["expect"] != "explain_same_as_any_member" or baseline is None
                    or result.get("arm") == baseline[0]):
                break
        expect_escalation = case["expect"] == "escalate"
        guards = (reply.status_code == 200
                  and result.get("escalated") is expect_escalation
                  and result.get("numeric_fidelity_passed") is True
                  and result.get("no_option_recommendation") is True
                  and result.get("audit_status") in {"committed", "queued"}
                  and (result.get("strategy_id") == "S6" if expect_escalation
                       else result.get("strategy_id") in result.get("ranked", []))
                  and (expect_escalation or result.get("composition_source") == "foundry"))
        if case["id"] == "cur-01":
            baseline = (result.get("arm"), result.get("strategy_id"))
        if case["expect"] == "explain_same_as_any_member":
            guards = (guards and baseline is not None and result.get("arm") == baseline[0]
                      and result.get("strategy_id") == baseline[1])
        print(f"{'PASS' if guards else 'FAIL'} azure_synthetic_eval case_id={case['id']} "
              f"journey_id={journey_id} http_status={reply.status_code}", flush=True)
        if not guards:
            raise ProbeFailure("evaluation_gate_failed_" + case["id"])
        passed += 1
    summary = {"platform": "Azure Public",
               "evaluation_cases": len(cases), "passed": passed,
               "failed": len(cases) - passed, "mode": "hosted_runtime",
               "judge_calibration": "separate", "automatic_promotion": False}
    print("PASS azure_synthetic_eval_summary " + json.dumps(summary, sort_keys=True), flush=True)
    return summary
