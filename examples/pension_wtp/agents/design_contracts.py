"""Closed offline contracts for the Transition Calculation, Event Processing and Adoption Guide design-depth agents."""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field, fields
from hashlib import sha256
from types import UnionType
from typing import Callable, ClassVar, Literal, TypeVar, cast, get_args, get_origin, get_type_hints

from governedgraph.common.contracts import ContractError, _strict

T = TypeVar("T")


def _valid_type(value: object, annotation: object) -> bool:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Literal:
        return any(type(value) is type(a) and value == a for a in args)
    if origin is UnionType:
        return any(_valid_type(value, a) for a in args)
    if origin is list:
        return isinstance(value, list) and all(_valid_type(v, args[0]) for v in value)
    if annotation is str:
        return isinstance(value, str) and bool(value.strip())
    return type(value) is annotation


@dataclass(frozen=True)
class DesignContract:
    contract_version: Literal["1.0"] = field(default="1.0", kw_only=True)
    __pydantic_config__: ClassVar[dict[str, str]] = {"extra": "forbid"}

    def __post_init__(self):
        hints = get_type_hints(type(self))
        for f in fields(self):
            if not _valid_type(getattr(self, f.name), hints[f.name]):
                raise ContractError(f"{type(self).__name__}: invalid {f.name}")

    @classmethod
    def from_dict(cls: type[T], payload: dict) -> T:
        try:
            return _strict(cls, payload)
        except TypeError as exc:
            raise ContractError(f"{cls.__name__}: missing or invalid fields") from exc


class IdempotentWrites:
    """In-memory consumer deduplication only; not a durable queue adapter."""

    def __init__(self):
        self._writes: dict[tuple[str, str], tuple[str, object]] = {}

    def apply(self, operation: str, key: str, payload: dict, action: Callable[[], T]) -> T:
        if not isinstance(key, str) or not key.strip() or len(key) > 128:
            raise ContractError("every write requires an idempotency key of at most 128 characters")
        fingerprint = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        slot = (operation, key)
        if slot in self._writes:
            previous, result = self._writes[slot]
            if previous != fingerprint:
                raise ContractError("idempotency key reused with a different payload")
            return cast(T, deepcopy(result))
        result = action()
        self._writes[slot] = (fingerprint, deepcopy(result))
        return deepcopy(result)


def domain_id(kind: str, payload: dict) -> str:
    digest = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return f"{kind}-{digest[:32]}"


@dataclass(frozen=True)
class ConversionBatch(DesignContract):
    batch_id: str
    run_plan_id: str
    rule_set_version: str
    member_refs: list[str]
    status: Literal["queued", "running", "awaiting_actuary", "exceptions", "released"] = "queued"
    engine_run_id: str | None = None
    exceptions: list[str] = field(default_factory=list)
    actuary_approval_ref: str | None = None

    def __post_init__(self):
        super().__post_init__()
        if not self.member_refs:
            raise ContractError("conversion batch must reference members")
        if self.status != "queued" and self.engine_run_id is None:
            raise ContractError("engine results require an engine run ID")
        if self.status == "released" and (not self.actuary_approval_ref or self.exceptions):
            raise ContractError("release requires actuary approval and no unresolved exceptions")


@dataclass(frozen=True)
class ReconciliationResult(DesignContract):
    batch_id: str
    engine_run_id: str
    rule_set_version: str
    exceptions: list[str]
    plausibility_passed: bool


@dataclass(frozen=True)
class ActuarySample(DesignContract):
    batch_id: str
    engine_run_id: str
    member_refs: list[str]


@dataclass(frozen=True)
class LifeEvent(DesignContract):
    event_id: str
    member_ref: str
    event_type: Literal["employment_change", "divorce", "death", "retirement"]
    document_refs: list[str]


@dataclass(frozen=True)
class DocumentCheck(DesignContract):
    event_id: str
    complete: bool
    authentic: bool


EventReason = Literal[
    "complex_case_human_review", "unknown_event_type", "documents_incomplete",
    "documents_unverified", "routine_rule_covered", "member_request",
]
EscalationReason = Literal["advice_intent", "member_request", "component_failure", "safety"]


@dataclass(frozen=True)
class EventOutcome(DesignContract):
    event_id: str
    route: Literal["submitted", "exception_queue"]
    reason_code: EventReason
    administrator_ref: str | None = None

    def __post_init__(self):
        super().__post_init__()
        if self.route == "submitted" and not self.administrator_ref:
            raise ContractError("a submitted event requires an administrator reference")
        if self.route == "exception_queue" and self.administrator_ref is not None:
            raise ContractError("an exception is not a submitted event")
        if (self.route == "submitted") != (self.reason_code == "routine_rule_covered"):
            raise ContractError("only a routine rule-covered event can be submitted")


@dataclass(frozen=True)
class ScheduleConversion(DesignContract):
    run_plan_id: str
    rule_set_version: str
    member_refs: list[str]
    capacity: int

    def __post_init__(self):
        super().__post_init__()
        if self.capacity < 1 or not self.member_refs:
            raise ContractError("a nonempty batch and positive capacity are required")


@dataclass(frozen=True)
class BatchReference(DesignContract):
    batch_id: str


@dataclass(frozen=True)
class RouteException(BatchReference):
    exception_id: str


@dataclass(frozen=True)
class BuildSample(BatchReference):
    size: int
    seed: int

    def __post_init__(self):
        super().__post_init__()
        if self.size < 1:
            raise ContractError("sample size must be positive")


@dataclass(frozen=True)
class EventReference(DesignContract):
    event_id: str


@dataclass(frozen=True)
class RouteEventException(EventReference):
    reason_code: Literal["member_request", "complex_case_human_review"] = "member_request"


@dataclass(frozen=True)
class SearchNextSteps(DesignContract):
    query: str


@dataclass(frozen=True)
class Escalate(DesignContract):
    reason_code: EscalationReason


@dataclass(frozen=True)
class JourneyState(DesignContract):
    journey_id: str
    journey_stage: str
    open_items: list[str]


@dataclass(frozen=True)
class SelectNextStep(DesignContract):
    advice_seeking: bool = False
    option_level: bool = False


@dataclass(frozen=True)
class EmptyRequest(DesignContract):
    pass
