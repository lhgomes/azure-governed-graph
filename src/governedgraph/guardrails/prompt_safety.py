"""Input screening. FAIL CLOSED: if screening is
unavailable, generative responses stop and the journey escalates.

Azure: Azure AI Content Safety Prompt Shields (user prompt + documents).
Local: deterministic heuristics for the adversarial persona harness.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_INJECTION = re.compile(
    r"ignore (?:all |your )?(?:previous|prior) instructions|system prompt|you are now|developer mode|disregard (?:the )?rules",
    re.IGNORECASE,
)
_OTHER_MEMBER = re.compile(r"\bMBR-[A-Z0-9]{4}\b")


@dataclass(frozen=True)
class ScreenResult:
    allowed: bool
    reason: str = ""


class ContentSafetyError(RuntimeError):
    pass


class LocalPromptScreen:
    def screen(self, utterance: str, authenticated_member_ref: str) -> ScreenResult:
        if _INJECTION.search(utterance):
            return ScreenResult(False, "prompt_injection")
        refs = set(_OTHER_MEMBER.findall(utterance)) - {authenticated_member_ref}
        if refs:
            return ScreenResult(False, "cross_member_request")
        return ScreenResult(True)

