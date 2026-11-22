"""Scheduled gap review: read declined interactions, write inert improvement proposals for the administration console.

Runs under its own identity with read access to response records, journey state, kept declined questions and
owner-approved passages, and write access to the proposal container only. It cannot sign, publish, change a catalogue or
write an approval (B3, B4, B8). When a pinned drafting agent is configured, it also asks that agent for a passage
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


def read_records(container, since: datetime) -> list[ExplainabilityRecord]:
    """Every response record written since ``since``; response records are immutable, so last-modified is their age."""
    records = []
    for item in container.list_blobs(name_starts_with=""):
        if not item.name.endswith((".json", ".jsonl")) or item.last_modified < since:
            continue
        text = container.get_blob_client(item.name).download_blob().readall().decode("utf-8")
        records.extend(ExplainabilityRecord.from_dict(json.loads(line)) for line in text.splitlines() if line.strip())
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


def suggest(proposal, drafter, label: str, corpus, vocabulary):
    """Attach a grounded suggestion, or return the proposal unchanged. Nothing from the agent is trusted before checks."""
    request = gap_review.drafting_request(proposal, corpus, vocabulary)
    if request is None:
        return proposal, False
    accept = gap_review.accept_style_suggestion if proposal.kind == "strategy_gap" else gap_review.accept_suggestion
    try:
        suggestion = accept(drafter(request), request, label)
    except Exception as error:
        log.warning("gap_review_suggestion_dropped", extra={"proposal_id": proposal.proposal_id,
                                                           "error_type": type(error).__name__})
        return proposal, False
    return gap_review.sealed({**asdict(proposal), "draft": {**proposal.draft, "suggested": suggestion}}), True


def run(*, records, journeys, proposals, approvals, catalogue, vocabulary, seed, questions=None, drafter=None,
        drafter_label: str = "", now: datetime | None = None, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> dict:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=lookback_days)
    approved = [approval.content for approval in improvement_store.read_approvals(approvals)]
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
    for proposal in found:
        if improvement_store.exists(proposals, proposal.blob_name):
            continue
        if drafter is not None:
            proposal, attached = suggest(proposal, drafter, drafter_label, corpus, vocabulary)
            drafted += attached
        if proposal.kind == "strategy_gap" and "suggested" not in proposal.draft:
            continue  # a style idea is only offered with the AI's reasoning; the next batch asks again
        written += improvement_store.create_once(proposals, proposal.blob_name, proposal.encode())
    summary = {**summary, "proposals": len(found), "questions_read": len(kept), "suggestions_drafted": drafted}
    if not improvement_store.create_once(proposals, f"batches/{summary['batch_id']}.json",
                                         json.dumps(summary, sort_keys=True).encode()):
        raise FileExistsError("this review batch is already recorded")
    log.info("gap_review_completed", extra={"batch_id": summary["batch_id"], "proposals": len(found),
                                           "new_revisions": written})
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
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
