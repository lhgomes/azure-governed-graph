"""Publish P3-approved immutable versions and retire their predecessors in one run.

The JSON input (one document or a list) must carry each human approval_ref.
    python -m governedgraph.search.reindex_on_approval /secure/p3-approved-document.json

``--from-approvals`` publishes the passages an owner approved in the administration console and not yet published.
"""
from __future__ import annotations

import argparse
import json
import logging
import os

from governedgraph.common.config import DATA_DIR
from governedgraph.learning import improvement_store
from governedgraph.learning.gap_review import version_key
from governedgraph.search.corpus import ContentDocument, load_corpus
from governedgraph.search.ingest import AzureContentPublisher

log = logging.getLogger("governedgraph.search.publication")


def parse_candidates(payload) -> list[ContentDocument]:
    return [ContentDocument.from_dict(item) for item in (payload if isinstance(payload, list) else [payload])]


def pending_approvals(container) -> list[improvement_store.ContentApproval]:
    """Owner-approved passages with no publication or refusal marker, in the order their versions must be applied."""
    done = improvement_store.published_names(container) | improvement_store.refused_names(container)
    pending = [item for item in improvement_store.read_approvals(container) if item.name not in done]
    return sorted(pending, key=lambda item: (item.content.content_id, version_key(item.content.version), item.content.language))


def publish_approvals(container, journal, seed, publisher, execution: str) -> int:
    """Publish owner-approved passages. Every approval must match a journalled owner decision, or nothing is published.

    The publisher checks each approval against the live feed under its lease: an approval whose base passage is no
    longer live is refused and marked, never applied over newer content, and it does not block the others."""
    pending = pending_approvals(container)
    if not pending:
        return 0
    for item in pending:
        improvement_store.verify_decision(journal, item)
    applied, refused = publisher.publish_owner_approved(seed, [(item.content, item.base_id) for item in pending])
    if refused:
        # A refusal is an owner action item, not a successful publication;
        # only report the count, never content or journal references.
        log.warning("publication_content_refused", extra={"refused_count": len(refused)})
    for item in pending:
        if item.content.id in applied:
            improvement_store.mark_published(container, item, execution)
        elif item.content.id in refused:
            improvement_store.mark_refused(container, item, refused[item.content.id], execution)
    return len(applied)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", nargs="?")
    parser.add_argument("--from-env", action="store_true", help="private P3 job execution input")
    parser.add_argument("--from-approvals", action="store_true", help="passages approved in the administration console")
    args = parser.parse_args()
    if sum((bool(args.document), args.from_env, args.from_approvals)) != 1:
        parser.error("provide exactly one of a document path, --from-env or --from-approvals")
    if args.from_approvals:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        from governedgraph.common.config import settings
        from governedgraph.common.operational_logging import configure_operational_logging
        from governedgraph.search.publication_monitor import backlog

        scheduled = os.getenv("GOVERNEDGRAPH_CONTENT_RECONCILER") == "true"
        if scheduled:
            configure_operational_logging()
        try:
            if not settings.explainability_account_url:
                raise ValueError("protected owner approval storage is required")
            with DefaultAzureCredential() as credential:
                account = BlobServiceClient(settings.explainability_account_url, credential=credential)
                approvals = account.get_container_client(settings.approvals_container)
                journal = account.get_container_client(settings.admin_actions_container)
                seed = load_corpus((DATA_DIR / "content" / "approved_content.jsonl").read_text())
                count = publish_approvals(
                    approvals, journal, seed, AzureContentPublisher(),
                    os.getenv("CONTAINER_APP_JOB_EXECUTION_NAME", ""))
                if scheduled:
                    status = backlog(approvals)
                    log.info("publication_reconciliation_completed", extra={
                        "published_count": count, "pending_records": status["pending_records"],
                        "age_seconds": status["age_seconds"]})
                    if status["stale"]:
                        log.warning("publication_backlog_stale", extra={
                            "pending_records": status["pending_records"], "age_seconds": status["age_seconds"]})
        except Exception as error:
            if scheduled:
                # Failures must be visible without logging source, proposal or member content.
                log.error("publication_reconciliation_failed", extra={"error_type": type(error).__name__})
            raise
        print(f"published {count} owner-approved passages", flush=True)
        return
    if args.from_env:
        candidates = parse_candidates(json.loads(os.environ["CONTENT_APPROVAL_DOCUMENT"]))
    else:
        with open(args.document) as source:
            candidates = parse_candidates(json.load(source))
    if not candidates or any(candidate.status != "approved" for candidate in candidates):
        parser.error("documents must already have P3 approval")
    seed = load_corpus((DATA_DIR / "content" / "approved_content.jsonl").read_text())
    AzureContentPublisher().publish(seed, candidates)


if __name__ == "__main__":
    main()
