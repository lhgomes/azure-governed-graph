"""Publish P3-approved immutable versions and retire their predecessors in one run.

ILLUSTRATIVE - synthetic data. The JSON input (one document or a list) must carry each human approval_ref.
    python -m governedgraph.search.reindex_on_approval /secure/p3-approved-document.json
"""
from __future__ import annotations

import argparse
import json
import os

from governedgraph.common.config import DATA_DIR
from governedgraph.search.corpus import ContentDocument, load_corpus
from governedgraph.search.ingest import AzureContentPublisher


def parse_candidates(payload) -> list[ContentDocument]:
    return [ContentDocument.from_dict(item) for item in (payload if isinstance(payload, list) else [payload])]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", nargs="?")
    parser.add_argument("--from-env", action="store_true", help="private P3 job execution input")
    args = parser.parse_args()
    if bool(args.document) == args.from_env:
        parser.error("provide a document path or --from-env, not both")
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
