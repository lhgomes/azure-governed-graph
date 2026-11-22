"""Publish P3-approved immutable versions and retire their predecessors in one run.

ILLUSTRATIVE - synthetic data. The JSON input (one document or a list) must carry each human approval_ref.
    python -m governedgraph.search.reindex_on_approval /secure/p3-approved-document.json

``--from-approvals`` publishes the passages an owner approved in the administration console and not yet published.
"""
from __future__ import annotations

import argparse
import json
import os

from governedgraph.common.config import DATA_DIR
from governedgraph.learning import improvement_store
from governedgraph.learning.gap_review import version_key
from governedgraph.search.corpus import ContentDocument, load_corpus
from governedgraph.search.ingest import AzureContentPublisher


def parse_candidates(payload) -> list[ContentDocument]:
    return [ContentDocument.from_dict(item) for item in (payload if isinstance(payload, list) else [payload])]


def pending_approvals(container) -> list[improvement_store.ContentApproval]:
    """Owner-approved passages without a publication marker, in the order their versions must be applied."""
    published = improvement_store.published_names(container)
    pending = [item for item in improvement_store.read_approvals(container) if item.name not in published]
    return sorted(pending, key=lambda item: (item.content.content_id, version_key(item.content.version), item.content.language))


def publish_approvals(container, seed, publisher, execution: str) -> int:
    pending = pending_approvals(container)
    if not pending:
        return 0
    publisher.publish(seed, [item.content for item in pending])
    for item in pending:
        improvement_store.mark_published(container, item, execution)
    return len(pending)


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

        if not settings.explainability_account_url:
            parser.error("EXPLAINABILITY_STORAGE_URL is required")
        with DefaultAzureCredential() as credential:
            container = BlobServiceClient(settings.explainability_account_url, credential=credential).get_container_client(
                settings.improvement_container)
            seed = load_corpus((DATA_DIR / "content" / "approved_content.jsonl").read_text())
            count = publish_approvals(container, seed, AzureContentPublisher(), os.getenv("CONTAINER_APP_JOB_EXECUTION_NAME", ""))
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
