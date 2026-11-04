"""Atomic strategy state; Azure snapshots are separate immutable blobs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Protocol

from governedgraph.common.io import canonical_json, save_json


class StateUnavailable(RuntimeError):
    pass


class StateConflict(StateUnavailable):
    pass


class StateStore(Protocol):
    def read(self, etag: str | None = None) -> tuple[dict | None, str | None]: ...

    def write(self, state: dict, etag: str | None) -> str: ...


class LocalStateStore:
    def __init__(self, path: Path):
        self.path = path

    def read(self, etag: str | None = None) -> tuple[dict | None, str | None]:
        if not self.path.exists():
            return None, None
        raw = self.path.read_bytes()
        version = hashlib.sha256(raw).hexdigest()
        return (None if etag == version else json.loads(raw)), version

    def write(self, state: dict, etag: str | None) -> str:
        save_json(self.path, state)
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


class BlobStateStore:
    def __init__(self, account_url: str, container: str = "ranking-snapshots"):
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        if not account_url:
            raise ValueError("RANKING_STORAGE_URL is required in Azure mode")
        self.container = BlobServiceClient(
            account_url, credential=DefaultAzureCredential(), retry_total=1,
            connection_timeout=2, read_timeout=2,
        ).get_container_client(container)
        self.blob = self.container.get_blob_client("state.json")

    def read(self, etag: str | None = None) -> tuple[dict | None, str | None]:
        from azure.core.exceptions import AzureError, ResourceNotFoundError

        state_loaded = False
        try:
            if etag and self.blob.get_blob_properties().etag == etag:
                from governedgraph.agents.foundry_tracing import dependency_outcome

                dependency_outcome("", "strategy_conditional_read", "not_modified")
                return None, etag
            stream = self.blob.download_blob()
            state = json.loads(stream.readall())
            state_loaded = True
            for field in ("current", "last_good"):
                reference = state[field]
                if reference is not None:
                    if not isinstance(reference, str) or not reference.startswith(field + "/"):
                        raise ValueError("invalid snapshot blob reference")
                    raw = self.container.get_blob_client(reference).download_blob().readall()
                    if reference != f"{field}/{hashlib.sha256(raw).hexdigest()}.json":
                        raise ValueError("snapshot blob digest mismatch")
                    state[field] = json.loads(raw)
            return state, stream.properties.etag
        except ResourceNotFoundError as error:
            if error.error_code == "BlobNotFound" and not etag:
                # Only the initial state blob may be absent, never a referenced snapshot.
                if not state_loaded:
                    return None, None
            raise StateUnavailable("strategy state or referenced snapshot disappeared") from error
        except (AzureError, ValueError, KeyError, TypeError) as error:
            raise StateUnavailable("could not read strategy state") from error

    def write(self, state: dict, etag: str | None) -> str:
        from azure.core import MatchConditions
        from azure.core.exceptions import AzureError, ResourceExistsError, ResourceModifiedError

        try:
            manifest = dict(state)
            for field in ("current", "last_good"):
                snapshot = state[field]
                if snapshot is None:
                    continue
                raw = canonical_json(snapshot)
                reference = f"{field}/{hashlib.sha256(raw).hexdigest()}.json"
                blob = self.container.get_blob_client(reference)
                try:
                    blob.upload_blob(raw, overwrite=False)
                except ResourceExistsError:
                    if blob.download_blob().readall() != raw:
                        raise StateUnavailable("immutable snapshot blob was modified")
                manifest[field] = reference
            options = {"overwrite": True, "etag": etag, "match_condition": MatchConditions.IfNotModified} if etag else {
                "overwrite": False,
            }
            result = self.blob.upload_blob(canonical_json(manifest), **options)
            return result["etag"]
        except (ResourceModifiedError, ResourceExistsError) as error:
            raise StateConflict("strategy state changed concurrently") from error
        except AzureError as error:
            raise StateUnavailable("could not persist strategy state") from error
