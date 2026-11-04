"""Internal dev-only token endpoint. Only the runtime voice bridge may invoke it."""
import logging
import os

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from governedgraph.common import domain
from governedgraph.common.operational_logging import configure_operational_logging
from governedgraph.dev.issue_member_token import KeyVaultTokenSigner, issue_token
from governedgraph.voice.access import verify

configure_operational_logging()
log = logging.getLogger("governedgraph.voice")
app = FastAPI(title="ILLUSTRATIVE - synthetic dev voice issuer", docs_url=None, redoc_url=None, openapi_url=None)


@app.post("/issue")
def issue(request: Request, authorization: str = Header("")):
    verify(authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else "",
           application=True)
    if request.headers.get("content-length", "0") != "0" or request.headers.get("transfer-encoding"):
        raise HTTPException(400, "the fixed synthetic issuer accepts no payload")
    from azure.core.exceptions import AzureError
    from azure.identity import ManagedIdentityCredential

    try:
        with ManagedIdentityCredential(client_id=os.environ["AZURE_CLIENT_ID"]) as credential:
            signer = KeyVaultTokenSigner(os.environ["AZURE_KEY_VAULT_URL"], "dev-member-token-signing",
                                        credential=credential)
            token = issue_token(signer, domain.demo()["member_ref"], os.environ["MEMBER_TOKEN_ISSUER"], lifetime=900)
        return JSONResponse({"token": token}, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    except (AzureError, OSError, ValueError) as error:
        log.error("voice_member_issuance_failed", extra={"error_type": type(error).__name__})
        raise HTTPException(503, "synthetic issuance unavailable") from None


@app.get("/healthz")
def health():
    return {"ok": True, "data": "ILLUSTRATIVE - synthetic data"}
