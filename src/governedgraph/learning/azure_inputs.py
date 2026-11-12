"""Identity-isolated Blob adapters for aggregate ranking and the fairness hand-off."""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from governedgraph.agents.fairness_explainability import AuditReport, FairnessExplainabilityAgent
from governedgraph.common.config import settings
from governedgraph.common.contracts import RequestKey
from governedgraph.learning.ranking_pipeline import load_outcome_aggregates, load_thresholds
from governedgraph.learning.feedback import FeedbackManifest
from governedgraph.learning.feedback_store import snapshot_name
from governedgraph.strategy_service.client import StrategyAdminClient

WATERMARK = "ILLUSTRATIVE - synthetic data"
log = logging.getLogger("governedgraph.learning")


class BlobLearningInputs:
    def __init__(self, *, credential=None):
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        if not settings.ranking_storage_url or not settings.explainability_account_url:
            raise ValueError("learning storage endpoints are required")
        if credential is None:
            credential = DefaultAzureCredential()
        self.aggregates = BlobServiceClient(
            settings.explainability_account_url, credential=credential,
        ).get_container_client("learning-aggregates")
        self.private = BlobServiceClient(settings.ranking_storage_url, credential=credential)

    def outcome_bytes(self) -> bytes:
        from azure.core.exceptions import ResourceNotFoundError

        if not hasattr(self, "_outcome_blob"):
            try:
                raw = self.aggregates.get_blob_client("current.json").download_blob().readall()
            except ResourceNotFoundError:
                log.info("learning_source_initial_synthetic_history")
                self._outcome_blob, self._outcome_digest = "outcomes.csv", None
            else:
                manifest = FeedbackManifest.from_dict(json.loads(raw))
                self._outcome_blob = snapshot_name(manifest.outcome_digest)
                self._outcome_digest = manifest.outcome_digest
        payload = self.aggregates.get_blob_client(self._outcome_blob).download_blob().readall()
        if self._outcome_digest is not None and hashlib.sha256(payload).hexdigest() != self._outcome_digest:
            raise ValueError("pinned feedback aggregate digest mismatch")
        return payload

    def pin_outcome_digest(self, digest: str) -> None:
        from azure.core.exceptions import ResourceNotFoundError

        path = snapshot_name(digest)
        try:
            payload = self.aggregates.get_blob_client(path).download_blob().readall()
        except ResourceNotFoundError:
            path = "outcomes.csv"
            payload = self.aggregates.get_blob_client(path).download_blob().readall()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError("fairness-reviewed aggregate version is unavailable")
        self._outcome_blob, self._outcome_digest = path, digest

    def ranking_inputs(self, audit_id: str, thresholds: dict) -> tuple[dict, AuditReport]:
        if not audit_id or "/" in audit_id or "\\" in audit_id:
            raise ValueError("an explicit fairness audit ID is required")
        outcomes = self.outcome_bytes()
        raw = self.private.get_blob_client("learning-audits", audit_id + ".json").download_blob().readall()
        report = json.loads(raw)
        expected = {"audit_id", "threshold_version", "outcome_digest", "vetoes", "created_at", "watermark"}
        if (set(report) != expected or report["audit_id"] != audit_id
                or report["threshold_version"] != thresholds["threshold_version"]
                or report["outcome_digest"] != hashlib.sha256(outcomes).hexdigest()
                or report["watermark"] != WATERMARK):
            raise ValueError("fairness hand-off does not match the approved thresholds and input aggregates")
        if not isinstance(report["vetoes"], list):
            raise ValueError("invalid fairness veto contract")
        vetoes = []
        for veto in report["vetoes"]:
            if not isinstance(veto, list) or len(veto) != 2 or not all(isinstance(v, str) for v in veto):
                raise ValueError("fairness hand-off permits request-key/strategy pairs only; no cohort attributes")
            key, strategy = veto
            RequestKey(*key.split("|"))
            vetoes.append((key, strategy, "protective-veto"))
        with TemporaryDirectory(prefix="gg-aggregates-") as directory:
            path = Path(directory) / "outcomes.csv"
            path.write_bytes(outcomes)
            evidence = load_outcome_aggregates(path)
        return evidence, AuditReport(audit_id, report["threshold_version"], thresholds["max_cohort_gap_pts"],
                                     [], vetoes, report["created_at"])

    def run_fairness(self, service: StrategyAdminClient, *, expected_digest: str | None = None) -> str:
        completed = False
        try:
            audit_id = self._run_fairness(service, expected_digest=expected_digest)
            completed = True
            return audit_id
        finally:
            if not completed:
                log.error("fairness_component_failed_freezing")
                service.freeze()

    def _run_fairness(self, service: StrategyAdminClient, *, expected_digest: str | None = None) -> str:
        from governedgraph.learning.fairlearn_audit import run_metricframe_audit

        thresholds = load_thresholds()
        outcomes = self.outcome_bytes()
        if expected_digest is not None and hashlib.sha256(outcomes).hexdigest() != expected_digest:
            raise ValueError("aggregate input changed before the isolated audit")
        cohort_zone = self.private.get_container_client("cohort-zone")
        raw = cohort_zone.get_blob_client("cohort_aggregates.csv").download_blob().readall()
        with TemporaryDirectory(prefix="gg-cohort-") as directory:
            root = Path(directory)
            cohort = root / "cohort.csv"
            cohort.write_bytes(raw)
            report = run_metricframe_audit(thresholds, cohort)
            agent = FairnessExplainabilityAgent(service, governance_dir=root / "governance")
            agent.apply_veto_gate(report)
            cohort_zone.get_blob_client("audits/" + report.audit_id + ".json").upload_blob(
                json.dumps({"watermark": WATERMARK, **asdict(report)}), overwrite=False,
            )
            cases = root / "governance" / "cases.jsonl"
            if cases.exists():
                cohort_zone.get_blob_client("governance/" + report.audit_id + ".jsonl").upload_blob(
                    cases.read_bytes(), overwrite=False,
                )
        projection = {
            "audit_id": report.audit_id, "threshold_version": report.threshold_version,
            "outcome_digest": hashlib.sha256(outcomes).hexdigest(), "created_at": report.created_at,
            "vetoes": sorted({(key, sid) for key, sid, _ in report.vetoes}), "watermark": WATERMARK,
        }
        self.private.get_blob_client("learning-audits", report.audit_id + ".json").upload_blob(
            json.dumps(projection), overwrite=False,
        )
        return report.audit_id

    def save_evidence(self, kind: str, identifier: str, payload: dict) -> None:
        import re

        from azure.core.exceptions import ResourceExistsError

        if kind not in {"tier1", "uplift"} or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier):
            raise ValueError("invalid learning evidence identity")
        encoded = json.dumps({"watermark": WATERMARK, **payload}, sort_keys=True).encode()
        blob = self.private.get_blob_client("learning-evidence", f"{kind}/{identifier}.json")
        try:
            blob.upload_blob(encoded, overwrite=False)
        except ResourceExistsError:
            log.info("learning_evidence_already_exists", extra={"evidence_kind": kind, "evidence_id": identifier})
        if blob.download_blob().readall() != encoded:
            raise ValueError("immutable learning evidence read-back conflict")

    def uplift_input(self) -> dict:
        return json.loads(self.aggregates.get_blob_client("uplift.json").download_blob().readall())


if __name__ == "__main__":
    print("FAIRNESS_AUDIT_ID=" + BlobLearningInputs().run_fairness(StrategyAdminClient(protective_only=True)))
