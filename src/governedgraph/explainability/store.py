"""Write-once audit records with a separately durable journey outbox."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path

from governedgraph.common.config import settings
from governedgraph.common.contracts import ExplainabilityRecord

log = logging.getLogger("governedgraph.explainability")


class AuditUnavailable(RuntimeError):
    pass


class AuditStorageUnavailable(OSError):
    pass


class AuditConflict(ValueError):
    pass


def encoded(record: ExplainabilityRecord) -> str:
    payload = record.to_dict()
    if record.contract_version not in {"1.2", "1.3"}:
        payload.pop("decision_evidence")
    return json.dumps(payload, sort_keys=True)


def record_key(record: ExplainabilityRecord) -> str:
    identity = record.response_id or encoded(record)
    return hashlib.sha256((record.journey_id + ":" + identity).encode()).hexdigest()


class LocalExplainabilityStore:
    def __init__(self, path: Path | None = None):
        self.path = path or settings.run_dir / "explainability" / "records.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: ExplainabilityRecord) -> None:
        with self.path.open("a+", encoding="utf-8") as output:
            fcntl.flock(output, fcntl.LOCK_EX)
            output.seek(0)
            for line in output:
                previous = ExplainabilityRecord.from_dict(json.loads(line))
                if record_key(previous) == record_key(record):
                    if encoded(previous) != encoded(record):
                        raise AuditConflict("an audit identifier cannot be overwritten")
                    return
            output.write(encoded(record) + "\n")
            output.flush()
            os.fsync(output.fileno())

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as source:
            fcntl.flock(source, fcntl.LOCK_SH)
            return [asdict(ExplainabilityRecord.from_dict(json.loads(line)))
                    for line in source if line.strip()]

    def read_journey(self, journey_id: str) -> list[dict]:
        return [r for r in self.read_all() if r["journey_id"] == journey_id]

    def read_member(self, member_ref: str) -> list[dict]:
        return [r for r in self.read_all() if r["member_ref"] == member_ref]


class BlobExplainabilityStore:
    """One create-only block blob per response; existing JSONL remains readable."""

    def __init__(self, account_url: str, container: str, *, client=None):
        if client is None:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient

            client = BlobServiceClient(account_url, credential=DefaultAzureCredential(),
                                       connection_timeout=5, read_timeout=10, retry_total=0)
        self._container = client.get_container_client(container)

    def append(self, record: ExplainabilityRecord) -> None:
        from azure.core.exceptions import AzureError, ResourceExistsError

        member = hashlib.sha256(record.member_ref.encode()).hexdigest()
        blob = self._container.get_blob_client(f"members/{member}/{record_key(record)}.json")
        payload = encoded(record).encode()
        try:
            try:
                blob.upload_blob(payload, overwrite=False)
            except ResourceExistsError:
                if blob.download_blob().readall() != payload:
                    raise AuditConflict("an audit identifier cannot be overwritten") from None
        except AzureError as error:
            raise AuditStorageUnavailable("immutable audit storage unavailable") from error

    def _read(self, prefix: str = "") -> list[dict]:
        from azure.core.exceptions import AzureError

        try:
            rows = []
            for item in self._container.list_blobs(name_starts_with=prefix):
                if not item.name.endswith((".json", ".jsonl")):
                    continue
                text = self._container.get_blob_client(item.name).download_blob().readall().decode("utf-8")
                rows.extend(asdict(ExplainabilityRecord.from_dict(json.loads(line)))
                            for line in text.splitlines() if line.strip())
            return rows
        except AzureError as error:
            raise AuditStorageUnavailable("immutable audit storage unavailable") from error

    def read_all(self) -> list[dict]:
        return self._read()

    def read_journey(self, journey_id: str) -> list[dict]:
        return [r for r in self.read_all() if r["journey_id"] == journey_id]

    def read_member(self, member_ref: str) -> list[dict]:
        prefix = "members/" + hashlib.sha256(member_ref.encode()).hexdigest() + "/"
        rows = self._read(prefix)
        # Legacy date-partitioned JSONL predates response-level identifiers.
        for digit in "0123456789":
            rows.extend(self._read(digit))
        return [r for r in rows if r["member_ref"] == member_ref]


class QueuedExplainabilityStore:
    def __init__(self, primary, enqueue: Callable[[ExplainabilityRecord], None]):
        self.primary, self.enqueue = primary, enqueue
        self.last_status = "committed"

    def append(self, record: ExplainabilityRecord) -> None:
        queue_errors: tuple[type[Exception], ...] = (OSError, RuntimeError)
        from azure.core.exceptions import AzureError

        queue_errors += (AzureError,)
        try:
            self.primary.append(record)
            self.last_status = "committed"
        except OSError as error:
            log.error("explainability_record_write_failed",
                      extra={"journey_id": record.journey_id, "error_type": type(error).__name__})
            try:
                self.enqueue(record)
            except queue_errors as queue_error:
                log.error("explainability_queue_unavailable",
                          extra={"journey_id": record.journey_id, "error_type": type(queue_error).__name__})
                raise AuditUnavailable("both audit storage and durable queue are unavailable") from queue_error
            self.last_status = "queued"

    def read_all(self) -> list[dict]:
        return self.primary.read_all()

    def read_journey(self, journey_id: str) -> list[dict]:
        return self.primary.read_journey(journey_id)

    def read_member(self, member_ref: str) -> list[dict]:
        return self.primary.read_member(member_ref)


def enqueue_in_transaction(transaction, record, audit: ExplainabilityRecord) -> None:
    previous = next((item for item in record.pending_audits if record_key(item) == record_key(audit)), None)
    if previous is not None:
        if encoded(previous) != encoded(audit):
            raise AuditConflict("queued audit identifier reused")
    else:
        record.pending_audits.append(audit)
    transaction.save(record)


def default_store(journey=None, enqueue=None):
    if not settings.explainability_account_url:
        raise ValueError("Azure explainability storage is required")
    primary = BlobExplainabilityStore(settings.explainability_account_url, settings.explainability_container)
    if enqueue is None and journey is not None:
        from governedgraph.orchestrator.store import BlobJourneyStore, JourneyRecord

        if not settings.journey_storage_url:
            raise ValueError("independent durable audit outbox is required")
        queue = BlobJourneyStore(settings.journey_storage_url)

        def enqueue(audit):
            with queue.transaction(journey.journey_id) as transaction:
                record = transaction.record or JourneyRecord(journey)
                enqueue_in_transaction(transaction, record, audit)
    return QueuedExplainabilityStore(primary, enqueue) if enqueue is not None else primary


def system_record(journey, response_id: str, kind: str) -> ExplainabilityRecord:
    return ExplainabilityRecord(
        journey_id=journey.journey_id, member_ref=journey.member_ref,
        strategy_id="S6", strategy_version="1.0", snapshot_id="none", arm="n/a",
        content_versions=[], value_ids=[], value_sources=[], model_deployment="deterministic",
        agent_name="gg-orchestrator", template_version="system-1",
        disclosure_given=True, escalation_offered=True, request_key=journey.request_key(),
        numeric_fidelity_passed=True, response_id=response_id, response_kind=kind,
    )
