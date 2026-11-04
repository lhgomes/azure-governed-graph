"""Closed, versioned P3 content contracts. ILLUSTRATIVE - synthetic data."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, fields, replace

from governedgraph.common.contracts import RESPONSE_LANGUAGES, ContractError, _strict


@dataclass(frozen=True)
class ContentDocument:
    id: str
    content_id: str
    version: str
    status: str
    title: str
    language: str
    journey_states: list[str]
    approval_ref: str
    body: str
    watermark: str

    def __post_init__(self):
        for name, value in asdict(self).items():
            if name != "journey_states" and (not isinstance(value, str) or not value.strip()):
                raise ContractError(f"content requires nonempty {name}")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", self.id):
            raise ContractError("invalid content version id")
        if self.status not in {"approved", "retired"} or self.language not in RESPONSE_LANGUAGES:
            raise ContractError("content requires approved/retired status and a supported language")
        if (not isinstance(self.journey_states, list) or not self.journey_states
                or any(not isinstance(s, str) or not s.strip() for s in self.journey_states)):
            raise ContractError("content requires journey states")
        if not self.watermark.startswith("ILLUSTRATIVE - synthetic"):
            raise ContractError("content requires synthetic watermark")
        if len(self.body.encode("utf-8")) > 8000:
            raise ContractError("passage exceeds conservative embedding input limit; split and approve it upstream")

    @classmethod
    def from_dict(cls, payload: dict) -> ContentDocument:
        if isinstance(payload, dict) and {field.name for field in fields(cls)} - payload.keys():
            raise ContractError("content requires every version and approval field")
        return _strict(cls, payload)

    @property
    def search_id(self) -> str:
        return self.id.replace(".", "_")

    def index_document(self) -> dict:
        return {**asdict(self), "id": self.search_id, "source_id": self.id}


def load_corpus(text: str, *, indexed: bool = False) -> list[ContentDocument]:
    docs = []
    for line in text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if indexed:
            source_id = row.pop("source_id")
            if row["id"] != source_id.replace(".", "_"):
                raise ContractError("inconsistent indexed content identity")
            row["id"] = source_id
        docs.append(ContentDocument.from_dict(row))
    ids: set[str] = set()
    versions: set[tuple[str, str, str]] = set()
    active: set[tuple[str, str]] = set()
    for doc in docs:
        if doc.search_id in ids:
            raise ContractError("duplicate or colliding content version id")
        ids.add(doc.search_id)
        version = (doc.content_id, doc.version, doc.language)
        if version in versions:
            raise ContractError("duplicate immutable content version")
        versions.add(version)
        key = (doc.content_id, doc.language)
        if doc.status == "approved":
            if key in active:
                raise ContractError("multiple approved versions for content and language")
            active.add(key)
    if not docs:
        raise ContractError("empty content corpus")
    return docs


def approve_version(docs: list[ContentDocument], candidate: ContentDocument) -> list[ContentDocument]:
    if candidate.status != "approved":
        raise ContractError("replacement requires P3 approval")
    for doc in docs:
        if doc.id == candidate.id:
            if doc != candidate:
                raise ContractError("immutable version id cannot be modified or reinstated; approve a new version")
            return docs
        if (doc.content_id, doc.version, doc.language) == (candidate.content_id, candidate.version, candidate.language):
            raise ContractError("immutable content version cannot be republished under another id")
    updated = [
        replace(doc, status="retired")
        if (doc.content_id, doc.language) == (candidate.content_id, candidate.language) else doc
        for doc in docs
    ]
    updated.append(candidate)
    return load_corpus("\n".join(json.dumps(asdict(d)) for d in updated))
