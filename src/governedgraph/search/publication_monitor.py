"""Content-free, read-only operational evidence about owner-approved publication backlog."""
from __future__ import annotations

from datetime import datetime, timezone

from governedgraph.learning import improvement_store

BACKLOG_MAX_AGE_SECONDS = 26 * 3600


def backlog(container, *, now: datetime | None = None) -> dict:
    """Every unmatched owner approval is pending; never silently truncate the store.

    This reads the same protected approval, published and refused markers used by
    the real publisher. A pending approval is not evidence of live content.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("backlog observation time must be timezone-aware")
    terminal = improvement_store.published_names(container) | improvement_store.refused_names(container)
    pending = [approval for approval in improvement_store.read_approvals(container)
               if approval.name not in terminal]
    ages = []
    for approval in pending:
        properties = container.get_blob_client(f"approved/{approval.name}").get_blob_properties()
        modified = properties.last_modified
        if modified.tzinfo is None or modified > now:
            raise ValueError("approval timestamp is missing its timezone or is in the future")
        ages.append(int((now - modified).total_seconds()))
    oldest = max(ages, default=0)
    return {
        "pending_records": len(pending),
        "age_seconds": oldest,
        "stale": bool(pending and oldest >= BACKLOG_MAX_AGE_SECONDS),
    }
