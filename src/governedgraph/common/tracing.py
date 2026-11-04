"""W3C correlation without member attributes or conversation content."""
from __future__ import annotations

import re
import uuid

TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-(0[01])$")


def traceparent(value: str = "") -> str:
    match = TRACEPARENT.fullmatch(value)
    if match and int(match[1], 16) and int(match[2], 16):
        return value
    return f"00-{uuid.uuid4().hex}-{uuid.uuid4().hex[:16]}-01"
