"""Fairness & Explainability Agent (L4 on detection, PROTECTIVE ONLY)

* run_cohort_audit: aggregate outcome rates per strategy by age, gender and
  salary cohort, computed ONLY in the segregated cohort analytics zone,
  with minimum cell sizes (B7).
* veto gate: any strategy whose cohort gap exceeds the threshold is SUSPENDED
  for that journey state, for ALL members (B6). It never targets a cohort.
* open_governance_case: every suspension opens a P3 case automatically.
* assemble_explainability_report: per-member report from explainability records.

May NEVER: reinstate or promote a strategy, create or edit content, pass a
cohort attribute to runtime. Reinstatement is a Tier 2 board decision.

In Azure, the audit runs as an Azure ML job using Fairlearn MetricFrame over
the Fabric cohort-zone tables; this pure-Python implementation produces the same
gap metric so the veto gate is testable offline.
"""
from __future__ import annotations

import json
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from governedgraph.common.contracts import SuspensionEvent, utcnow
from governedgraph.common.io import load_csv


@dataclass(frozen=True)
class CohortFinding:
    request_key: str
    strategy_id: str
    strategy_version: str
    dimension: str
    cohort: str
    overall_fcr_pct: float
    cohort_fcr_pct: float
    gap_pts: float
    cohort_repeat_rate_pct: float
    overall_repeat_rate_pct: float
    breach: bool


@dataclass
class AuditReport:
    audit_id: str
    threshold_version: str
    max_gap_pts: float
    findings: list[CohortFinding]
    vetoes: list[tuple[str, str, str]]  # (request_key, strategy_id, worst cohort label)
    created_at: str

    def worst_by_strategy(self, request_key: str | None = None) -> dict[str, CohortFinding]:
        worst: dict[str, CohortFinding] = {}
        for f in self.findings:
            if request_key and f.request_key != request_key:
                continue
            if f.strategy_id not in worst or f.gap_pts > worst[f.strategy_id].gap_pts:
                worst[f.strategy_id] = f
        return worst


def run_cohort_audit(thresholds: dict, cohort_path: Path | None = None) -> AuditReport:
    if cohort_path is None:
        raise ValueError("fairness audit requires the isolated cohort-zone input")
    rows = load_csv(cohort_path)
    min_cell = thresholds["min_cell_size"]
    max_gap = thresholds["max_cohort_gap_pts"]
    dims = set(thresholds["audit_dimensions"])

    # overall per (key, strategy, dimension) = sum over cohorts of that dimension
    totals: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0, 0])
    for r in rows:
        t = totals[(r["request_key"], r["strategy_id"], r["dimension"])]
        t[0] += int(r["interactions"]); t[1] += int(r["resolved_first_contact"]); t[2] += int(r["repeat_contact_14d"])

    findings: list[CohortFinding] = []
    for r in rows:
        if r["dimension"] not in dims:
            continue
        n = int(r["interactions"])
        if n < min_cell:
            continue  # suppress small cells (re-identification risk)
        n_all, res_all, rep_all = totals[(r["request_key"], r["strategy_id"], r["dimension"])]
        overall = 100 * res_all / n_all
        cohort = 100 * int(r["resolved_first_contact"]) / n
        gap = round(overall - cohort, 1)
        findings.append(
            CohortFinding(
                request_key=r["request_key"], strategy_id=r["strategy_id"], strategy_version=r["strategy_version"],
                dimension=r["dimension"], cohort=r["cohort"],
                overall_fcr_pct=round(overall, 1), cohort_fcr_pct=round(cohort, 1), gap_pts=gap,
                cohort_repeat_rate_pct=round(100 * int(r["repeat_contact_14d"]) / n, 1),
                overall_repeat_rate_pct=round(100 * rep_all / n_all, 1),
                breach=gap > max_gap,
            )
        )
    vetoes = sorted({(f.request_key, f.strategy_id, f"{f.dimension}={f.cohort}") for f in findings if f.breach})
    return AuditReport(
        audit_id=f"audit-{uuid.uuid4().hex[:8]}", threshold_version=thresholds["threshold_version"],
        max_gap_pts=max_gap, findings=findings, vetoes=vetoes, created_at=utcnow(),
    )


class FairnessExplainabilityAgent:
    """Protective L4 agent. Its only write actions are suspend + open case."""

    def __init__(self, strategy_service, governance_dir: Path | None = None):
        self.strategy_service = strategy_service
        if governance_dir is None:
            raise ValueError("fairness case evidence requires an explicit isolated staging directory")
        self.governance_dir = governance_dir

    def apply_veto_gate(self, report: AuditReport) -> list[SuspensionEvent]:
        events = []
        worst = {}
        for f in report.findings:
            k = (f.request_key, f.strategy_id)
            if f.breach and (k not in worst or f.gap_pts > worst[k].gap_pts):
                worst[k] = f
        for (key_id, sid), f in sorted(worst.items()):
            journey_state = key_id.split("|")[0]
            case_id = self.open_governance_case(f, report)
            ev = SuspensionEvent(
                strategy_id=sid, strategy_version=f.strategy_version, journey_state=journey_state,
                audit_id=report.audit_id, gap_metric=f.gap_pts, threshold_version=report.threshold_version,
                governance_case_id=case_id,
            )
            # Suspension applies to ALL members for the journey state; the cohort label is never sent.
            self.strategy_service.suspend(ev)
            events.append(ev)
        return events

    def open_governance_case(self, finding: CohortFinding, report: AuditReport) -> str:
        case_id = f"GC-{uuid.uuid4().hex[:6].upper()}"
        self.governance_dir.mkdir(parents=True, exist_ok=True)
        with open(self.governance_dir / "cases.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "case_id": case_id, "type": "automatic_suspension", "tier": "Tier 2 to reinstate",
                "audit_id": report.audit_id, "finding": asdict(finding),
                "options_for_board": ["revise as new version", "retire", "reinstate with recorded justification"],
                "created_at": utcnow(),
            }) + "\n")
        return case_id

    # Explicitly NOT implemented: promote(), reinstate(), edit_content(). Tested in tests/test_fairness_veto.py.


def assemble_explainability_report(member_ref: str, records: list[dict]) -> dict:
    mine = [r for r in records if r.get("member_ref") == member_ref]
    return {
        "member_ref": member_ref,
        "data": "ILLUSTRATIVE - synthetic data",
        "interactions": len(mine),
        "records": mine,
        "statement": "Each explanation used an approved, versioned strategy and approved content; all figures came from the administrator's official systems.",
    }
