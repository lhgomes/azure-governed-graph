"""Bounded wait-and-retry for a model request refused with HTTP 429.

A 429 from a gateway or deployment rate limit is a pacing signal, not an accepted model result.
Quota exhaustion (HTTP 403) and every other failure are never retried here.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

log = logging.getLogger("governedgraph.agents")

MAX_WAIT_SECONDS = 20.0
TURN_WAIT_BUDGET_SECONDS = 30.0
DEFAULT_WAIT_SECONDS = 5.0


def retry_after_seconds(error: BaseException) -> float | None:
    """The recommended wait for a rate-limited request, or None when it must not be retried."""
    response = getattr(error, "response", None)
    if getattr(error, "status_code", getattr(response, "status_code", None)) != 429:
        return None
    headers = getattr(response, "headers", None) or {}
    try:
        if headers.get("retry-after-ms") is not None:
            seconds = float(headers["retry-after-ms"]) / 1000
        elif headers.get("retry-after") is not None:
            seconds = float(headers["retry-after"])
        else:
            seconds = DEFAULT_WAIT_SECONDS
    except (TypeError, ValueError):
        seconds = DEFAULT_WAIT_SECONDS
    seconds = max(1.0, seconds) + 0.5
    return seconds if seconds <= MAX_WAIT_SECONDS else None


def create_with_retry(create: Callable[..., Any], tools, *, sleep: Callable[[float], None] = time.sleep, **kwargs):
    """Call a model endpoint; on a pacing 429 wait the recommended time once, within a per-turn waiting budget."""
    try:
        return create(**kwargs)
    except Exception as error:
        wait = retry_after_seconds(error)
        spent = getattr(tools.trace, "rate_limit_wait", 0.0)
        if wait is None or spent + wait > TURN_WAIT_BUDGET_SECONDS:
            raise
        tools.trace.rate_limit_wait = spent + wait
        tools.trace.note("rate_limit", "retried")
        log.warning("model_rate_limited_retry", extra={"journey_id": tools.journey.journey_id,
                                                      "wait_seconds": round(wait, 1)})
        sleep(wait)
        return create(**kwargs)
