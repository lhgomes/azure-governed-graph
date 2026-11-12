"""Immutable aggregate snapshots; individual evidence stays in runtime stores."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from governedgraph.common.config import DATA_DIR, settings
from governedgraph.learning.feedback import FeedbackManifest, FeedbackSnapshot, WATERMARK, project_feedback
from governedgraph.orchestrator.store import atomic_write


class BaselinePending(Exception):
    """The approved baseline is not published yet (fresh deployment); retry later, not an incident."""


def snapshot_name(digest: str) -> str:
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("invalid feedback aggregate digest")
    return f"live/{digest}.csv"


class FileFeedbackStore:
    def __init__(self, directory: Path, baseline: Path | None = None):
        self.directory = directory
        self.baseline = baseline or DATA_DIR / "synthetic" / "outcome_aggregates_illustrative.csv"

    def baseline_bytes(self) -> bytes:
        return self.baseline.read_bytes()

    def current(self) -> FeedbackManifest | None:
        path = self.directory / "current.json"
        return FeedbackManifest.from_dict(json.loads(path.read_text())) if path.exists() else None

    def save(self, snapshot: FeedbackSnapshot) -> None:
        if hashlib.sha256(snapshot.outcomes).hexdigest() != snapshot.manifest.outcome_digest:
            raise ValueError("feedback aggregate digest mismatch")
        path = self.directory / snapshot_name(snapshot.manifest.outcome_digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            atomic_write(path, snapshot.outcomes.decode())
        if path.read_bytes() != snapshot.outcomes:
            raise ValueError("immutable feedback aggregate conflict")
        atomic_write(self.directory / "current.json", json.dumps(asdict(snapshot.manifest), sort_keys=True))

    def outcome_path(self) -> Path:
        manifest = self.current()
        if manifest is None:
            raise ValueError("no published feedback snapshot")
        path = self.directory / snapshot_name(manifest.outcome_digest)
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest.outcome_digest:
            raise ValueError("feedback aggregate digest mismatch")
        return path


class BlobFeedbackStore:
    def __init__(self, *, container=None):
        if container is None:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient

            if not settings.explainability_account_url:
                raise ValueError("feedback storage endpoint is required")
            container = BlobServiceClient(
                settings.explainability_account_url, credential=DefaultAzureCredential(),
                connection_timeout=5, read_timeout=10, retry_total=0,
            ).get_container_client("learning-aggregates")
        self.container = container
        self._etag = None

    def baseline_bytes(self) -> bytes:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return self.container.get_blob_client("outcomes.csv").download_blob().readall()
        except ResourceNotFoundError as error:
            raise BaselinePending("learning-aggregates/outcomes.csv is not published yet") from error

    def current(self) -> FeedbackManifest | None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            stream = self.container.get_blob_client("current.json").download_blob()
        except ResourceNotFoundError:
            self._etag = None
            return None
        manifest = FeedbackManifest.from_dict(json.loads(stream.readall()))
        self._etag = stream.properties.etag
        return manifest

    def save(self, snapshot: FeedbackSnapshot) -> None:
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceExistsError

        if hashlib.sha256(snapshot.outcomes).hexdigest() != snapshot.manifest.outcome_digest:
            raise ValueError("feedback aggregate digest mismatch")
        blob = self.container.get_blob_client(snapshot_name(snapshot.manifest.outcome_digest))
        try:
            blob.upload_blob(snapshot.outcomes, overwrite=False, metadata={"watermark": WATERMARK})
        except ResourceExistsError:
            pass
        if blob.download_blob().readall() != snapshot.outcomes:
            raise ValueError("immutable feedback aggregate conflict")
        options = ({"overwrite": True, "etag": self._etag, "match_condition": MatchConditions.IfNotModified}
                   if self._etag is not None else {"overwrite": False})
        self.container.get_blob_client("current.json").upload_blob(
            json.dumps(asdict(snapshot.manifest), sort_keys=True).encode(),
            metadata={"watermark": WATERMARK}, **options,
        )


@contextmanager
def baseline_file(store: FileFeedbackStore | BlobFeedbackStore):
    with TemporaryDirectory(prefix="gg-feedback-") as directory:
        path = Path(directory) / "baseline.csv"
        path.write_bytes(store.baseline_bytes())
        yield path


def publish_feedback(journey_store, audit_store, output: FileFeedbackStore | BlobFeedbackStore) -> FeedbackManifest:
    from governedgraph.common.contracts import ExplainabilityRecord

    # A single producer uses the existing runtime lease, not a new identity/job.
    with journey_store.transaction("feedback-projection"):
        current = output.current()
        journeys = []
        for journey_id in journey_store.ids():
            if journey_id == "feedback-projection":
                continue
            with journey_store.transaction(journey_id) as transaction:
                if transaction.record is not None:
                    journeys.append(transaction.record.journey)
        identifiers = {journey.journey_id for journey in journeys}
        records = [ExplainabilityRecord.from_dict(record) for record in audit_store.read_all()
                   if record["journey_id"] in identifiers]
        with baseline_file(output) as baseline:
            snapshot = project_feedback(journeys, records, baseline=baseline)
        if current != snapshot.manifest:
            output.save(snapshot)
        return snapshot.manifest


def default_feedback_store() -> BlobFeedbackStore:
    return BlobFeedbackStore()
