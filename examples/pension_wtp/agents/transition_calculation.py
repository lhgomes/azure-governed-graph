"""L3 design-depth orchestration only; all results and samples come from the engine."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from typing import Protocol

from pension_wtp.agents.design_contracts import (
    ActuarySample, BuildSample, ConversionBatch, IdempotentWrites,
    ReconciliationResult, RouteException, ScheduleConversion, domain_id,
)
from governedgraph.common.contracts import ContractError


class ActuarialEngine(Protocol):
    def reconcile(self, batch_id: str, idempotency_key: str) -> ReconciliationResult: ...
    def build_actuary_sample(self, batch_id: str, size: int, seed: int,
                             idempotency_key: str) -> ActuarySample: ...
    def release(self, batch_id: str, actuary_approval_ref: str,
                idempotency_key: str) -> ConversionBatch: ...


class TransitionCalculationAgent:
    TOOLS = ("schedule_conversion_batch", "get_batch_status", "run_reconciliation_check",
             "route_exception", "build_actuary_sample")

    def __init__(self, engine: ActuarialEngine | None, *,
                 approved_plans: dict[str, tuple[str, int]] | None = None):
        self.engine = engine
        self.approved_plans = dict(approved_plans if approved_plans is not None else {"RP1": ("RS-1", 10)})
        self.queue: list[ConversionBatch | RouteException] = []
        self._batches: dict[str, ConversionBatch] = {}
        self._reconciliations: dict[str, ReconciliationResult] = {}
        self._writes = IdempotentWrites()

    def schedule_conversion_batch(self, run_plan_id: str, rule_set_version: str,
                                  member_refs: list[str], capacity: int, *,
                                  idempotency_key: str | None = None) -> ConversionBatch:
        request = ScheduleConversion(run_plan_id, rule_set_version, member_refs, capacity)
        if self.approved_plans.get(run_plan_id) != (rule_set_version, capacity):
            raise PermissionError("run plan, rule version and capacity must match the approved plan")
        if len(member_refs) > capacity:
            raise ContractError("batch exceeds administrator capacity - queue-based load levelling required")
        payload = asdict(request)
        key = idempotency_key if idempotency_key is not None else domain_id("schedule", payload)

        def enqueue() -> ConversionBatch:
            batch = ConversionBatch(domain_id("B", payload), run_plan_id, rule_set_version, list(member_refs))
            if batch.batch_id in self._batches:
                return self.get_batch_status(batch.batch_id)
            self._batches[batch.batch_id] = batch
            self.queue.append(batch)
            return batch

        return self._writes.apply("schedule", key, payload, enqueue)

    def get_batch_status(self, batch_id: str) -> ConversionBatch:
        return deepcopy(self._batches[batch_id])

    def run_reconciliation_check(self, batch: ConversionBatch, *, idempotency_key: str) -> ReconciliationResult:
        def reconcile() -> ReconciliationResult:
            current = self.get_batch_status(batch.batch_id)
            if current.status == "released":
                raise PermissionError("released results cannot be changed")
            if self.engine is None:
                raise NotImplementedError("APIM actuarial-engine adapter is not configured")
            report = self.engine.reconcile(current.batch_id, idempotency_key)
            if not isinstance(report, ReconciliationResult):
                raise ContractError("engine must return a versioned reconciliation result")
            if report.batch_id != current.batch_id or report.rule_set_version != current.rule_set_version:
                raise ContractError("engine returned a different batch or rule version")
            self._reconciliations[current.batch_id] = report
            self._batches[current.batch_id] = replace(
                current, engine_run_id=report.engine_run_id, exceptions=list(report.exceptions),
                status="awaiting_actuary" if report.plausibility_passed and not report.exceptions else "exceptions",
            )
            return report

        return self._writes.apply("reconcile", idempotency_key, {"batch_id": batch.batch_id}, reconcile)

    def route_exception(self, batch_id: str, exception_id: str, *, idempotency_key: str) -> RouteException:
        request = RouteException(batch_id, exception_id)
        self.get_batch_status(batch_id)

        def enqueue() -> RouteException:
            if request not in self.queue:
                self.queue.append(request)
            return request

        return self._writes.apply("exception", idempotency_key, asdict(request), enqueue)

    def build_actuary_sample(self, batch: ConversionBatch, size: int, seed: int, *,
                             idempotency_key: str) -> ActuarySample:
        batch = self.get_batch_status(batch.batch_id)
        request = BuildSample(batch.batch_id, size, seed)
        if batch.engine_run_id is None:
            raise ContractError("an actuary sample requires an engine-assigned run ID")
        if self.engine is None:
            raise NotImplementedError("APIM actuarial-engine adapter is not configured")

        def sample() -> ActuarySample:
            result = self.engine.build_actuary_sample(batch.batch_id, size, seed, idempotency_key)
            if (not isinstance(result, ActuarySample) or result.batch_id != batch.batch_id
                    or result.engine_run_id != self.get_batch_status(batch.batch_id).engine_run_id
                    or len(result.member_refs) > size
                    or not set(result.member_refs).issubset(batch.member_refs)):
                raise ContractError("invalid engine sample or engine run ID")
            return result

        return self._writes.apply("sample", idempotency_key, asdict(request), sample)

    def release(self, batch: ConversionBatch, actuary_approval_ref: str | None, *,
                idempotency_key: str = "") -> ConversionBatch:
        """Human-only adapter seam, deliberately absent from the agent/APIM tool list."""
        def release() -> ConversionBatch:
            current = self.get_batch_status(batch.batch_id)
            report = self._reconciliations.get(batch.batch_id)
            if (not actuary_approval_ref or report is None or not report.plausibility_passed
                    or current.status != "awaiting_actuary" or current.exceptions):
                raise PermissionError("release requires reconciliation and actuary approval (L3)")
            if self.engine is None:
                raise NotImplementedError("administrator must verify the actuary's approval")
            result = self.engine.release(batch.batch_id, actuary_approval_ref, idempotency_key)
            if result != replace(current, status="released", actuary_approval_ref=actuary_approval_ref):
                raise ContractError("administrator release must not change results or batch scope")
            self._batches[batch.batch_id] = result
            return result

        if not actuary_approval_ref:
            raise PermissionError("results cannot be released without actuary approval (L3)")
        return self._writes.apply("release", idempotency_key,
                                  {"batch_id": batch.batch_id, "approval_ref": actuary_approval_ref}, release)
