"""Publish a P3-approved immutable version and retire its predecessor in one run.

ILLUSTRATIVE - synthetic data. The JSON input must carry its human approval_ref.
    python -m governedgraph.search.reindex_on_approval /secure/p3-approved-document.json
"""
from __future__ import annotations

import argparse
import json
import os

from governedgraph.common.config import DATA_DIR
from governedgraph.search.corpus import ContentDocument, load_corpus
from governedgraph.search.ingest import AzureContentPublisher


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", nargs="?")
    parser.add_argument("--from-env", action="store_true", help="private P3 job execution input")
    args = parser.parse_args()
    if bool(args.document) == args.from_env:
        parser.error("provide a document path or --from-env, not both")
    if args.from_env:
        candidate = ContentDocument.from_dict(json.loads(os.environ["CONTENT_APPROVAL_DOCUMENT"]))
    else:
        with open(args.document) as source:
            candidate = ContentDocument.from_dict(json.load(source))
    if candidate.status != "approved":
        parser.error("document must already have P3 approval")
    seed = load_corpus((DATA_DIR / "content" / "approved_content.jsonl").read_text())
    AzureContentPublisher().publish(seed, candidate)


if __name__ == "__main__":
    main()
