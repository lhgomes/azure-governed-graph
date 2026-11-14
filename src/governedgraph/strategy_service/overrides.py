"""Closed human-decision contracts; no member attributes or conversation inputs."""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field

from governedgraph.common.contracts import ContractError, _strict, utcnow
from governedgraph.common.io import canonical_json


def state_revision(state: dict) -> str:
    return hashlib.sha256(canonical_json(state)).hexdigest()


def suspension_digest(event: dict) -> str:
    return hashlib.sha256(canonical_json(event)).hexdigest()


def _validate_scope(action: str, strategy_id: str, journey_state: str, request_key: str,
                    order: list[str], override_id: str = "") -> None:
    for value in (strategy_id, journey_state, request_key, override_id):
        if not isinstance(value, str) or len(value) > 250 or value != value.strip():
            raise ContractError("invalid owner decision scope")
    if (not isinstance(order, list) or len(order) > 100
            or any(not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", sid) for sid in order)
            or len(set(order)) != len(order)):
        raise ContractError("the order must contain unique approved style IDs")
    if action == "order":
        valid = request_key and order and not any((strategy_id, journey_state, override_id))
    elif action in {"pause", "unpause"}:
        valid = strategy_id and journey_state and not any((request_key, order, override_id))
    elif action == "revoke":
        valid = re.fullmatch(r"GB-[a-f0-9]{32}", override_id) and not any((strategy_id, journey_state, request_key, order))
    else:
        valid = False
    if not valid:
        raise ContractError("the owner decision has an invalid or unrelated scope")


@dataclass(frozen=True)
class OwnerDecision:
    action: str
    expected_revision: str
    decision_ref: str
    reason: str
    confirmed: bool
    strategy_id: str = ""
    journey_state: str = ""
    request_key: str = ""
    order: list[str] = field(default_factory=list)
    override_id: str = ""

    @classmethod
    def from_dict(cls, payload: dict) -> OwnerDecision:
        result = _strict(cls, payload)
        if not isinstance(result.action, str) or result.action not in {"order", "pause", "unpause", "revoke"} or result.confirmed is not True:
            raise ContractError("confirm the human owner decision")
        if not isinstance(result.expected_revision, str) or not re.fullmatch(r"[a-f0-9]{64}", result.expected_revision):
            raise ContractError("a current state revision is required")
        if not isinstance(result.decision_ref, str) or not re.fullmatch(r"GB-[a-f0-9]{32}", result.decision_ref):
            raise ContractError("a recorded owner decision reference is required")
        if not isinstance(result.reason, str) or not 8 <= len(result.reason.strip()) <= 500:
            raise ContractError("provide a reason of 8 to 500 characters; no member details")
        _validate_scope(result.action, result.strategy_id, result.journey_state, result.request_key, result.order, result.override_id)
        return result


@dataclass(frozen=True)
class OwnerOverride:
    override_id: str
    kind: str
    strategy_id: str = ""
    journey_state: str = ""
    request_key: str = ""
    order: list[str] = field(default_factory=list)
    target_digests: list[str] = field(default_factory=list)
    snapshot: dict | None = None
    created_at: str = field(default_factory=utcnow)

    @classmethod
    def from_dict(cls, payload: dict) -> OwnerOverride:
        result = _strict(cls, payload)
        if (not isinstance(result.override_id, str) or not re.fullmatch(r"GB-[a-f0-9]{32}", result.override_id)
                or not isinstance(result.kind, str) or result.kind not in {"order", "pause", "unpause"}):
            raise ContractError("invalid recorded owner decision")
        _validate_scope(result.kind, result.strategy_id, result.journey_state, result.request_key, result.order)
        if (not isinstance(result.created_at, str) or not result.created_at or len(result.created_at) > 64
                or not isinstance(result.target_digests, list)
                or any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
                       for value in result.target_digests)
                or len(set(result.target_digests)) != len(result.target_digests)):
            raise ContractError("invalid owner override metadata")
        if (result.kind == "unpause") != bool(result.target_digests):
            raise ContractError("only an unpause decision targets existing suspension evidence")
        if result.snapshot is not None and (result.kind != "order" or not isinstance(result.snapshot, dict)):
            raise ContractError("only an order override accepts a signed snapshot")
        return result

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class OwnerOrderPublication:
    override_id: str
    snapshot: dict

    @classmethod
    def from_dict(cls, payload: dict) -> OwnerOrderPublication:
        result = _strict(cls, payload)
        if (not isinstance(result.override_id, str) or not re.fullmatch(r"GB-[a-f0-9]{32}", result.override_id)
                or not isinstance(result.snapshot, dict)):
            raise ContractError("invalid owner order publication")
        return result


def lifted_suspensions(state: dict) -> set[str]:
    if state["frozen"]:
        return set()
    overrides = [OwnerOverride.from_dict(item) for item in state.get("owner_overrides", [])]
    return {suspension_digest(event) for event in state["suspensions"]
            if any(item.kind == "unpause" and item.strategy_id == event["strategy_id"]
                   and item.journey_state in ("*", event["journey_state"])
                   and suspension_digest(event) in item.target_digests for item in overrides)}


def effective_suspensions(state: dict, journey_state: str) -> list[str]:
    overrides = [OwnerOverride.from_dict(item) for item in state.get("owner_overrides", [])]
    lifted = lifted_suspensions(state)
    paused = {item["strategy_id"] for item in state["suspensions"]
              if item["journey_state"] in (journey_state, "*") and suspension_digest(item) not in lifted}
    paused.update(item.strategy_id for item in overrides
                  if item.kind == "pause" and item.journey_state in (journey_state, "*"))
    return sorted(paused)
