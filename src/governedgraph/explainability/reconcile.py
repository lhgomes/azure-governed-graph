"""Retry durable audit outboxes and verify exact response-to-record cardinality."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
import logging
import os

from governedgraph.common.config import settings
from governedgraph.common.contracts import JourneyEvent
from governedgraph.explainability.store import default_store
from governedgraph.orchestrator.store import BlobJourneyStore, FileJourneyStore, validate_id

log = logging.getLogger("governedgraph.explainability")
LEGACY_CUTOVER_ENV = "GOVERNEDGRAPH_LEGACY_CUTOVER_UTC"
LEGACY_LABEL = "legacy, untracked (synthetic dev history)"
DELIVERY_EVENTS = {
    "disclosed_and_authenticated", "response_delivered", "no_teach_back",
    "teach_back_passed", "escalate", "output_delivered",
}


def parse_cutover(cutover_utc: str, environment_name: str):
    """Optional gg-dev-only boundary before which deliveries are reported as legacy; never set in a fresh deployment."""
    if environment_name != "gg-dev" or not cutover_utc:
        return None
    try:
        cutover = datetime.fromisoformat(cutover_utc)
    except ValueError as error:
        raise ValueError(f"{LEGACY_CUTOVER_ENV} must be an ISO-8601 timestamp with a timezone") from error
    if cutover.tzinfo is None or cutover.utcoffset() is None:
        raise ValueError(f"{LEGACY_CUTOVER_ENV} requires a timezone")
    return cutover


def reconciliation_report(events: list[JourneyEvent], records: list[dict], *,
                          environment_name: str = "", cutover_utc: str = "") -> dict:
    cutover = parse_cutover(cutover_utc, environment_name)

    def in_scope(event: JourneyEvent) -> bool:
        if cutover is None:
            return True
        try:
            timestamp = datetime.fromisoformat(event.created_at)
        except (TypeError, ValueError) as error:
            raise ValueError("invalid audit event timestamp") from error
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("audit event timestamp requires a timezone")
        return timestamp >= cutover

    unique = {}
    conflicting = []
    for event in events:
        key = (event.journey_id, event.event_id)
        if key in unique and unique[key] != event and (in_scope(event) or in_scope(unique[key])):
            conflicting.append(event.event_id)
        unique[key] = event
    deliveries = [e for e in unique.values() if not e.response_withheld
                  and (e.response_id or e.event in DELIVERY_EVENTS)]
    expected = [e for e in deliveries if in_scope(e)]
    legacy_count = len(deliveries) - len(expected)
    counts = Counter((r["journey_id"], r["response_id"]) for r in records if r.get("response_id"))
    references = Counter((e.journey_id, e.response_id) for e in expected if e.response_id)
    missing, duplicates = [], []
    matched = 0
    for event in expected:
        key = (event.journey_id, event.response_id)
        item = {"journey_id": event.journey_id, "event_id": event.event_id, "response_id": event.response_id}
        if not event.response_id or counts[key] == 0:
            missing.append(item)
        elif counts[key] != 1 or references[key] != 1:
            duplicates.append(item)
        else:
            matched += 1
    return {
        "data": "ILLUSTRATIVE - synthetic data",
        "cutover_utc": cutover_utc if cutover is not None else None,
        "legacy_untracked": {"label": LEGACY_LABEL, "count": legacy_count} if cutover is not None else None,
        "expected_responses": len(expected), "matched_responses": matched,
        "coverage_percent": 100 * matched / len(expected) if expected else None,
        "complete": bool(expected) and matched == len(expected) and not conflicting,
        "missing": missing, "duplicates": duplicates, "conflicting_event_ids": sorted(set(conflicting)),
        "legacy_unlinked_responses": sum(e.response_id is None for e in expected),
        "withheld_responses": sum(e.response_withheld for e in unique.values()),
        "unreferenced_record_ids": sorted({r["response_id"] for r in records
                                           if r.get("response_id")
                                           and (r["journey_id"], r["response_id"]) not in references}),
    }


def retry_audits(transaction, record, store) -> None:
    while record.pending_audits:
        try:
            store.append(record.pending_audits[0])
        except OSError as error:
            log.error("explainability_record_write_failed",
                      extra={"journey_id": record.journey.journey_id, "error_type": type(error).__name__})
            break
        record.pending_audits.pop(0)
        transaction.save(record)


def run_reconciliation(journeys, records, journey_ids: list[str] | None = None) -> dict:
    events = []
    pending = 0
    ids = list(dict.fromkeys(journey_ids if journey_ids is not None else journeys.ids()))
    for journey_id in ids:
        validate_id(journey_id)
        with journeys.transaction(journey_id) as transaction:
            if transaction.record is None:
                raise ValueError("reconciliation journey does not exist")
            record = transaction.record
            retry_audits(transaction, record, records)
            events.extend(record.journey.events)
            pending += len(record.pending_audits)
    rows = records.read_all()
    if journey_ids is not None:
        selected = set(journey_ids)
        rows = [row for row in rows if row["journey_id"] in selected]
    report = reconciliation_report(events, rows, environment_name=settings.environment_name,
                                   cutover_utc=os.environ.get(LEGACY_CUTOVER_ENV, ""))
    report["complete"] = report["complete"] and pending == 0
    report["pending_records"] = pending
    report["scope"] = {"journey_ids": sorted(ids)}
    log.info("explainability_reconciled",
             extra={"expected_responses": report["expected_responses"],
                    "matched_responses": report["matched_responses"],
                    "coverage_percent": report["coverage_percent"], "pending_records": pending})
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journey-id", action="append")
    args = parser.parse_args()
    failures: tuple[type[Exception], ...] = (OSError, RuntimeError, ValueError)
    if settings.is_azure:
        from azure.core.exceptions import AzureError

        failures += (AzureError,)
    try:
        journeys = (BlobJourneyStore(settings.journey_storage_url) if settings.is_azure
                    else FileJourneyStore(settings.run_dir / "journeys"))
        report = run_reconciliation(journeys, default_store(), args.journey_id)
    except failures as error:
        log.error("explainability_reconciliation_failed", extra={"error_type": type(error).__name__})
        print(json.dumps({"complete": False, "error_type": type(error).__name__}))
        raise SystemExit(1) from None
    if legacy := report["legacy_untracked"]:
        print(f"{legacy['label']}: {legacy['count']}")
    print(json.dumps(report, sort_keys=True))
    raise SystemExit(0 if report["complete"] else 1)


if __name__ == "__main__":
    main()
