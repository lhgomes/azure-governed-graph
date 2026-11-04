"""Least-privilege audit-status and create-only recovery-journal adapters."""
from __future__ import annotations

import logging
import json
from urllib.error import URLError

from governedgraph.common.config import settings
from governedgraph.common.io import canonical_json
from governedgraph.learning.container_jobs import execution_rows
from governedgraph.strategy_service.recovery import DecisionReferenceUsed, latest_successful_audit, validate_decision_ref
from governedgraph.strategy_service.state import StateUnavailable

log = logging.getLogger("governedgraph.strategy_service")


def audit_health() -> str:
    from azure.core.exceptions import AzureError
    from azure.identity import DefaultAzureCredential

    if (not settings.is_azure or settings.environment_name != "gg-dev"
            or not settings.p2_fairness_job_id.endswith("/Microsoft.App/jobs/job-gg-p2-fairness")
            or "@sha256:" not in settings.p2_image):
        raise StateUnavailable("dev recovery audit monitor is not configured")
    try:
        with DefaultAzureCredential() as credential:
            return latest_successful_audit(execution_rows(credential, settings.p2_fairness_job_id), settings.p2_image)
    except (AzureError, URLError, TimeoutError, ValueError, KeyError, TypeError) as error:
        log.error("strategy_recovery_audit_health_unavailable")
        raise StateUnavailable("latest isolated audit health is unavailable") from error


class BlobRecoveryJournal:
    def __init__(self, container):
        self.container = container

    def used_decision_refs(self) -> set[str]:
        from azure.core.exceptions import AzureError

        try:
            references = set()
            for blob in self.container.list_blobs(name_starts_with="human-recovery/"):
                record = json.loads(self.container.get_blob_client(blob.name).download_blob().readall())
                reference = record["decision_ref"]
                validate_decision_ref(reference)
                references.add(reference)
            return references
        except (AzureError, ValueError, KeyError, TypeError) as error:
            log.error("strategy_recovery_history_unavailable")
            raise StateUnavailable("decision-reference reuse check unavailable; recovery history cannot be certified") from error

    def write(self, identifier: str, stage: str, record: dict) -> None:
        from azure.core.exceptions import AzureError, ResourceExistsError

        validate_decision_ref(record["decision_ref"])
        # A deterministic create-only intent claims the decision across replicas.
        name = f"decision-{record['decision_ref']}" if stage == "requested" else identifier
        blob = self.container.get_blob_client(f"human-recovery/{name}-{stage}.json")
        raw = canonical_json(record)
        try:
            blob.upload_blob(raw, overwrite=False)
            if blob.download_blob().readall() != raw:
                raise StateUnavailable("recovery journal read-back differs")
        except ResourceExistsError as error:
            if stage == "requested":
                raise DecisionReferenceUsed(
                    f"decision-reference reuse check failed: detected {record['decision_ref']!r}; already recorded",
                ) from error
            log.error("strategy_recovery_journal_unavailable", extra={"recovery_id": identifier, "stage": stage})
            raise StateUnavailable("durable recovery journal already exists") from error
        except AzureError as error:
            log.error("strategy_recovery_journal_unavailable", extra={"recovery_id": identifier, "stage": stage})
            raise StateUnavailable("durable recovery journal is unavailable") from error
