"""Opt-in record of every model request and response, for diagnosing synthetic gg-dev conversations.

Unlike the rest of the operational log this is NOT content-free, so it is enabled only when the environment is
gg-dev AND GOVERNEDGRAPH_LOG_LLM_CALLS=true (infra sets that for gg-dev alone). Request headers are never part of
a record, text that looks like a credential is withheld, and the log is never an input to prompts, ranking or
learning (B4). Large payloads are split into numbered parts so console lines are not truncated."""
from __future__ import annotations

import json
import logging
import os
import re
from uuid import uuid4

from governedgraph.common.config import settings

log = logging.getLogger("governedgraph.llm_calls")
CHUNK_CHARS = 7000
_CREDENTIAL = re.compile(
    r"\bBearer\s+\S+|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
    r"|\b(?:authorization|password|accountkey|api[_-]?key|client[_-]?secret)\s*[:=]"
    r"|[?&](?:sig|token|key|code)=", re.IGNORECASE,
)


def enabled() -> bool:
    return settings.environment_name == "gg-dev" and os.getenv("GOVERNEDGRAPH_LOG_LLM_CALLS") == "true"


def _text(value) -> str:
    if isinstance(value, str):
        text = value
    else:
        dump = getattr(value, "model_dump", None)
        if callable(dump):
            try:
                value = dump(mode="json", exclude_none=True)
            except Exception:  # a response object that cannot be dumped is still recorded as text
                pass
        text = json.dumps(value, ensure_ascii=False, default=str)
    return "[withheld: possible credential]" if _CREDENTIAL.search(text) else text


def record(journey_id: str | None, kind: str, deployment: str, request, response, *, outcome: str = "ok") -> None:
    """Never raises: diagnostics must not change the outcome of a member turn."""
    if not enabled():
        return
    try:
        call_id = uuid4().hex
        for part, value in (("request", request), ("response", response)):
            text = _text(value)
            chunks = [text[start:start + CHUNK_CHARS] for start in range(0, len(text), CHUNK_CHARS)] or [""]
            for number, chunk in enumerate(chunks, 1):
                log.info("llm_call", extra={
                    "journey_id": journey_id or "none", "call_id": call_id, "kind": kind,
                    "model_deployment": deployment, "part": part, "chunk": number, "chunks": len(chunks),
                    "outcome": outcome, "text": chunk,
                })
    except Exception:
        return
