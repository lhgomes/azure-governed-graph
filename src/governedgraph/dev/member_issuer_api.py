"""Internal dev-only token endpoint. Only the runtime voice bridge may invoke it."""
import json
import logging
import os
from dataclasses import dataclass

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from governedgraph.common import domain
from governedgraph.common.contracts import ContractError, _strict
from governedgraph.common.operational_logging import configure_operational_logging
from governedgraph.dev.issue_member_token import KeyVaultTokenSigner, issue_token
from governedgraph.voice.access import verify

configure_operational_logging()
log = logging.getLogger("governedgraph.voice")
app = FastAPI(title="ILLUSTRATIVE - synthetic dev voice issuer", docs_url=None, redoc_url=None, openapi_url=None)


@dataclass(frozen=True)
class SyntheticMemberRequest:
    member_ref: str

    def __post_init__(self):
        if not isinstance(self.member_ref, str) or self.member_ref not in domain.demo()["trace_members"]:
            raise ContractError("an allowlisted synthetic member is required")

    @classmethod
    def from_dict(cls, body: dict):
        if isinstance(body, dict) and "member_ref" not in body:
            raise ContractError("synthetic member reference required")
        return _strict(cls, body)


@app.post("/issue")
async def issue(request: Request, authorization: str = Header("")):
    verify(authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else "",
           application=True)
    if request.headers.get("transfer-encoding"):
        raise HTTPException(400, "bounded synthetic issuer payload required")
    body = await request.body()
    if len(body) > 256:
        raise HTTPException(400, "bounded synthetic issuer payload required")
    try:
        selection = SyntheticMemberRequest.from_dict(json.loads(body)) if body else SyntheticMemberRequest(
            domain.demo()["member_ref"])
    except (ContractError, json.JSONDecodeError, UnicodeDecodeError) as error:
        log.warning("voice_member_selection_rejected", extra={"error_type": type(error).__name__})
        raise HTTPException(400, "invalid synthetic member selection") from None
    from azure.core.exceptions import AzureError
    from azure.identity import ManagedIdentityCredential

    try:
        with ManagedIdentityCredential(client_id=os.environ["AZURE_CLIENT_ID"]) as credential:
            signer = KeyVaultTokenSigner(os.environ["AZURE_KEY_VAULT_URL"], "dev-member-token-signing",
                                        credential=credential)
            token = issue_token(signer, selection.member_ref, os.environ["MEMBER_TOKEN_ISSUER"], lifetime=900)
        return JSONResponse({"token": token}, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    except (AzureError, OSError, ValueError) as error:
        log.error("voice_member_issuance_failed", extra={"error_type": type(error).__name__})
        raise HTTPException(503, "synthetic issuance unavailable") from None


@app.get("/healthz")
def health():
    return {"ok": True, "data": "ILLUSTRATIVE - synthetic data"}
