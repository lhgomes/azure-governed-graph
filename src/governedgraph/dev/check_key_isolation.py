"""ILLUSTRATIVE - synthetic data. Probe only after approved key-scope cleanup.

Run separately in the existing P2 and dev-issuer execution contexts. Signatures
are discarded; the fixed probe is neither a member token nor a ranking snapshot.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from uuid import UUID


KEYS = {
    "pipeline": ("ranking-snapshot-signing", "dev-member-token-signing"),
    "issuer": ("dev-member-token-signing", "ranking-snapshot-signing"),
}


def check(vault_url: str, identity_role: str, expected_principal_id: str) -> dict[str, str | bool]:
    from azure.core.exceptions import HttpResponseError
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.keys.crypto import CryptographyClient, SignatureAlgorithm

    principal = str(UUID(expected_principal_id))
    allowed, denied = KEYS[identity_role]
    digest = hashlib.sha256(b"GovernedGraph synthetic key-isolation probe; not a token or snapshot").digest()
    with DefaultAzureCredential() as credential:
        with CryptographyClient(f"{vault_url.rstrip('/')}/keys/{allowed}", credential) as client:
            client.sign(SignatureAlgorithm.rs256, digest)
        with CryptographyClient(f"{vault_url.rstrip('/')}/keys/{denied}", credential) as client:
            try:
                client.sign(SignatureAlgorithm.rs256, digest)
            except HttpResponseError as error:
                message = str(error)
                if (error.status_code != 403 or "ForbiddenByRbac" not in message
                        or not re.search(r"\boid=" + re.escape(principal) + r"(?:[;\s]|$)",
                                         message.lower())):
                    raise RuntimeError("Key isolation requires RBAC denial for the expected identity") from error
            else:
                raise RuntimeError("Key isolation failed: the other signing key is accessible")
    return {
        "data": "ILLUSTRATIVE - synthetic data",
        "identity_role": identity_role,
        "principal_id": principal,
        "allowed_key": allowed,
        "denied_key": denied,
        "rbac_denial": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity-role", choices=KEYS, required=True)
    parser.add_argument("--expected-principal-id", type=UUID, required=True)
    parser.add_argument("--probe", action="store_true", help="Explicitly perform the two signing probes")
    args = parser.parse_args()
    if args.probe:
        result = check(os.environ["AZURE_KEY_VAULT_URL"], args.identity_role,
                       str(args.expected_principal_id))
    else:
        allowed, denied = KEYS[args.identity_role]
        result = {"probe": False, "identity_role": args.identity_role,
                  "principal_id": str(args.expected_principal_id),
                  "allowed_key": allowed, "denied_key": denied}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
