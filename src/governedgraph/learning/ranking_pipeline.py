"""Plane P2: bounded continuous ranking of already-approved strategies (B5).

Steps per cycle:
  1. read pseudonymised outcome AGGREGATES (never transcripts, never demographics - B4/B7)
  2. score each eligible strategy: Bayesian blend of expert prior and observed
     first-contact resolution; strategies below the minimum sample keep their prior
  3. reorder within the approved catalogue only, capped by max rank shift per cycle
  4. hand the candidate to the fairness audit + veto gate (Fairness & Explainability Agent)
  5. classify the decision tier (Tier 0 auto-publish / Tier 1 Agentic Assurance Committee)
  6. sign and publish; the control arm keeps the static expert ranking

In Azure this module runs as an Azure ML pipeline step (see infra + prompt 08);
the logic is identical locally so it is unit-testable.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from governedgraph.common.config import DATA_DIR, settings
from governedgraph.common.contracts import RequestKey, _strict
from governedgraph.common.io import load_csv, load_json
from governedgraph.strategy_service.catalogue import Catalogue
from governedgraph.strategy_service.snapshot import RankingSnapshot, Signer, sign_snapshot


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


def load_outcome_aggregates(path: Path | None = None) -> dict[str, dict[str, StrategyEvidence]]:
    if settings.is_azure and path is None:
        raise ValueError("Azure P2 must read governed learning-aggregates, not repository seed data")
    rows = load_csv(path or DATA_DIR / "synthetic" / "outcome_aggregates_illustrative.csv")
    catalogue = Catalogue.load()
    out: dict[str, dict[str, StrategyEvidence]] = defaultdict(dict)
    for r in rows:
        allowed = {"request_key", "strategy_id", "strategy_version", "interactions",
                   "resolved_first_contact", "repeat_contact_14d"}
        if set(r) != allowed:
            raise ValueError("runtime-side aggregates have unknown or cohort columns (B7)")
        RequestKey(*r["request_key"].split("|"))
        if not catalogue.is_active(r["strategy_id"], r["strategy_version"]):
            raise ValueError("aggregate references a non-active strategy version")
        if r["strategy_id"] in out[r["request_key"]]:
            raise ValueError("duplicate strategy aggregate")
        out[r["request_key"]][r["strategy_id"]] = StrategyEvidence(
            r["strategy_id"], int(r["interactions"]), int(r["resolved_first_contact"]), int(r["repeat_contact_14d"])
        )
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
) -> list[CandidateRanking]:
    out = []
    for key_id, prev in previous_rankings.items():
        key = RequestKey(*key_id.split("|"))
        approved = {s.strategy_id for s in catalogue.rankable_for(key)}
        prev = [s for s in prev if s in approved]  # reorder within approved catalogue ONLY
        scores = score_strategies(
            prev, evidence.get(key_id, {}), thresholds["min_sample_per_strategy"], thresholds["prior_pseudo_count"]
        )
        cand = bounded_reorder(prev, scores, thresholds["max_rank_shift_per_cycle"])
        assert set(cand) == set(prev), "ranking may never add or remove strategies (B5)"
        out.append(CandidateRanking(key_id, prev, cand, scores, max_shift(prev, cand)))
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
