"""Signed Ranking Snapshot.

The P2 pipeline signs each snapshot; the Strategy Service verifies the signature
and that every referenced strategy is an ACTIVE catalogue version before loading
(B3). Locally an HMAC key is used; in Azure the key lives in Key Vault and only
the P2 pipeline identity may sign (see KeyVaultSigner).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import asdict, dataclass, field
from typing import Protocol

from governedgraph.common.contracts import _strict, utcnow
from governedgraph.common.io import canonical_json


@dataclass
class RankingSnapshot:
    snapshot_id: str
    catalogue_version: str
    threshold_version: str
    source_audit_id: str
    tier: str  # tier0 | tier1 | expert_priors
    # request key id -> ordered list of [strategy_id, version]
    rankings: dict[str, list[list[str]]]
    created_at: str = field(default_factory=utcnow)
    signature: str = ""
    signer: str = ""

    def __post_init__(self):
        string_fields = (self.snapshot_id, self.catalogue_version, self.threshold_version, self.source_audit_id,
                         self.tier, self.created_at, self.signature, self.signer)
        if not all(isinstance(value, str) for value in string_fields) or not self.snapshot_id:
            raise ValueError("invalid snapshot metadata")
        if not isinstance(self.rankings, dict):
            raise ValueError("snapshot rankings must be an object")
        for key, rankings in self.rankings.items():
            if not isinstance(key, str) or not isinstance(rankings, list):
                raise ValueError("invalid snapshot ranking")
            if any(not isinstance(pair, list) or len(pair) != 2
                   or not all(isinstance(value, str) and value for value in pair) for pair in rankings):
                raise ValueError("invalid strategy/version pair")

    def payload(self) -> dict:
        d = asdict(self)
        d.pop("signature")
        d.pop("signer")
        return d

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RankingSnapshot":
        return _strict(cls, d)


class Signer(Protocol):
    name: str

    def sign(self, data: bytes) -> str: ...

    def verify(self, data: bytes, signature: str) -> bool: ...


class LocalHmacSigner:
    """Local development signer. NOT for production; production uses KeyVaultSigner."""

    name = "local-hmac-sha256"

    def __init__(self, secret: str):
        self._key = secret.encode("utf-8")

    def sign(self, data: bytes) -> str:
        return base64.b64encode(hmac.new(self._key, data, hashlib.sha256).digest()).decode()

    def verify(self, data: bytes, signature: str) -> bool:
        return hmac.compare_digest(self.sign(data), signature or "")


class KeyVaultSigner:
    """Signs with an RSA key held in Azure Key Vault (P2 pipeline identity only).

    The runtime identity has only keys/verify/action. Initialize directly from
    the key identifier so it does not need KeyClient.get_key permission.
    """

    name = "azure-key-vault-rs256"

    def __init__(self, vault_url: str, key_name: str, *, signing_enabled: bool = False):
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.keys.crypto import CryptographyClient

        cred = DefaultAzureCredential()
        key_id = f"{vault_url.rstrip('/')}/keys/{key_name}"
        self._crypto = CryptographyClient(key_id, credential=cred)
        self._signing_enabled = signing_enabled

    def sign(self, data: bytes) -> str:
        if not self._signing_enabled:
            raise PermissionError("snapshot signing is restricted to the P2 pipeline")
        from azure.keyvault.keys.crypto import SignatureAlgorithm

        digest = hashlib.sha256(data).digest()
        return base64.b64encode(self._crypto.sign(SignatureAlgorithm.rs256, digest).signature).decode()

    def verify(self, data: bytes, signature: str) -> bool:
        from azure.core.exceptions import AzureError
        from azure.keyvault.keys.crypto import SignatureAlgorithm

        digest = hashlib.sha256(data).digest()
        try:
            decoded = base64.b64decode(signature, validate=True)
        except ValueError:
            return False
        try:
            return self._crypto.verify(SignatureAlgorithm.rs256, digest, decoded).is_valid
        except AzureError as error:
            raise SignatureUnavailable("Key Vault snapshot verification unavailable") from error


class SignatureUnavailable(RuntimeError):
    pass


def sign_snapshot(snapshot: RankingSnapshot, signer: Signer) -> RankingSnapshot:
    snapshot.signature = signer.sign(canonical_json(snapshot.payload()))
    snapshot.signer = signer.name
    return snapshot


def verify_snapshot(snapshot: RankingSnapshot, signer: Signer) -> bool:
    return bool(snapshot.signature) and signer.verify(canonical_json(snapshot.payload()), snapshot.signature)


def default_signer(*, signing: bool = False):
    from governedgraph.common.config import settings

    if not settings.key_vault_url:
        raise ValueError("AZURE_KEY_VAULT_URL is required in Azure mode")
    return KeyVaultSigner(settings.key_vault_url, settings.snapshot_signing_key_name, signing_enabled=signing)
