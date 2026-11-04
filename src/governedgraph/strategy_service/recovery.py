"""Human-only dev recovery orchestration; no change to the approved ranking or veto overlay."""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import uuid4

from governedgraph.common.contracts import ContractError, _strict, utcnow
from governedgraph.strategy_service.service import StrategyService
from governedgraph.strategy_service.state import StateUnavailable

REASON = "watchdog proof completed; audit pipeline healthy"
log = logging.getLogger("governedgraph.strategy_service")


class DecisionReferenceUsed(StateUnavailable):
    pass


def validate_decision_ref(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"GB-DEV-\d{4}-\d{3}", value):
        raise ContractError(f"decision-reference format check failed: detected {value!r}; required GB-DEV-YYYY-NNN")


@dataclass(frozen=True)
class RecoveryRequest:
    decision_ref: str
    expected_audit_execution: str

    @classmethod
    def from_dict(cls, payload: dict) -> RecoveryRequest:
        request = _strict(cls, payload)
        validate_decision_ref(request.decision_ref)
        if (not isinstance(request.expected_audit_execution, str)
                or not re.fullmatch(r"job-gg-p2-fairness-[a-z0-9]+", request.expected_audit_execution)):
            raise ContractError(f"audit execution check failed: detected {request.expected_audit_execution!r}")
        return request


def latest_successful_audit(rows: list[dict], image: str) -> str:
    candidates = []
    for row in rows:
        properties = row["properties"]
        containers = properties["template"]["containers"]
        if len(containers) != 1:
            raise StateUnavailable("cannot certify isolated audit execution metadata")
        container = containers[0]
        env = {item["name"]: item.get("value") for item in container.get("env", [])}
        if env.get("GOVERNEDGRAPH_P2_STAGE") == "audit":
            timestamp = datetime.fromisoformat(properties["startTime"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise StateUnavailable("audit timestamp must be timezone-aware")
            candidates.append((timestamp, row, container, env))
    if not candidates:
        raise StateUnavailable("no actual isolated audit execution is available")
    candidates.sort(key=lambda item: item[0], reverse=True)
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        raise StateUnavailable("latest audit execution is ambiguous")
    _, row, container, env = candidates[0]
    command = container.get("command")
    native = (command == ["python", "-m", "governedgraph.learning.scheduled_jobs"]
              and env.get("GOVERNEDGRAPH_P2_CADENCE_SECONDS") in {"300", "14400"})
    component = (command == ["python", "-m", "governedgraph.learning.container_jobs", "--stage", "audit"]
                 and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", env.get("GOVERNEDGRAPH_P2_CYCLE_ID", "")))
    if (row["properties"]["status"] != "Succeeded" or env.get("GOVERNEDGRAPH_P2_ROLE") != "fairness"
            or container["image"] != image or env.get("GOVERNEDGRAPH_P2_IMAGE") != image
            or not (native or component)
            or not re.fullmatch(r"job-gg-p2-fairness-[a-z0-9]+", row["name"])):
        raise StateUnavailable("latest isolated audit has not successfully completed the approved audit code")
    return row["name"]


class RecoveryJournal(Protocol):
    def used_decision_refs(self) -> set[str]: ...

    def write(self, identifier: str, stage: str, record: dict) -> None: ...


def require_unused_decision_ref(decision_ref: str, journal: RecoveryJournal) -> None:
    validate_decision_ref(decision_ref)
    if decision_ref in journal.used_decision_refs():
        raise DecisionReferenceUsed(f"decision-reference reuse check failed: detected {decision_ref!r}; already recorded")


def release(
    service: StrategyService, request: RecoveryRequest, actor_id: str,
    audit_health: Callable[[], str], journal: RecoveryJournal,
) -> dict:
    require_unused_decision_ref(request.decision_ref, journal)
    revision = service.frozen_revision()
    if audit_health() != request.expected_audit_execution:
        raise StateUnavailable("latest successful audit changed; review recovery again")
    identifier = "recovery-" + uuid4().hex
    record = {
        "watermark": "ILLUSTRATIVE - synthetic data", "recovery_id": identifier,
        "actor_id": actor_id, "requested_at": utcnow(), "decision_ref": request.decision_ref,
        "reason": REASON, "audit_execution": request.expected_audit_execution, "state_revision": revision,
    }
    journal.write(identifier, "requested", record)
    attempted, completed = False, False
    try:
        if audit_health() != request.expected_audit_execution:
            raise StateUnavailable("latest audit changed before recovery")
        attempted = True
        service.release_freeze(revision, actor_id=actor_id, decision_ref=request.decision_ref,
                               recovery_id=identifier, audit_execution=request.expected_audit_execution,
                               requested_at=record["requested_at"], reason=REASON)
        if audit_health() != request.expected_audit_execution:
            raise StateUnavailable("latest audit changed during recovery")
        journal.write(identifier, "completed", {**record, "completed_at": utcnow(), "frozen": False})
        completed = True
        return {"recovery_id": identifier, "frozen": False, "decision_ref": request.decision_ref,
                "audit_execution": request.expected_audit_execution}
    finally:
        if not completed:
            log.error("strategy_human_recovery_failed", extra={"recovery_id": identifier, "actor_id": actor_id,
                                                               "decision_ref": request.decision_ref})
            if attempted:
                service.freeze()
