"""Read-only, demographic-free projections of governed Azure evidence."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from governedgraph.common.config import DATA_DIR, settings
from governedgraph.common.contracts import ACCESSIBILITY_MODES, CHANNELS, RequestKey
from governedgraph.common.io import load_json
from governedgraph.strategy_service.catalogue import Catalogue
from governedgraph.strategy_service.catalogue_release import patterns, stages
from governedgraph.strategy_service.overrides import effective_suspensions, lifted_suspensions, state_revision, suspension_digest

WATERMARK = "ILLUSTRATIVE - synthetic data"


def revision(state: dict) -> str:
    return state_revision(state)


def pick(row: dict, fields: str) -> dict:
    return {key: row[key] for key in fields.split() if key in row}


TIER1_FIELDS = ("snapshot_id tier approved verdicts humans_notified notification_status "
                "rollback_target_retained created_at")


def tier1_decision(pack: dict) -> dict:
    row = pick(pack, TIER1_FIELDS)
    return {"kind": "tier1_review", "reference": row.get("snapshot_id", ""), "tier": row.get("tier", "tier1"),
            "outcome": "approved" if row.get("approved") else "rejected", **row}


def uplift_decision(pack: dict) -> dict:
    row = pick(pack, "window_id aggregate_digest fcr_uplift_pts repeat_reduction_pts z_fcr significant "
               "window_closed decision")
    return {"kind": "uplift_window", "reference": row.get("window_id", ""), "tier": "B10 control comparison",
            "outcome": row.get("decision", "unknown"), **row}


def project_learning(manifest, evidence: dict) -> dict:
    if manifest is None:
        return {"manifest": None, "aggregates": []}
    return {"manifest": asdict(manifest),
            "aggregates": [{"request_key": key, **asdict(row)}
                           for key, strategies in evidence.items() for row in strategies.values()]}


@dataclass(frozen=True)
class Context:
    """The approved domain pack the console explains rankings against."""

    catalogue: Catalogue
    priors: dict
    thresholds: dict
    control_share: float

    @classmethod
    def load(cls) -> Context:
        return cls(Catalogue.load(), load_json(DATA_DIR / "catalogue" / "expert_priors_v1.json"),
                   load_json(DATA_DIR / "fairness" / "thresholds_v1.json"), settings.control_arm_share)


def build_overview(context: Context, state: dict, *, mode: str, cases: list[dict], decisions: list[dict],
                   learning: dict, actions: list[dict]) -> dict:
    current = state["current"]
    keys = sorted(set(context.priors["rankings"]) | set(current["rankings"] if current else {}))
    rankings = []
    for key_id in keys:
        key = RequestKey(*key_id.split("|"))
        eligible = {s.strategy_id for s in context.catalogue.rankable_for(key)}
        suspended = effective_suspensions(state, key.journey_state)
        baseline = context.priors["rankings"].get(key_id, context.priors["default"])
        learned = [sid for sid, _ in current["rankings"][key_id]] if current and key_id in current["rankings"] else baseline
        owner_order = next((item for item in state.get("owner_overrides", [])
                            if item["kind"] == "order" and item["request_key"] == key_id
                            and item["snapshot"] is not None and not state["frozen"]), None)
        if owner_order:
            learned = owner_order["order"]
        rankings.append({
            "request_key": key_id,
            "expert_priors": baseline,
            "snapshot_order": learned,
            "effective_control": [sid for sid in baseline if sid in eligible and sid not in suspended],
            "effective_treatment": [sid for sid in learned if sid in eligible and sid not in suspended],
            "suspended": suspended,
            "source": "owner override" if owner_order else
                      "signed snapshot" if current and key_id in current["rankings"] else "expert priors fallback",
        })
    lifted = lifted_suspensions(state)
    pauses = [{**item, "source": "fairness"} for item in state["suspensions"] if suspension_digest(item) not in lifted]
    pauses.extend({**item, "source": "owner", "audit_id": item["override_id"], "governance_case_id": item["override_id"]}
                  for item in state.get("owner_overrides", []) if item["kind"] == "pause")
    return {
        "watermark": WATERMARK, "mode": mode, "revision": revision(state),
        "catalogue_version": context.catalogue.version,
        "strategies": [asdict(strategy) for strategy in context.catalogue.all()],
        "style_patterns": [asdict(pattern) for pattern in patterns()], "style_stages": sorted(stages()),
        "style_channels": sorted(CHANNELS), "style_accessibility_modes": sorted(ACCESSIBILITY_MODES),
        "state": state, "rankings": rankings, "always_available": ["S6"], "effective_pauses": pauses,
        "control_arm_share": context.control_share,
        "thresholds": pick(context.thresholds, "threshold_version min_sample_per_strategy max_rank_shift_per_cycle "
                           "min_cell_size max_cohort_gap_pts exploration_rate"),
        "cases": cases, "decisions": decisions, "learning": learning, "actions": actions,
    }
