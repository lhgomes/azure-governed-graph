"""Mock deterministic system of record (stands in for the administrator's actuarial engine).

Read-only. Returns official values with source, calculation run ID and as-of
date. Exposes NO demographic attributes. Agents never compute values (B1).
"""
from __future__ import annotations

from governedgraph.common.config import DATA_DIR
from governedgraph.common.contracts import MemberValue, MemberValues
from governedgraph.common.io import load_json

_DATA = load_json(DATA_DIR / "synthetic" / "members.json")
SOURCE_SYSTEM = "mock-administration-system"


class MemberScopeError(PermissionError):
    pass


def get_member_values(member_ref: str, authenticated_member_ref: str) -> MemberValues:
    """Member scoping on EVERY call: only the authenticated member's values."""
    if member_ref != authenticated_member_ref:
        raise MemberScopeError("member scoping violation: request rejected and incident raised")
    m = _DATA["members"].get(member_ref)
    if m is None:
        raise KeyError(member_ref)
    return MemberValues(
        member_ref=member_ref,
        values=[MemberValue(source_system=SOURCE_SYSTEM, **v) for v in m["values"]],
    )
