"""Evidence and protective-action backends: local files for the demo, deployed stores on Azure.

Both backends project the same governed evidence. Neither reads the segregated cohort zone,
conversation text or member values; they read references, closed decisions and aggregates.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock

from governedgraph.admin.data import Context, build_overview, overview, tier1_decision, uplift_decision
from governedgraph.common.config import settings
from governedgraph.common.contracts import ExplainabilityRecord, SuspensionEvent
from governedgraph.strategy_service.service import StrategyService
from governedgraph.strategy_service.snapshot import RankingSnapshot
from governedgraph.strategy_service.state import LocalStateStore

log = logging.getLogger("governedgraph.admin")
STATE_KEYS = {"current", "last_good", "frozen", "suspensions"}
BOARD_OPTIONS = ["revise as new version", "retire", "reinstate with recorded justification"]


class AdminSourceError(RuntimeError):
    """An operator-safe explanation; its message may be shown in the console."""


def append_action(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        fcntl.flock(output, fcntl.LOCK_EX)
        output.write(json.dumps(record) + "\n")
        output.flush()
        os.fsync(output.fileno())


class LocalSources:
    mode = "local"

    def __init__(self, directory: Path, evidence_directory: Path, demo: bool, service: StrategyService | None = None):
        self.directory, self.evidence_directory, self.demo, self._service = directory, evidence_directory, demo, service

    @property
    def profile(self) -> str:
        return "demo" if self.demo else "runtime"

    def service(self) -> StrategyService:
        if self._service is None:
            state_path = self.evidence_directory / ("strategy" if self.demo else "strategy_service") / "state.json"
            self._service = StrategyService(store=LocalStateStore(state_path))
        return self._service

    def context(self) -> Context:
        return Context.from_service(self.service())

    def state(self) -> dict:
        return self.service().read_state()

    def overview(self) -> dict:
        return overview(self.service(), self.directory, self.evidence_directory)

    def audit_records(self) -> tuple[list[dict], bool]:
        from governedgraph.admin.data import audit_records

        return audit_records(self.evidence_directory), False

    def journal(self, record: dict) -> None:
        append_action(self.directory / "admin" / "actions.jsonl", record)

    def apply(self, action: str, event: SuspensionEvent | None) -> None:
        service = self.service()
        if action == "freeze":
            service.freeze()
        elif action == "revert_to_static":
            service.revert_to_static()
        elif event is not None:
            service.suspend(event)


@dataclass
class Containers:
    explainability: object
    aggregates: object
    evidence: object
    actions: object


class AzureSources:
    """Reads the deployed Blob stores and strategy state; acts only through the role-protected Strategy API."""

    mode = "azure"
    profile = "azure"

    def __init__(self, *, http, containers: Containers, strategy_client_factory: Callable[[], object],
                 context: Context | None = None, max_audit_blobs: int = 1000, cache_seconds: float = 30.0):
        self.http, self.containers = http, containers
        self._client_factory, self._client = strategy_client_factory, None
        self._context, self.max_audit_blobs, self.cache_seconds = context, max_audit_blobs, cache_seconds
        self._cache: dict[str, tuple[float, object]] = {}
        self._cache_lock = Lock()

    @classmethod
    def from_settings(cls) -> AzureSources:
        import httpx
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        required = {"STRATEGY_ADMIN_URL": settings.strategy_admin_url, "STRATEGY_API_CLIENT_ID": settings.strategy_api_client_id,
                    "EXPLAINABILITY_STORAGE_URL": settings.explainability_account_url,
                    "RANKING_STORAGE_URL": settings.ranking_storage_url}
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise AdminSourceError("Administration is not configured: set " + ", ".join(missing) + ".")
        credential = DefaultAzureCredential()
        options = {"credential": credential, "connection_timeout": 5, "read_timeout": 20, "retry_total": 1}
        records = BlobServiceClient(settings.explainability_account_url, **options)
        content = BlobServiceClient(settings.ranking_storage_url, **options)
        containers = Containers(
            records.get_container_client(settings.explainability_container),
            records.get_container_client("learning-aggregates"),
            content.get_container_client("learning-evidence"),
            records.get_container_client(os.getenv("GOVERNEDGRAPH_ADMIN_ACTIONS_CONTAINER", "admin-actions")),
        )

        def client():
            from governedgraph.strategy_service.client import StrategyAdminClient

            return StrategyAdminClient(protective_only=True, credential=credential)

        return cls(http=httpx.Client(base_url=settings.strategy_admin_url.rstrip("/") + "/", timeout=15),
                   containers=containers, strategy_client_factory=client,
                   max_audit_blobs=int(os.getenv("GOVERNEDGRAPH_ADMIN_MAX_AUDIT_BLOBS", "1000")))

    # ----- small helpers -----
    def _cached(self, key: str, load: Callable[[], object]):
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < self.cache_seconds:
                return hit[1]
        value = load()
        with self._cache_lock:
            self._cache[key] = (time.monotonic(), value)
        return value

    @staticmethod
    def _names(container, prefix: str, limit: int, suffixes: tuple[str, ...]) -> tuple[list[str], bool]:
        names: list[str] = []
        for item in container.list_blobs(name_starts_with=prefix):
            if not item.name.endswith(suffixes):
                continue
            if len(names) >= limit:
                return names, True
            names.append(item.name)
        return names, False

    @staticmethod
    def _download(container, names: list[str]) -> list[bytes]:
        with ThreadPoolExecutor(max_workers=8) as pool:
            return list(pool.map(lambda name: container.get_blob_client(name).download_blob().readall(), names))

    # ----- strategy state -----
    def context(self) -> Context:
        if self._context is None:
            self._context = Context.load()
        return self._context

    def state(self) -> dict:
        import httpx

        try:
            response = self.http.get("snapshots")
            response.raise_for_status()
            state = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise AdminSourceError("The Strategy service state is unavailable; refresh shortly.") from error
        try:
            if not isinstance(state, dict) or set(state) != STATE_KEYS or not isinstance(state["frozen"], bool):
                raise ValueError("unexpected strategy state shape")
            for field in ("current", "last_good"):
                if state[field] is not None:
                    RankingSnapshot.from_dict(state[field])
            for entry in state["suspensions"]:
                SuspensionEvent.from_dict(entry)
        except (ValueError, TypeError, KeyError) as error:
            raise AdminSourceError("The Strategy service returned state in an unexpected format.") from error
        return state

    # ----- evidence -----
    def _cases(self, state: dict) -> list[dict]:
        # Case files stay beside the segregated cohort audit; only the suspension references are projected here.
        return [{
            "case_id": row["governance_case_id"], "type": "automatic_suspension", "tier": "Tier 2 to reinstate",
            "audit_id": row["audit_id"], "created_at": row["created_at"], "options_for_board": BOARD_OPTIONS,
            "finding": {"request_key": row["journey_state"], "strategy_id": row["strategy_id"],
                        "strategy_version": row["strategy_version"], "gap_pts": row["gap_metric"], "breach": True},
        } for row in state["suspensions"]]

    def _decisions(self) -> list[dict]:
        try:
            tier1, _ = self._names(self.containers.evidence, "tier1/", 200, (".json",))
            uplift, _ = self._names(self.containers.evidence, "uplift/", 200, (".json",))
            rows = []
            for name, raw in zip(tier1 + uplift, self._download(self.containers.evidence, tier1 + uplift), strict=True):
                pack = json.loads(raw)
                rows.append(tier1_decision(pack) if name.startswith("tier1/") else uplift_decision(pack))
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise AdminSourceError("Assurance evidence is unavailable or unreadable.") from error
        except Exception as error:
            log.error("admin_source_failed", extra={"source": "assurance", "error_type": type(error).__name__})
            raise AdminSourceError("Assurance evidence storage is unavailable; check the identity's read access.") from error
        return sorted(rows, key=lambda row: str(row.get("created_at", "")), reverse=True)

    def _learning(self) -> dict:
        from azure.core.exceptions import ResourceNotFoundError

        from governedgraph.admin.data import project_learning
        from governedgraph.learning.feedback import FeedbackManifest
        from governedgraph.learning.feedback_store import snapshot_name
        from governedgraph.learning.ranking_pipeline import load_outcome_aggregates

        try:
            try:
                raw = self.containers.aggregates.get_blob_client("current.json").download_blob().readall()
            except ResourceNotFoundError:
                return project_learning(None, {})
            manifest = FeedbackManifest.from_dict(json.loads(raw))
            payload = self.containers.aggregates.get_blob_client(snapshot_name(manifest.outcome_digest)).download_blob().readall()
            if hashlib.sha256(payload).hexdigest() != manifest.outcome_digest:
                raise ValueError("learning aggregate digest mismatch")
            with tempfile.TemporaryDirectory(prefix="gg-admin-") as directory:
                path = Path(directory) / "outcomes.csv"
                path.write_bytes(payload)
                return project_learning(manifest, load_outcome_aggregates(path))
        except (ValueError, TypeError, KeyError) as error:
            raise AdminSourceError("Learning aggregates failed verification; do not rely on them.") from error
        except Exception as error:
            log.error("admin_source_failed", extra={"source": "learning", "error_type": type(error).__name__})
            raise AdminSourceError("Learning aggregate storage is unavailable; check the identity's read access.") from error

    def actions(self) -> list[dict]:
        try:
            names, _ = self._names(self.containers.actions, "", 500, (".json",))
            rows = [json.loads(raw) for raw in self._download(self.containers.actions, names)]
        except (ValueError, TypeError) as error:
            raise AdminSourceError("The admin journal contains an unreadable record.") from error
        except Exception as error:
            log.error("admin_source_failed", extra={"source": "journal", "error_type": type(error).__name__})
            raise AdminSourceError("The admin journal is unavailable; check the identity's access.") from error
        return sorted(rows, key=lambda row: (str(row.get("created_at", "")), str(row.get("status", ""))))

    def overview(self) -> dict:
        state = self.state()
        return build_overview(self.context(), state, mode="azure", cases=self._cases(state),
                              decisions=self._decisions(), learning=self._learning(), actions=self.actions())

    def audit_records(self) -> tuple[list[dict], bool]:
        def load() -> tuple[list[dict], bool]:
            try:
                names, truncated = self._names(self.containers.explainability, "", self.max_audit_blobs, (".json", ".jsonl"))
                rows = []
                for raw in self._download(self.containers.explainability, names):
                    rows.extend(asdict(ExplainabilityRecord.from_dict(json.loads(line)))
                                for line in raw.decode("utf-8").splitlines() if line.strip())
            except (ValueError, TypeError, KeyError) as error:
                raise AdminSourceError("A response audit record is unreadable; investigate before relying on this view.") from error
            except Exception as error:
                log.error("admin_source_failed", extra={"source": "audit", "error_type": type(error).__name__})
                raise AdminSourceError("Response audit storage is unavailable; check the identity's read access.") from error
            return rows, truncated

        return self._cached("audit", load)

    # ----- protective actions -----
    def journal(self, record: dict) -> None:
        from azure.core.exceptions import AzureError

        blob = self.containers.actions.get_blob_client(f"{record['action_id']}/{record['status']}.json")
        try:
            blob.upload_blob(json.dumps(record).encode(), overwrite=False)
        except AzureError as error:
            raise AdminSourceError("The admin journal is unavailable. No protective action was attempted.") from error

    def apply(self, action: str, event: SuspensionEvent | None) -> None:
        import httpx
        from azure.core.exceptions import ClientAuthenticationError

        try:
            if self._client is None:
                self._client = self._client_factory()
            if action == "freeze":
                self._client.freeze()
            elif action == "revert_to_static":
                self._client.revert_to_static()
            elif event is not None:
                self._client.suspend(event)
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            if status in (401, 403):
                raise AdminSourceError(
                    "The Strategy service refused the administration identity. Assign it the Strategy.Freeze and "
                    "Strategy.Suspend application roles; see docs/administration.md.") from error
            raise AdminSourceError(f"The Strategy service rejected the action (HTTP {status}).") from error
        except httpx.HTTPError as error:
            raise AdminSourceError("The Strategy service is unreachable; no change was confirmed.") from error
        except ClientAuthenticationError as error:
            raise AdminSourceError("Could not obtain a Microsoft Entra token for the Strategy service.") from error
        with self._cache_lock:
            self._cache.clear()
