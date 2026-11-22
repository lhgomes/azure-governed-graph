"""Scheduled gap review: read declined interactions, write inert improvement proposals for the administration console.

Runs under its own identity with read access to response records, journey state, kept declined questions and
owner-approved passages, and write access to the proposal container only. It cannot sign, publish, change a catalogue or
write an approval (B3, B4, B8). When the drafting agent is configured, it also asks that agent for a passage
suggestion for each new content proposal and for a reasoned style recommendation for each style gap. A failed suggestion
is dropped: the content idea stays without a draft, and a style idea is not offered until the agent answers.

    python -m governedgraph.learning.gap_review_job
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from governedgraph.common.config import DATA_DIR, settings
from governedgraph.common.contracts import ExplainabilityRecord
from governedgraph.explainability.declined_questions import CAPTURED_REASONS, DeclinedQuestion
from governedgraph.learning import gap_review, improvement_store
from governedgraph.search.corpus import load_corpus

log = logging.getLogger("governedgraph.learning.gap_review")
DEFAULT_LOOKBACK_DAYS = 30
# Fail closed: no proposal may be based on a truncated response-record scan.
MAX_RECORDS = 25000
MAX_RESPONSE_BLOB_BYTES = 1048576
MAX_TOTAL_RESPONSE_BYTES = 134217728


class GapReviewCapacityError(RuntimeError):
    """The batch exceeds its safe capacity; an operator must investigate."""



def read_records(container, since: datetime, *, max_records: int = MAX_RECORDS,
                 max_total_bytes: int = MAX_TOTAL_RESPONSE_BYTES) -> list[ExplainabilityRecord]:
    """Bounded full-lookback scan. Abort on capacity limits; never analyze only part."""
    records = []
    consumed = 0
    if max_records <= 0 or max_total_bytes <= 0:
        raise ValueError("gap review capacity must be positive")
    for item in container.list_blobs(name_starts_with=""):
        if not item.name.endswith((".json", ".jsonl")) or item.last_modified < since:
            continue
        if getattr(item, "size", 0) > MAX_RESPONSE_BLOB_BYTES:
            raise GapReviewCapacityError("oversized response blob")
        raw = container.get_blob_client(item.name).download_blob().readall()
        consumed += len(raw)
        if len(raw) > MAX_RESPONSE_BLOB_BYTES or consumed > max_total_bytes:
            raise GapReviewCapacityError("response bytes exceed the safe scan budget")
        for line in raw.decode("utf-8").splitlines():
            if not line.strip():
                continue
            if len(records) >= max_records:
                raise GapReviewCapacityError("response count exceeds the safe scan budget")
            records.append(ExplainabilityRecord.from_dict(json.loads(line)))
    return records

def excluded_test_journeys(journeys, journey_ids: set[str]) -> set[str]:
    """Selected-style test journeys, and journeys that cannot be verified, are left out of every count."""
    from azure.core.exceptions import ResourceNotFoundError

    from governedgraph.orchestrator.store import JourneyRecord, validate_id

    excluded = set()
    for journey_id in sorted(journey_ids):
        validate_id(journey_id)
        try:
            text = journeys.get_blob_client(f"{journey_id}.json").download_blob().readall().decode("utf-8")
        except ResourceNotFoundError:
            excluded.add(journey_id)
            continue
        if text == "null" or JourneyRecord.decode(text).journey.test_strategy_id:
            excluded.add(journey_id)
    return excluded


def read_questions(container, response_ids: set[str]) -> dict[str, str]:
    """Kept question text for the given responses. A missing blob (expired or never kept) is simply absent."""
    from azure.core.exceptions import ResourceNotFoundError

    found = {}
    for response_id in sorted(response_ids):
        try:
            raw = container.get_blob_client(f"questions/{response_id}.json").download_blob().readall()
        except ResourceNotFoundError:
            continue
        question = DeclinedQuestion.from_dict(json.loads(raw))
        if question.response_id != response_id:
            raise ValueError("kept question does not match its response")
        found[response_id] = question.text
    return found


def supplemental_feedback_counts(records, journeys, *, since: datetime, now: datetime) -> dict:
    """Synthetic-only observed outcome diagnostics with cross-journey attribution.

    Walk the *originating* and subsequent journey states, since a linked contact
    is recorded on the later journey. No member text is collected or exported.
    Listing must be complete within the bounded budget; missing origin states
    are explicitly flagged, never counted as successful feedback.
    """
    from azure.core.exceptions import ResourceNotFoundError
    from governedgraph.orchestrator.store import JourneyRecord, validate_id

    eligible: dict[tuple[str, str], tuple[str, datetime]] = {}
    for record in records:
        if record.response_kind != "explanation" or not record.response_id:
            continue
        intent = next((item.decision for item in reversed(record.decision_evidence)
                       if item.role == "intent"), None)
        if not intent or intent.get("route") != "explanation":
            continue
        identity = (record.journey_id, record.response_id)
        created = datetime.fromisoformat(record.created_at.replace("Z", "+00:00"))
        if created.tzinfo is None or created < since or created > now:
            raise ValueError("feedback reference has an invalid observation timestamp")
        prior = eligible.setdefault(identity, (record.member_ref, created))
        if prior != (record.member_ref, created):
            raise ValueError("conflicting feedback source identity")

    origins: dict[str, str] = {}
    for (journey_id, _), (member_ref, _) in eligible.items():
        if journey_id in origins and origins[journey_id] != member_ref:
            raise ValueError("conflicting member reference for one journey")
        origins[journey_id] = member_ref

    negative: set[tuple[str, str]] = set()
    followups: set[tuple[str, str]] = set()
    ambiguous = 0
    seen_origins: set[str] = set()
    ineligible_origins: set[str] = set()
    scanned = total_bytes = 0
    for item in journeys.list_blobs(name_starts_with=""):
        if not item.name.endswith(".json") or "/" in item.name:
            continue
        scanned += 1
        if scanned > MAX_RECORDS:
            raise GapReviewCapacityError("journey inventory exceeds the safe scan budget")
        journey_id = validate_id(item.name[:-5])
        if getattr(item, "size", 0) > MAX_RESPONSE_BLOB_BYTES:
            raise GapReviewCapacityError("oversized journey state during feedback review")
        try:
            raw = journeys.get_blob_client(item.name).download_blob().readall()
        except ResourceNotFoundError:
            if journey_id in origins:
                ineligible_origins.add(journey_id)
            continue
        total_bytes += len(raw)
        if len(raw) > MAX_RESPONSE_BLOB_BYTES or total_bytes > MAX_TOTAL_RESPONSE_BYTES:
            raise GapReviewCapacityError("journey bytes exceed the safe scan budget")
        if raw == b"null":
            if journey_id in origins:
                ineligible_origins.add(journey_id)
            continue
        stored = JourneyRecord.decode(raw.decode("utf-8")).journey
        if stored.journey_id != journey_id:
            raise ValueError("journey state identity does not match its storage name")
        if stored.test_strategy_id:
            if journey_id in origins:
                ineligible_origins.add(journey_id)
            continue
        if journey_id in origins:
            seen_origins.add(journey_id)
            if stored.member_ref != origins[journey_id]:
                ineligible_origins.add(journey_id)

        for event in stored.events:
            if event.event not in {"outcome_reported", "contact_linked"}:
                continue
            occurred = datetime.fromisoformat(event.created_at.replace("Z", "+00:00"))
            if occurred.tzinfo is None:
                raise ValueError("closed feedback event lacks timezone")
            if not (since <= occurred <= now):
                continue
            if event.event == "outcome_reported":
                identity = (journey_id, event.outcome_response_id)
                source = eligible.get(identity)
                if source and source[0] == stored.member_ref and occurred >= source[1] and event.outcome_choice == "no":
                    negative.add(identity)
            else:
                # A contact event lives on the *later* journey, and names the
                # earlier journey explicitly. Matching only the response ID can
                # misattribute two different journeys' opaque response IDs.
                identity = (event.related_journey_id, event.related_response_id)
                source = eligible.get(identity)
                if source and source[0] == stored.member_ref and occurred >= source[1]:
                    followups.add(identity)
                elif source:
                    ambiguous += 1
    invalid_origins = (set(origins) - seen_origins) | ineligible_origins
    # A later journey cannot validate an absent or test-origin journey. Suppress
    # its counters even if the linked identity/member otherwise appears to fit.
    negative.difference_update({identity for identity in negative if identity[0] in invalid_origins})
    followups.difference_update({identity for identity in followups if identity[0] in invalid_origins})
    missing = len(invalid_origins)
    return {
        "status": "synthetic_gg_dev_observational",
        "eligible_response_references": len(eligible),
        "journeys_scanned": scanned,
        "reported_no": len(negative),
        "linked_followups_observed": len(followups),
        "attribution_complete": missing == 0,
        "unverified_origin_journeys": missing,
        "ambiguous_link_events": ambiguous,
    }

def suggest(proposal, drafter, label: str, corpus, vocabulary, catalogue):
    """Attach a grounded suggestion, or return the proposal unchanged. Nothing from the agent is trusted before checks."""
    request = gap_review.drafting_request(proposal, corpus, vocabulary, catalogue)
    if request is None:
        return proposal, False
    accept = gap_review.accept_style_suggestion if proposal.kind == "strategy_gap" else gap_review.accept_suggestion
    try:
        suggestion = accept(drafter(gap_review.for_model(request)), request, label)
    except Exception as error:
        log.warning("gap_review_suggestion_dropped", extra={"proposal_id": proposal.proposal_id,
                                                           "error_type": type(error).__name__})
        return proposal, False
    return gap_review.sealed({**asdict(proposal), "draft": {**proposal.draft, "suggested": suggestion}}), True


def run(*, records, journeys, proposals, approvals, catalogue, vocabulary, seed, questions=None, drafter=None,
        drafter_label: str = "", now: datetime | None = None, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> dict:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=lookback_days)
    refused = improvement_store.refused_names(approvals)
    approved = [approval.content for approval in improvement_store.read_approvals(approvals) if approval.name not in refused]
    corpus = gap_review.effective_corpus(seed, approved)
    observed = read_records(records, since)
    candidates, _ = gap_review.signals(observed, since=since.isoformat(timespec="seconds"))
    excluded = excluded_test_journeys(journeys, {signal.journey_id for signal in candidates})
    kept = read_questions(questions, {signal.response_id for signal in candidates
                                      if signal.journey_id not in excluded
                                      and (signal.decline_reason in CAPTURED_REASONS or signal.outcome == "review_flagged")}
                          ) if questions is not None else {}
    found, summary = gap_review.review(
        observed, catalogue=catalogue, corpus=corpus, vocabulary=vocabulary, excluded_journeys=excluded,
        since=since.isoformat(timespec="seconds"), now=now, questions=kept)
    written = drafted = 0
    current: dict[str, str] = {}
    for proposal in found:
        if improvement_store.exists(proposals, proposal.blob_name):
            current[proposal.proposal_id] = proposal.evidence_digest
            continue
        if drafter is not None:
            proposal, attached = suggest(proposal, drafter, drafter_label, corpus, vocabulary, catalogue)
            drafted += attached
        if proposal.kind == "strategy_gap" and "suggested" not in proposal.draft:
            continue  # a style idea is only offered with the AI's reasoning; the next batch asks again
        written += improvement_store.create_once(proposals, proposal.blob_name, proposal.encode())
        current[proposal.proposal_id] = proposal.evidence_digest
    redacted = improvement_store.redact_expired_questions(proposals, now)
    extra = (supplemental_feedback_counts(observed, journeys, since=since, now=now)
             if os.getenv("AZURE_ENV_NAME") == "gg-dev"
             and os.getenv("GOVERNEDGRAPH_GAP_REVIEW_SYNTHETIC_FEEDBACK") == "true"
             else {"status": "disabled", "eligible_response_references": 0,
                   "journeys_scanned": 0, "reported_no": 0, "linked_followups_observed": 0,
                   "attribution_complete": False, "unverified_origin_journeys": 0,
                   "ambiguous_link_events": 0})
    summary = {**summary, "proposals": len(found), "questions_read": len(kept), "suggestions_drafted": drafted,
               "supplemental_signals": extra, "current": current}
    if not improvement_store.create_once(proposals, f"batches/{summary['batch_id']}.json",
                                         json.dumps(summary, sort_keys=True).encode()):
        raise FileExistsError("this review batch is already recorded")
    log.info("gap_review_completed", extra={"batch_id": summary["batch_id"], "proposals": len(found),
                                           "new_revisions": written, "questions_redacted": redacted})
    return {**summary, "new_revisions": written}


def main() -> None:
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient

    from governedgraph.agents.gap_drafter import connect_gap_drafter
    from governedgraph.common import domain
    from governedgraph.common.operational_logging import configure_operational_logging
    from governedgraph.strategy_service.client import StrategyAdminClient

    configure_operational_logging()
    if not settings.explainability_account_url or not settings.journey_storage_url:
        raise ValueError("response-record and journey-state storage endpoints are required")
    lookback = int(os.getenv("GOVERNEDGRAPH_GAP_REVIEW_LOOKBACK_DAYS", str(DEFAULT_LOOKBACK_DAYS)))
    if not 1 <= lookback <= 90:
        raise ValueError("lookback must be 1 to 90 days")
    options = {"connection_timeout": 10, "read_timeout": 30, "retry_total": 2}
    with DefaultAzureCredential() as credential:
        records_account = BlobServiceClient(settings.explainability_account_url, credential=credential, **options)
        journey_account = BlobServiceClient(settings.journey_storage_url, credential=credential, **options)
        drafter = connect_gap_drafter(credential)
        try:
            result = run(
            records=records_account.get_container_client(settings.explainability_container),
            journeys=journey_account.get_container_client("journey-state"),
            proposals=records_account.get_container_client(settings.improvement_container),
            approvals=records_account.get_container_client(settings.approvals_container),
            questions=records_account.get_container_client(settings.declined_questions_container),
            drafter=drafter, drafter_label=drafter.label if drafter else "",
            catalogue=StrategyAdminClient(protective_only=True, credential=credential).catalogue,
            vocabulary=domain.vocabulary(),
            seed=load_corpus((DATA_DIR / "content" / "approved_content.jsonl").read_text()),
            lookback_days=lookback)
        except Exception as error:
            # Operational telemetry must not include member text or Blob contents.
            log.error("gap_review_failed", extra={"error_type": type(error).__name__})
            raise
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
