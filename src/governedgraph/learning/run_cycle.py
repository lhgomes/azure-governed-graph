"""Explicit, fairness-reviewed Azure P2 cycle adapter.

Scheduled publication uses the lease-guarded eight-stage scheduled_jobs workflow.
This entry point never accepts repository fixtures or invokes a local fairness agent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Callable
from pathlib import Path
from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler

from governedgraph.agents.fairness_explainability import AuditReport
from governedgraph.learning import assurance_committee
from governedgraph.learning.pipeline_steps import current_id, previous_rankings
from governedgraph.learning.ranking_pipeline import (
    CandidateRanking, build_snapshot, compute_candidates, load_thresholds,
)
from governedgraph.strategy_service.snapshot import default_signer
from governedgraph.common.workflow import run_sync


@dataclass
class CycleResult:
    candidates: list[CandidateRanking]
    audit: AuditReport
    snapshot_id: str
    tier: str
    published: bool
    suspended: dict[str, list[str]]
    workflow_steps: list[str] = field(default_factory=list)


class _CycleStage(Executor):
    def __init__(self, step: str, action: Callable[[], CycleResult | None], steps: list[str]):
        super().__init__(id=step)
        self.action, self.steps = action, steps

    @handler
    async def process(self, _signal: bool, ctx: WorkflowContext[bool, CycleResult]) -> None:
        self.steps.append(self.id)
        result = self.action()
        if result is None:
            await ctx.send_message(True)
        else:
            await ctx.yield_output(result)


def run_cycle(service=None, *, fairness_audit_id: str | None = None,
              outcome_path: Path | None = None) -> CycleResult:
    from governedgraph.learning.azure_inputs import BlobLearningInputs
    from governedgraph.strategy_service.client import StrategyAdminClient

    if service is not None or outcome_path is not None:
        raise ValueError("P2 must publish through the governed Strategy API using isolated Blob aggregates")
    if not fairness_audit_id:
        raise ValueError("an explicit applied fairness audit ID is required")
    service = StrategyAdminClient()
    inputs = BlobLearningInputs()
    thresholds = load_thresholds()
    previous = previous_rankings(service)
    previous_id = current_id(service)
    evidence = {}
    audit: AuditReport | None = None
    candidates: list[CandidateRanking] = []
    steps: list[str] = []

    def aggregate() -> None:
        nonlocal evidence, audit
        evidence, audit = inputs.ranking_inputs(fairness_audit_id, thresholds)

    def rank() -> None:
        nonlocal candidates
        candidates = compute_candidates(service.catalogue, previous, evidence, thresholds)

    def veto() -> None:
        if audit is None:
            raise RuntimeError("learning workflow requires aggregate audit evidence")
        for key, sid, _ in audit.vetoes:
            if sid not in service.suspended_for(key.split("|")[0]):
                raise ValueError("fairness veto has not been applied; refuse publication")

    def publish() -> CycleResult:
        if audit is None:
            raise RuntimeError("learning workflow cannot publish without an audit")
        snap = build_snapshot(service.catalogue, candidates, audit.audit_id, thresholds, default_signer(signing=True))
        suspended = {c.request_key: service.suspended_for(c.request_key.split("|")[0]) for c in candidates}
        published = True
        if snap.tier == "tier1":
            writer = lambda pack: inputs.save_evidence("tier1", snap.snapshot_id, pack)
            published, _ = assurance_committee.review(snap, previous, service.catalogue, suspended,
                                                      thresholds["max_rank_shift_per_cycle"], evidence_writer=writer)
        if published:
            service.load_snapshot(snap, expected_snapshot_id=previous_id)
        return CycleResult(candidates, audit, snap.snapshot_id, snap.tier, published, suspended, list(steps))

    async def execute() -> CycleResult:
        load = _CycleStage("aggregate", aggregate, steps)
        bounded = _CycleStage("bounded-rank", rank, steps)
        fairness = _CycleStage("fairness-veto", veto, steps)
        commit = _CycleStage("sign-govern-publish", publish, steps)
        workflow = (WorkflowBuilder(start_executor=load, name="governed-learning-cycle")
                    .add_edge(load, bounded).add_edge(bounded, fairness).add_edge(fairness, commit).build())
        outputs = (await workflow.run(True)).get_outputs()
        if len(outputs) != 1 or not isinstance(outputs[0], CycleResult):
            raise RuntimeError("learning workflow must produce exactly one governed cycle result")
        return outputs[0]

    return run_sync(execute())


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fairness-audit-id")
    arguments = parser.parse_args()
    r = run_cycle(fairness_audit_id=arguments.fairness_audit_id)
    print(f"snapshot {r.snapshot_id} tier={r.tier} published={r.published}")
    for c in r.candidates:
        print(f"  {c.request_key}: {c.previous} -> {c.candidate} (max shift {c.shift})")
    print(f"  suspended: {r.suspended}")
