"""Member questions that ended without an explanation, kept briefly for the gap review.

This is the one place member words are stored for analysis. The store is a quarantined container written by the runtime
and read only by the gap-review job, with a short lifecycle expiry. The text is never logged and never enters a prompt,
template, model or catalogue automatically: it reaches an owner as an example inside a proposal, and an AI drafting step
that the owner reviews. Each blob links to its response record by response_id (B4, by explicit owner decision).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass

from governedgraph.common.config import settings
from governedgraph.common.contracts import ContractError, _strict

log = logging.getLogger("governedgraph.explainability")
MAX_CHARS = 1000
# Declines where the member's wording can show a missing passage or style; protective and member-requested exits are excluded.
CAPTURED_REASONS = frozenset({"output_gate", "answer_review", "no_safe_answer", "no_eligible_style"})
WATERMARK = "ILLUSTRATIVE - synthetic data"


@dataclass(frozen=True)
class DeclinedQuestion:
    journey_id: str
    response_id: str
    created_at: str
    text: str
    watermark: str = WATERMARK

    def __post_init__(self):
        if (not isinstance(self.journey_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", self.journey_id)
                or not isinstance(self.response_id, str) or not re.fullmatch(r"(?:[0-9a-f]{32}|[0-9a-f]{64})", self.response_id)
                or not isinstance(self.created_at, str) or len(self.created_at) > 40
                or not isinstance(self.text, str) or not self.text.strip() or len(self.text) > MAX_CHARS
                or self.watermark != WATERMARK):
            raise ContractError("invalid declined question")

    @classmethod
    def from_dict(cls, payload: dict) -> DeclinedQuestion:
        return _strict(cls, payload)

    @property
    def blob_name(self) -> str:
        return f"questions/{self.response_id}.json"

    def encode(self) -> bytes:
        return json.dumps(asdict(self), sort_keys=True).encode()


class BlobDeclinedQuestionStore:
    """One create-only blob per declined response, under the runtime's own container-scoped role."""

    def __init__(self, account_url: str, container: str, *, client=None):
        if client is None:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient

            client = BlobServiceClient(account_url, credential=DefaultAzureCredential(),
                                       connection_timeout=3, read_timeout=5, retry_total=0)
        self._container = client.get_container_client(container)

    def append(self, question: DeclinedQuestion) -> None:
        from azure.core.exceptions import ResourceExistsError

        try:
            self._container.get_blob_client(question.blob_name).upload_blob(question.encode(), overwrite=False)
        except ResourceExistsError:
            return


def default_question_store() -> BlobDeclinedQuestionStore | None:
    """Enabled only by explicit deployment configuration; capture is an opt-in side channel, never a turn dependency."""
    import os

    if os.getenv("GOVERNEDGRAPH_CAPTURE_DECLINED_QUESTIONS", "").lower() != "true":
        return None
    if not settings.explainability_account_url:
        # A side channel must never stop a member turn, so a missing endpoint disables capture loudly instead.
        log.error("declined_question_capture_disabled", extra={"reason": "explainability storage endpoint missing"})
        return None
    return BlobDeclinedQuestionStore(settings.explainability_account_url, settings.declined_questions_container)
