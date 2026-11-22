"""Dev issuer mints in memory; the hosted runtime identity performs all Speech I/O."""
from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

from governedgraph.agents.persona_simulator import persona
from governedgraph.common import domain
from governedgraph.dev.issue_member_token import KeyVaultTokenSigner, ProbeFailure, issue_token


async def probe_voice(orchestrator: str) -> None:
    if not os.getenv("IDENTITY_ENDPOINT") or os.getenv("GOVERNEDGRAPH_ENABLE_DEV_MEMBER_ISSUER") != "true":
        raise PermissionError("voice proofs require the existing in-network synthetic dev issuer")
    target = urlsplit(orchestrator)
    if (target.scheme != "https" or not target.hostname
            or not target.hostname.startswith("ca-gg-orchestrator-")
            or not target.hostname.endswith(".azurecontainerapps.io")
            or target.netloc != target.hostname or target.path not in ("", "/")
            or target.query or target.fragment):
        raise ValueError("voice proofs require the approved orchestrator endpoint")
    client_id = os.environ["AZURE_CLIENT_ID"]
    if not client_id or client_id != os.environ.get("DEV_ISSUER_CLIENT_ID"):
        raise PermissionError("voice proofs must retain the existing dedicated dev-issuer identity")
    import httpx
    from azure.identity import ManagedIdentityCredential

    print("PASS ILLUSTRATIVE - synthetic data", flush=True)
    actor = persona(domain.demo()["persona"]).persona
    script = {key: actor[key] for key in ("opening", "teach_back_answer")}
    checks = ("disclosure_first", "s5_selected", "s2_suspended", "bound_text_only",
              "teach_back_passed", "explainability_written", "under_two_minutes")
    with ManagedIdentityCredential(client_id=client_id) as credential:
        signer = KeyVaultTokenSigner(os.environ["AZURE_KEY_VAULT_URL"], "dev-member-token-signing",
                                    credential=credential)
        token = issue_token(signer, domain.demo()["member_ref"], os.environ["MEMBER_TOKEN_ISSUER"])
        async with httpx.AsyncClient(timeout=210, follow_redirects=False) as client:
            for _ in range(5):
                response = await client.post(orchestrator.rstrip("/") + "/voice/simulations",
                                             headers={"Authorization": "Bearer " + token}, json=script)
                response.raise_for_status()
                result = response.json()
                if result["arm"] != "control":
                    break
                control_ok = (result["strategy_id"] == "S1" and result["s5_selected"] is False
                              and all(result[name] is True for name in checks if name != "s5_selected"))
                print(f"{'PASS' if control_ok else 'FAIL'} control_stays_static "
                      f"journey_id={result['journey_id']}", flush=True)
                if not control_ok:
                    raise ProbeFailure("hosted voice control acceptance failed")
    if result["arm"] != "treatment":
        print(f"FAIL treatment_journey journey_id={result['journey_id']}", flush=True)
        raise ProbeFailure("hosted voice treatment journey not observed")
    for name in checks:
        print(f"{'PASS' if result[name] is True else 'FAIL'} {name} journey_id={result['journey_id']}", flush=True)
    print(json.dumps({key: result[key] for key in (
        "journey_id", "runtime_principal_id", "response_ids",
    )}), flush=True)
    if not all(result[name] is True for name in checks):
        raise ProbeFailure("hosted voice flow acceptance failed")
    print(f"{'PASS' if result['metrics']['target_met'] else 'FAIL'} voice_turn_slo "
          f"journey_id={result['journey_id']}", flush=True)
