"""Governed retrieval over APPROVED, versioned content only (B3 step 5).

Local: keyword scoring over data/content/approved_content.jsonl.
Azure: Azure AI Search with filter status eq 'approved' (see search/ingest.py).
Retired versions are never returned in either mode.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from dataclasses import asdict, dataclass

from governedgraph.common import domain
from governedgraph.common.config import DATA_DIR, settings
from governedgraph.common.contracts import ACCESSIBILITY_MODES, RESPONSE_LANGUAGES
from governedgraph.search.corpus import load_corpus

log = logging.getLogger("governedgraph.search")


def content_language(accessibility_mode: str) -> str:
    if accessibility_mode not in ACCESSIBILITY_MODES:
        raise ValueError("unknown accessibility mode")
    return "en" if accessibility_mode == "english" else domain.vocabulary()["default_language"]


@dataclass(frozen=True)
class ContentHit:
    id: str
    content_id: str
    version: str
    title: str
    body: str
    language: str


def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-zA-Z]{3,}", s.lower())}


class LocalContentSearch:
    def __init__(self):
        rows = [asdict(d) for d in load_corpus((DATA_DIR / "content" / "approved_content.jsonl").read_text())]
        self.docs = [r for r in rows if r["status"] == "approved"]

    def search(self, query: str, journey_state: str, language: str = "en", top: int = 3) -> list[ContentHit]:
        q = _tokens(query)
        scored = []
        for d in self.docs:
            if d["language"] != language:
                continue
            if "*" not in d["journey_states"] and journey_state not in d["journey_states"]:
                continue
            score = len(q & _tokens(d["title"] + " " + d["body"]))
            scored.append((score, d))
        scored.sort(key=lambda x: (-x[0], x[1]["id"]))
        return [ContentHit(d["id"], d["content_id"], d["version"], d["title"], d["body"], d["language"]) for s, d in scored[:top] if s > 0]


class AzureContentSearch:
    def __init__(self):
        from governedgraph.agents.connections import isolated, scoped

        self.client = (scoped("search", lambda: _new_search_client(settings.search_endpoint, settings.search_index))
                       if isolated() else _search_client(settings.search_endpoint, settings.search_index))

    def search(self, query: str, journey_state: str, language: str = "en", top: int = 3) -> list[ContentHit]:
        from azure.search.documents.models import VectorizableTextQuery

        if language not in RESPONSE_LANGUAGES or top < 1:
            raise ValueError("supported language and positive result count required")
        state = journey_state.replace("'", "''")
        flt = (
            f"status eq 'approved' and language eq '{language}' and "
            f"(journey_states/any(s: s eq '{state}') or journey_states/any(s: s eq '*'))"
        )
        res = self.client.search(search_text=query, filter=flt, top=top, query_type="semantic",
                                 semantic_configuration_name="default",
                                 vector_queries=[VectorizableTextQuery(text=query, fields="body_vector",
                                                                     k_nearest_neighbors=50)],
                                 vector_filter_mode="preFilter",
                                 select=["id", "source_id", "content_id", "version", "status",
                                         "approval_ref", "title", "body", "language"])
        hits = []
        for row in res:
            if (row["status"] != "approved" or not row.get("approval_ref") or not row.get("source_id")
                    or row["language"] != language):
                log.error("unapproved_content_returned")
                raise ValueError("search returned content outside the approved language filter")
            hits.append(ContentHit(row["source_id"], row["content_id"], row["version"],
                                   row["title"], row["body"], row["language"]))
        return hits


@lru_cache(maxsize=4)
def _search_client(endpoint: str, index: str):
    return _new_search_client(endpoint, index)


def _new_search_client(endpoint: str, index: str):
    from azure.search.documents import SearchClient
    from governedgraph.agents.connections import credential

    return SearchClient(endpoint, index, credential())


def default_search():
    return AzureContentSearch() if settings.is_azure and settings.search_endpoint else LocalContentSearch()
