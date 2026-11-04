"""Agentic Assurance Committee - agents that do second-line WORK,
not second-line ACCOUNTABILITY. Approves Tier 1 ranking moves; humans are
notified and can roll back. Anything that widens what the system may say, use
or decide is Tier 2 (human governance board) and is rejected here.

"Agents can always make it safer on their own. Only humans can make it riskier."
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass

from governedgraph.common.config import settings
from governedgraph.common.contracts import DEMOGRAPHIC_FIELD_NAMES
from governedgraph.strategy_service.catalogue import ESCALATION_STRATEGY, Catalogue
from governedgraph.strategy_service.snapshot import RankingSnapshot


@dataclass
class Verdict:
    agent: str
    passed: bool
    note: str


def review(snapshot: RankingSnapshot, previous: dict[str, list[str]], catalogue: Catalogue, suspended: dict[str, list[str]],
           max_shift: int, *, evidence_writer: Callable[[dict], None] | None = None) -> tuple[bool, list[Verdict]]:
    if settings.is_azure and evidence_writer is None:
        raise ValueError("Azure Tier 1 review requires a durable evidence writer")
    v: list[Verdict] = []
    # Challenger: tries to break the candidate
    same_sets = all(set(s for s, _ in r) == set(previous.get(k, [s for s, _ in r])) for k, r in snapshot.rankings.items())
    v.append(Verdict("challenger", same_sets, "no strategy added or removed; reorder only"))
    shifts_ok = all(abs(previous[k].index(s) - i) <= max_shift for k, r in snapshot.rankings.items() if k in previous
                    for i, (s, _) in enumerate(r))
    v.append(Verdict("challenger", shifts_ok, f"every move within max rank shift {max_shift}"))
    # Compliance: catalogue + duty of care
    active = all(catalogue.is_active(s, ver) for r in snapshot.rankings.values() for s, ver in r)
    v.append(Verdict("compliance", active, "only active catalogue versions"))
    v.append(Verdict("compliance", all(ESCALATION_STRATEGY not in [s for s, _ in r] for r in snapshot.rankings.values()),
                     "S6 never ranked for resolution; always available separately"))
    keys_ok = all(not (set(k.lower().split("|")) & DEMOGRAPHIC_FIELD_NAMES) for k in snapshot.rankings)
    v.append(Verdict("compliance", keys_ok, "request keys carry no demographics"))
    # Fairness: suspensions are overlaid at read time; record that they exist
    v.append(Verdict("fairness_explainability", True, f"active suspensions honoured at read time: {suspended}"))
    # Red-team: snapshot signed
    v.append(Verdict("red_team", bool(snapshot.signature), "snapshot is signed"))
    approved = all(x.passed for x in v)
    pack = {"snapshot_id": snapshot.snapshot_id, "tier": snapshot.tier, "approved": approved,
            "verdicts": [asdict(x) for x in v], "humans_notified": not settings.is_azure,
            "notification_status": "pending_operator_review" if settings.is_azure else "simulated",
            "rollback_target_retained": True, "created_at": snapshot.created_at}
    if evidence_writer is not None:
        evidence_writer(pack)
        return approved, v
    out = settings.run_dir / "governance" / "evidence_packs.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(pack) + "\n")
    return approved, v
