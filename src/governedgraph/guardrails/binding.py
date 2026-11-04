"""Template binding by reference (B2).

Approved templates and model drafts contain placeholders, never figures:
  {{value:<value_id>}}  -> the system-of-record display string
  {{asof:<value_id>}}   -> the as-of date of that value
Unknown placeholders fail closed.
"""
from __future__ import annotations

import re

from governedgraph.common.contracts import MemberValues

PLACEHOLDER = re.compile(r"\{\{(value|asof):([a-z0-9_]+)\}\}")


class BindingError(ValueError):
    pass


def referenced_value_ids(text: str) -> list[str]:
    return sorted({m.group(2) for m in PLACEHOLDER.finditer(text)})


def bind(text: str, values: MemberValues) -> str:
    by_id = values.by_id()

    def repl(m: re.Match) -> str:
        kind, vid = m.group(1), m.group(2)
        if vid not in by_id:
            raise BindingError(f"placeholder references unknown value_id {vid!r}")
        return by_id[vid].display if kind == "value" else by_id[vid].as_of

    return PLACEHOLDER.sub(repl, text)
