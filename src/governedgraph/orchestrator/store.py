"""Reference-only journey snapshots and outbox, committed together."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
import fcntl
import json
import os
from pathlib import Path
import re
import threading
from typing import Callable, Generic, Iterator, Protocol, TypeVar

from governedgraph.common.contracts import EscalationRequest, ExplainabilityRecord, JourneyEvent, _strict
from governedgraph.orchestrator.state_machine import Journey


class JourneyBusy(RuntimeError):
    pass


class LeaseLost(RuntimeError):
    pass


def validate_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", value):
        raise ValueError("invalid journey/request identifier")
    return value


def validate_message_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_:-]{0,127}", value):
        raise ValueError("invalid message identifier")
    return value


def atomic_write(path: Path, text: str) -> None:
    temporary = path.with_suffix(".tmp")
    try:
        with temporary.open("w") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass
class Delivery:
    queue: str
    message_id: str
    payload: dict

    @classmethod
    def from_dict(cls, payload: dict) -> "Delivery":
        result = _strict(cls, payload)
        contract = {"journey-events": JourneyEvent, "escalations": EscalationRequest}.get(result.queue)
        if contract is None:
            raise ValueError("unknown outbox queue")
        contract.from_dict(result.payload)
        validate_message_id(result.message_id)
        return result


@dataclass
class JourneyRecord:
    journey: Journey
    pending: list[Delivery] = field(default_factory=list)
    processed_requests: list[str] = field(default_factory=list)
    escalation_sequence: int = 0
    pending_audits: list[ExplainabilityRecord] = field(default_factory=list)

    def encode(self) -> str:
        return json.dumps({"journey": self.journey.to_dict(),
                           "pending": [asdict(item) for item in self.pending],
                           "processed_requests": self.processed_requests,
                           "escalation_sequence": self.escalation_sequence,
                           "pending_audits": [asdict(item) for item in self.pending_audits]}, sort_keys=True)

    @classmethod
    def decode(cls, text: str) -> "JourneyRecord":
        result = _strict(cls, json.loads(text))
        result.journey = Journey.from_dict(result.journey)
        result.pending = [Delivery.from_dict(item) for item in result.pending]
        result.pending_audits = [ExplainabilityRecord.from_dict(item) for item in result.pending_audits]
        for request_id in result.processed_requests:
            validate_id(request_id)
        return result


class Record(Protocol):
    def encode(self) -> str: ...


T = TypeVar("T", bound=Record)


@dataclass
class Transaction(Generic[T]):
    record: T | None
    write: Callable[[str], None]

    def save(self, record: T) -> None:
        self.write(record.encode())
        self.record = record


class JourneyStore(Protocol):
    def transaction(self, journey_id: str): ...
    def ids(self) -> Iterator[str]: ...


class FileRecordStore(Generic[T]):
    """Local offline adapter; atomic rename under a cross-process file lock."""

    def __init__(self, directory: Path, decoder: Callable[[str], T]):
        self.directory = directory
        self.decoder = decoder
        directory.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def transaction(self, journey_id: str):
        validate_id(journey_id)
        path = self.directory / f"{journey_id}.json"
        with (self.directory / f"{journey_id}.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise JourneyBusy(journey_id) from error
            try:
                yield Transaction(self.decoder(path.read_text()) if path.exists() else None,
                                  lambda text: atomic_write(path, text))
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def ids(self) -> Iterator[str]:
        return (path.stem for path in self.directory.glob("*.json"))


class FileJourneyStore(FileRecordStore[JourneyRecord]):
    def __init__(self, directory: Path):
        super().__init__(directory, JourneyRecord.decode)


class BlobRecordStore(Generic[T]):
    """One blob per journey; renewable leases serialize requests across replicas."""

    def __init__(self, account_url: str, container: str, decoder: Callable[[str], T]):
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        self.credential = DefaultAzureCredential()
        self.client = BlobServiceClient(account_url, credential=self.credential,
                                       connection_timeout=10, read_timeout=20, retry_total=0)
        self.container = self.client.get_container_client(container)
        self.decoder = decoder

    @contextmanager
    def transaction(self, journey_id: str):
        from azure.core.exceptions import AzureError, HttpResponseError, ResourceExistsError

        validate_id(journey_id)
        blob = self.container.get_blob_client(f"{journey_id}.json")
        if not blob.exists():
            try:
                blob.upload_blob("null", overwrite=False)
            except ResourceExistsError as error:
                if error.error_code != "BlobAlreadyExists":
                    raise
                from governedgraph.agents.foundry_tracing import dependency_outcome

                dependency_outcome(journey_id, "blob_create_if_absent", "already_exists", error)
            except HttpResponseError as error:
                if error.error_code in {"LeaseAlreadyPresent", "LeaseIdMissing"}:
                    raise JourneyBusy(journey_id) from error
                raise
        try:
            lease = blob.acquire_lease(lease_duration=60)
        except HttpResponseError as error:
            if error.error_code in {"LeaseAlreadyPresent", "LeaseIdMissing"}:
                raise JourneyBusy(journey_id) from error
            raise
        stopped = threading.Event()
        errors: list[Exception] = []

        def renew() -> None:
            while not stopped.wait(15):
                try:
                    lease.renew()
                except AzureError as error:
                    errors.append(error)
                    return

        worker = threading.Thread(target=renew, name="journey-lease", daemon=True)
        worker.start()
        try:
            text = blob.download_blob(lease=lease).readall().decode()

            def write(value: str) -> None:
                if errors:
                    raise LeaseLost(journey_id) from errors[0]
                blob.upload_blob(value, overwrite=True, lease=lease)

            yield Transaction(None if text == "null" else self.decoder(text), write)
            if errors:
                raise LeaseLost(journey_id) from errors[0]
        finally:
            stopped.set()
            worker.join()
            lease.release()

    def ids(self) -> Iterator[str]:
        return (blob.name.removesuffix(".json") for blob in self.container.list_blobs()
                if blob.name.endswith(".json"))


class BlobJourneyStore(BlobRecordStore[JourneyRecord]):
    def __init__(self, account_url: str):
        super().__init__(account_url, "journey-state", JourneyRecord.decode)
