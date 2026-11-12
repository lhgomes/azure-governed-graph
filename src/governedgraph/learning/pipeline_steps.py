"""Closed, aggregate-only artifacts between the isolated P2 pipeline components."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

from governedgraph.common.contracts import _strict
from governedgraph.learning import assurance_committee
from governedgraph.learning.ranking_pipeline import (
    CandidateRanking, StrategyEvidence, build_snapshot, compute_candidates,
    classify_tier, load_outcome_aggregates, load_thresholds,
)
from governedgraph.learning.uplift import evaluate_window
from governedgraph.strategy_service.bootstrap import expert_snapshot
from governedgraph.strategy_service.snapshot import RankingSnapshot, verify_expert_snapshot, verify_snapshot

STAGES = ("aggregate", "rank", "audit", "veto", "sign", "tier", "publish", "uplift")


@dataclass
class PipelineState:
    outcome_digest: str = ""
    evidence: dict[str, dict[str, dict]] = field(default_factory=dict)
    previous: dict[str, list[str]] = field(default_factory=dict)
    previous_snapshot_id: str = ""
    candidates: list[dict] = field(default_factory=list)
    audit_id: str = ""
    snapshot: dict | None = None
    static_snapshot: dict | None = None
    approved: bool = False
    completed_stage: str = ""
    uplift: dict | None = None
    watermark: str = "ILLUSTRATIVE - synthetic data"

    @classmethod
    def from_dict(cls, payload: dict) -> "PipelineState":
        state = _strict(cls, payload)
        if (state.watermark != "ILLUSTRATIVE - synthetic data" or not isinstance(state.approved, bool)
                or state.completed_stage not in ("", *STAGES)):
            raise ValueError("invalid P2 artifact metadata")
        for strategies in state.evidence.values():
            for evidence in strategies.values():
                StrategyEvidence.from_dict(evidence)
        for candidate in state.candidates:
            CandidateRanking.from_dict(candidate)
        if state.snapshot is not None:
            RankingSnapshot.from_dict(state.snapshot)
        if state.static_snapshot is not None:
            RankingSnapshot.from_dict(state.static_snapshot)
        return state


def previous_rankings(service) -> dict[str, list[str]]:
    previous = {key: list(ranking) for key, ranking in service.priors["rankings"].items()}
    if service.current is not None:
        previous.update({key: [sid for sid, _ in ranking] for key, ranking in service.current.rankings.items()})
    return previous


def current_id(service) -> str:
    return service.current.snapshot_id if service.current else "expert-priors-" + service.priors["priors_version"]


def verified_candidates(state: PipelineState, inputs, service, thresholds: dict) -> list[CandidateRanking]:
    evidence, audit = inputs.ranking_inputs(state.audit_id, thresholds)
    if (hashlib.sha256(inputs.outcome_bytes()).hexdigest() != state.outcome_digest
            or {key: {sid: asdict(ev) for sid, ev in rows.items()} for key, rows in evidence.items()} != state.evidence):
        raise ValueError("P2 artifact does not match the audited aggregate input")
    service.refresh()
    if previous_rankings(service) != state.previous or current_id(service) != state.previous_snapshot_id:
        raise ValueError("ranking baseline changed during the pipeline; retry a fresh cycle")
    candidates = compute_candidates(service.catalogue, state.previous, evidence, thresholds, expert_priors=service.priors)
    expected = [asdict(candidate) for candidate in candidates]
    if sorted(expected, key=lambda row: row["request_key"]) != sorted(state.candidates, key=lambda row: row["request_key"]):
        raise ValueError("candidate is not the approved bounded reorder")
    for key, strategy, _ in audit.vetoes:
        if strategy not in service.suspended_for(key.split("|")[0]):
            raise ValueError("fairness veto was not applied for all journeys")
    return candidates


def run_stage(stage: str, state: PipelineState, inputs, service, signer) -> PipelineState:
    if stage not in STAGES:
        raise ValueError("unknown P2 component")
    expected = "" if stage == STAGES[0] else STAGES[STAGES.index(stage) - 1]
    if state.completed_stage != expected:
        raise ValueError("P2 component dependency did not complete")
    thresholds = load_thresholds()
    if stage == "aggregate":
        raw = inputs.outcome_bytes()
        with TemporaryDirectory(prefix="gg-p2-aggregates-") as directory:
            path = Path(directory) / "outcomes.csv"
            path.write_bytes(raw)
            evidence = load_outcome_aggregates(path)
        state.outcome_digest = hashlib.sha256(raw).hexdigest()
        state.evidence = {key: {sid: asdict(ev) for sid, ev in rows.items()} for key, rows in evidence.items()}
    elif stage == "rank":
        service.refresh()
        state.previous = previous_rankings(service)
        state.previous_snapshot_id = current_id(service)
        evidence = {key: {sid: StrategyEvidence.from_dict(ev) for sid, ev in rows.items()}
                    for key, rows in state.evidence.items()}
        state.candidates = [asdict(c) for c in compute_candidates(
            service.catalogue, state.previous, evidence, thresholds, expert_priors=service.priors,
        )]
    elif stage == "audit":
        state.audit_id = inputs.run_fairness(service, expected_digest=state.outcome_digest)
    elif stage in {"veto", "sign", "tier", "publish"}:
        candidates = verified_candidates(state, inputs, service, thresholds)
        if stage == "sign":
            state.snapshot = build_snapshot(service.catalogue, candidates, state.audit_id, thresholds, signer).to_dict()
            state.static_snapshot = expert_snapshot(service.catalogue, service.priors, thresholds, signer).to_dict()
        elif stage in {"tier", "publish"}:
            if state.snapshot is None or state.static_snapshot is None:
                raise ValueError("signed P2 snapshot or static baseline is missing")
            snapshot = RankingSnapshot.from_dict(state.snapshot)
            static = RankingSnapshot.from_dict(state.static_snapshot)
            expected_rankings = {c.request_key: [[sid, service.catalogue.get(sid).version] for sid in c.candidate]
                                 for c in candidates}
            if (snapshot.rankings != expected_rankings or snapshot.source_audit_id != state.audit_id
                    or snapshot.threshold_version != thresholds["threshold_version"]
                    or snapshot.tier != classify_tier(candidates, thresholds)
                    or not verify_snapshot(snapshot, signer)
                    or not verify_expert_snapshot(static, service.catalogue, service.priors, thresholds, signer)):
                raise ValueError("signed snapshot differs from the audited bounded candidate")
            if stage == "tier":
                state.approved = snapshot.tier == "tier0"
                if snapshot.tier == "tier1":
                    suspended = {c.request_key: service.suspended_for(c.request_key.split("|")[0]) for c in candidates}
                    state.approved, _ = assurance_committee.review(
                        snapshot, state.previous, service.catalogue, suspended, thresholds["max_rank_shift_per_cycle"],
                        evidence_writer=lambda pack: inputs.save_evidence("tier1", snapshot.snapshot_id, pack),
                    )
                if not state.approved:
                    raise ValueError("tier routing rejected the snapshot")
            else:
                if not state.approved:
                    raise ValueError("snapshot lacks a successful tier verdict")
                service.load_snapshot(snapshot, expected_snapshot_id=state.previous_snapshot_id, static_snapshot=static)
    elif stage == "uplift":
        state.uplift = evaluate_window(inputs.uplift_input(), service)
        inputs.save_evidence("uplift", state.uplift["aggregate_digest"], state.uplift)
    state.completed_stage = stage
    return state


def run_guarded_stage(stage: str, state: PipelineState, inputs, service, signer) -> PipelineState:
    completed = False
    try:
        result = run_stage(stage, state, inputs, service, signer)
        completed = True
        return result
    finally:
        if not completed:
            service.freeze()
