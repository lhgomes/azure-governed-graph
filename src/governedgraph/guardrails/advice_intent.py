"""Advice-intent detection (guidance, not advice).

Any option-level or personal-advice question triggers a warm handover to a
licensed human advisor. Zero option-recommendation incidents is a hard gate.

The generic English patterns below are the framework floor; a domain pack adds its own
language- and domain-specific patterns (catalogue/vocabulary_v1.json: advice_patterns).

LocalAdviceClassifier is retained only as an offline evaluation baseline.
Runtime interpretation belongs to the pinned contextual intent agent.
The independent output steering gate remains mandatory.
"""
from __future__ import annotations

import re

from governedgraph.common import domain

_PATTERNS = [
    r"\bshould i\b", r"\bwhat would you do\b", r"\bin my (?:position|shoes)\b", r"\bwhich (?:option|choice) is (?:better|best)\b",
    r"\brecommend\b", r"\badvise me\b", r"\bis it (?:better|wise|smart) to\b",
    r"\bbest for me\b", r"\bwhat do you suggest\b",
    *domain.vocabulary()["advice_patterns"],
]
_RX = re.compile("|".join(_PATTERNS), re.IGNORECASE)

# Phrases an ANSWER must never contain (output-side check, GNA).
_STEERING = re.compile(
    r"\byou should (?:choose|pick|object|opt|go for)\b|\bi (?:would )?recommend (?:that you )?(?:choose|option|object)\b|\bthe better option (?:for you )?is\b|\bi advise you to\b",
    re.IGNORECASE,
)


class LocalAdviceClassifier:
    def is_advice_seeking(self, utterance: str) -> bool:
        return bool(_RX.search(utterance or ""))


def response_steers_choice(response: str) -> bool:
    return bool(_STEERING.search(response or ""))
