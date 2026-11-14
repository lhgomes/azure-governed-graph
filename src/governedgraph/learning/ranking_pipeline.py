"""Plane P2: bounded continuous ranking of already-approved strategies (B5).

Steps per cycle:
  1. read pseudonymised outcome AGGREGATES (never transcripts, never demographics - B4/B7)
  2. score each eligible strategy: Bayesian blend of expert prior and observed
     first-contact resolution; strategies below the minimum sample keep their prior
  3. reorder within the approved catalogue only, capped by max rank shift per cycle
  4. hand the candidate to the fairness audit + veto gate (Fairness & Explainability Agent)
  5. classify the decision tier (Tier 0 auto-publish / Tier 1 Agentic Assurance Committee)
  6. sign and publish; the control arm keeps the static expert ranking

This module runs in the governed Azure ML pipeline; supplied aggregate paths
must point to inputs materialized from the isolated learning zone.
"""
from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from governedgraph.common.config import DATA_DIR
from governedgraph.common.contracts import RequestKey, _strict
from governedgraph.common.io import load_csv, load_json
from governedgraph.strategy_service.catalogue import Catalogue
from governedgraph.strategy_service.snapshot import RankingSnapshot, Signer, sign_snapshot

log = logging.getLogger("governedgraph.learning")


@dataclass(frozen=True)
class StrategyEvidence:
    strategy_id: str
    interactions: int
    resolved: int
    repeat_contacts: int

    def __post_init__(self):
        if (any(isinstance(n, bool) or not isinstance(n, int) for n in
                (self.interactions, self.resolved, self.repeat_contacts))
                or self.interactions < 0 or not 0 <= self.resolved <= self.interactions
                or not 0 <= self.repeat_contacts <= self.interactions):
            raise ValueError("invalid strategy aggregate counts")

    @classmethod
    def from_dict(cls, payload: dict) -> "StrategyEvidence":
        return _strict(cls, payload)

    @property
    def fcr(self) -> float:
        return self.resolved / self.interactions if self.interactions else 0.0


def load_outcome_aggregates(path: Path | None = None, *, catalogue: Catalogue | None = None) -> dict[str, dict[str, StrategyEvidence]]:
    if path is None:
        raise ValueError("P2 must read explicitly supplied governed learning-aggregates")
    rows = load_csv(path)
    catalogue = catalogue or Catalogue.load()
    out: dict[str, dict[str, StrategyEvidence]] = defaultdict(dict)
    seen = set()
    for r in rows:
        allowed = {"request_key", "strategy_id", "strategy_version", "interactions",
                   "resolved_first_contact", "repeat_contact_14d"}
        if set(r) != allowed:
            raise ValueError("runtime-side aggregates have unknown or cohort columns (B7)")
        RequestKey(*r["request_key"].split("|"))
        identity = (r["request_key"], r["strategy_id"], r["strategy_version"])
        if identity in seen:
            raise ValueError("duplicate strategy aggregate")
        seen.add(identity)
        evidence = StrategyEvidence(
            r["strategy_id"], int(r["interactions"]), int(r["resolved_first_contact"]), int(r["repeat_contact_14d"]))
        if not catalogue.is_active(r["strategy_id"], r["strategy_version"]):
            if catalogue.historical(r["strategy_id"], r["strategy_version"]) is None:
                raise ValueError("aggregate references an unapproved strategy version")
            log.info("historical_strategy_aggregate_excluded",
                extra={"strategy_id": r["strategy_id"], "strategy_version": r["strategy_version"]})
            continue
        out[r["request_key"]][r["strategy_id"]] = evidence
    return out


def prior_rate(rank: int) -> float:
    """Expert prior expressed as a pseudo first-contact-resolution rate by prior rank."""
    return max(0.40, 0.70 - 0.02 * (rank - 1))


def score_strategies(
    prior_order: list[str], evidence: dict[str, StrategyEvidence], min_sample: int, pseudo_count: int
) -> dict[str, float]:
    scores = {}
    for i, sid in enumerate(prior_order):
        p = prior_rate(i + 1)
        ev = evidence.get(sid)
        if ev is None or ev.interactions < min_sample:
            scores[sid] = p  # not enough evidence: stay on the expert prior
        else:
            scores[sid] = (ev.resolved + pseudo_count * p) / (ev.interactions + pseudo_count)
    return scores


def bounded_reorder(previous: list[str], scores: dict[str, float], max_shift: int) -> list[str]:
    """Order by score, but no strategy moves more than ``max_shift`` places per cycle.

    Greedy fill: at each position, a strategy that would otherwise exceed its
    downward bound is placed first; otherwise the best-scoring strategy whose
    upward bound allows this position is placed.
    """
    prev_rank = {sid: i for i, sid in enumerate(previous)}
    remaining = list(previous)
    result: list[str] = []
    for pos in range(len(previous)):
        forced = [s for s in remaining if prev_rank[s] + max_shift <= pos]
        if forced:
            pick = min(forced, key=lambda s: prev_rank[s])
        else:
            allowed = [s for s in remaining if prev_rank[s] - max_shift <= pos]
            pick = max(allowed, key=lambda s: (scores.get(s, 0.0), -prev_rank[s]))
        result.append(pick)
        remaining.remove(pick)
    return result


def max_shift(previous: list[str], new: list[str]) -> int:
    return max((abs(previous.index(s) - new.index(s)) for s in new), default=0)


@dataclass
class CandidateRanking:
    request_key: str
    previous: list[str]
    candidate: list[str]
    scores: dict[str, float]
    shift: int

    @classmethod
    def from_dict(cls, payload: dict) -> "CandidateRanking":
        return _strict(cls, payload)


def compute_candidates(
    catalogue: Catalogue,
    previous_rankings: dict[str, list[str]],
    evidence: dict[str, dict[str, StrategyEvidence]],
    thresholds: dict,
    *,
    expert_priors: dict | None = None,
) -> list[CandidateRanking]:
    """Bounded ranking using the immutable expert prior as the Bayesian baseline.

    Without a mature, minimum-size sample, keep the preceding learned order.
    Never infer an improvement from missing feedback.
    """
    priors = expert_priors if expert_priors is not None else load_json(DATA_DIR / "catalogue" / "expert_priors_v1.json")
    if not isinstance(priors, dict):
        raise TypeError("approved expert priors must be an object")
    unsupported = set(evidence) - set(previous_rankings)
    if unsupported:
        log.warning("learning_unsupported_contexts", extra={"context_count": len(unsupported)})
    out = []
    for key_id, previous in previous_rankings.items():
        key = RequestKey(*key_id.split("|"))
        approved = {strategy.strategy_id for strategy in catalogue.rankable_for(key)}
        prev = [sid for sid in previous if sid in approved]
        prior_order = priors["rankings"].get(key_id, priors["default"])
        if (len(set(prior_order)) != len(prior_order)
                or not set(prev) <= set(prior_order)
                or any(sid not in approved for sid in prev)):
            raise ValueError(f"{key_id}: expert priors do not cover the approved previous ranking")
        observed = evidence.get(key_id, {})
        prior_scores = score_strategies(
            prior_order, observed, thresholds["min_sample_per_strategy"], thresholds["prior_pseudo_count"],
        )
        scores = {sid: prior_scores[sid] for sid in prev}
        has_evidence = any(
            sid in observed and observed[sid].interactions >= thresholds["min_sample_per_strategy"]
            for sid in prev
        )
        candidate = (bounded_reorder(prev, scores, thresholds["max_rank_shift_per_cycle"])
                     if has_evidence else list(prev))
        if set(candidate) != set(prev):
            raise ValueError("ranking may never add or remove strategies (B5)")
        out.append(CandidateRanking(key_id, prev, candidate, scores, max_shift(prev, candidate)))
    return out


def classify_tier(candidates: list[CandidateRanking], thresholds: dict) -> str:
    """Tier 0: auto-publish within bounds. Tier 1: larger move -> Agentic Assurance Committee."""
    worst = max((c.shift for c in candidates), default=0)
    return "tier1" if worst >= thresholds["tier1_rank_shift"] else "tier0"


def build_snapshot(
    catalogue: Catalogue,
    candidates: list[CandidateRanking],
    audit_id: str,
    thresholds: dict,
    signer: Signer,
) -> RankingSnapshot:
    snap = RankingSnapshot(
        snapshot_id=f"snap-{uuid.uuid4().hex[:10]}",
        catalogue_version=catalogue.version,
        threshold_version=thresholds["threshold_version"],
        source_audit_id=audit_id,
        tier=classify_tier(candidates, thresholds),
        rankings={c.request_key: [[s, catalogue.get(s).version] for s in c.candidate] for c in candidates},
    )
    return sign_snapshot(snap, signer)


def load_thresholds(path: Path | None = None) -> dict:
    return load_json(path or DATA_DIR / "fairness" / "thresholds_v1.json")
