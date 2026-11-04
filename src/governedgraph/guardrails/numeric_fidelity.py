"""Numeric-fidelity evaluator (B2) - HARD GATE at 100%.

Every figure in a member-facing response must be traceable to a
get_member_values result (display string or as-of date) or appear verbatim in
retrieved APPROVED content. Anything else blocks the response.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from governedgraph.common.contracts import MemberValues

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
FIGURE = re.compile(
    rf"(?:EUR|€)\s?\d(?:[\d.,]*\d)?(?:\s(?:a|per)\s(?:year|month))?"   # currency
    rf"|\d{{1,2}}\s(?:{MONTHS})\s\d{{4}}"                          # long dates
    rf"|\d{{4}}-\d{{2}}-\d{{2}}"                                    # ISO dates
    rf"|\d(?:[\d.,]*\d)?\s?%"                                              # percentages
    rf"|\d{{1,3}}(?:[.,]\d{{3}})+(?:[.,]\d+)?"                      # grouped numbers
    rf"|\d+[.,]\d+"                                                 # decimals
    rf"|\b\d{{3,}}\b"                                               # bare numbers >= 100
)
ORDINAL = re.compile(r"\b(?:Step|step|Stap|stap)\s\d\b")


@dataclass(frozen=True)
class FidelityResult:
    passed: bool
    figures: list[str]
    untraceable: list[str]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("€", "EUR ")).strip()


def check(response: str, values: MemberValues | None, approved_texts: list[str]) -> FidelityResult:
    text = ORDINAL.sub(" ", response)
    allowed: list[str] = []
    if values:
        for v in values.values:
            allowed += [_norm(v.display), v.as_of]
    corpus = _norm(" ".join(approved_texts))
    figures = [m.group(0).strip() for m in FIGURE.finditer(text)]
    bad = []
    for fig in figures:
        nf = _norm(fig)
        if any(nf == a or nf.startswith(a + " ") for a in allowed):
            continue
        if nf in corpus:
            continue
        bad.append(fig)
    return FidelityResult(passed=not bad, figures=figures, untraceable=bad)
