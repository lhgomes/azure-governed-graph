"""Deterministic, bounded exploration within an approved treatment ranking."""
from __future__ import annotations

import hashlib
import math


def explore(ranking: list[str], journey_id: str, request_key: str,
            rate: float, max_shift: int) -> tuple[list[str], bool]:
    if (isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate)
            or not 0 <= rate <= 1 or isinstance(max_shift, bool)
            or not isinstance(max_shift, int) or max_shift < 0):
        raise ValueError("invalid approved exploration bounds")
    alternatives = min(max_shift, len(ranking) - 1)
    if rate == 0 or alternatives <= 0:
        return list(ranking), False
    digest = hashlib.sha256(f"gg-exploration|{journey_id}|{request_key}".encode()).digest()
    if int.from_bytes(digest[:8]) / 2**64 >= rate:
        return list(ranking), False
    selected = 1 + int.from_bytes(digest[8:16]) % alternatives
    return [ranking[selected], *ranking[:selected], *ranking[selected + 1:]], True
