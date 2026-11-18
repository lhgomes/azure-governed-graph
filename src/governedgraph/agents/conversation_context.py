"""Screened dialogue is reference context, never authority or a learning input."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from governedgraph.common.contracts import ContractError, _strict

MAX_CONVERSATION_TURNS = 20


@dataclass(frozen=True)
class ConversationMessage:
    role: Literal["user", "assistant"]
    text: str

    def __post_init__(self):
        if self.role not in {"user", "assistant"} or not isinstance(self.text, str) or not self.text.strip():
            raise ContractError("invalid closed conversation message")

    @classmethod
    def from_dict(cls, payload: dict) -> "ConversationMessage":
        return _strict(cls, payload)


def reference_messages(messages: tuple[ConversationMessage, ...]) -> list[dict[str, str]]:
    # Even a previously checked figure must be read afresh, not replayed as authority.
    return [{"role": item.role,
             "text": re.sub(r"\d+(?:[.,:/% -]\d+)*", "[historical number; obtain current official reference]", item.text)}
            for item in messages]


def screening_batches(messages: tuple[ConversationMessage, ...]):
    from governedgraph.guardrails.prompt_safety import ContentSafetyError

    batch: list[str] = []
    length = 0
    for item in messages:
        if len(item.text) > 10_000:
            raise ContentSafetyError("historical message exceeds Prompt Shields limits")
        if batch and (len(batch) == 5 or length + len(item.text) > 10_000):
            yield batch
            batch, length = [], 0
        batch.append(item.text)
        length += len(item.text)
    if batch:
        yield batch
