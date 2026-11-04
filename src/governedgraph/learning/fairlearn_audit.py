"""MetricFrame audit over aggregate cells, confined to the fairness identity."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from governedgraph.agents.fairness_explainability import AuditReport, run_cohort_audit
from governedgraph.common.io import load_csv


def aggregate_fcr(y_true, _y_pred, *, sample_weight) -> float:
    return 100 * sum(y_true) / sum(sample_weight)


def aggregate_repeat(_y_true, y_pred, *, sample_weight) -> float:
    return 100 * sum(y_pred) / sum(sample_weight)


def run_metricframe_audit(thresholds: dict, cohort_path: Path) -> AuditReport:
    from fairlearn.metrics import MetricFrame

    report = run_cohort_audit(thresholds, cohort_path)
    groups = defaultdict(list)
    for row in load_csv(cohort_path):
        if row["dimension"] in thresholds["audit_dimensions"]:
            groups[(row["request_key"], row["strategy_id"], row["dimension"])].append(row)
    frames = {}
    for key, cells in groups.items():
        labels = [cell["cohort"] for cell in cells]
        if len(labels) != len(set(labels)):
            raise ValueError("duplicate aggregate cohort cells")
        sizes = [int(cell["interactions"]) for cell in cells]
        resolved = [int(cell["resolved_first_contact"]) for cell in cells]
        repeats = [int(cell["repeat_contact_14d"]) for cell in cells]
        if any(n <= 0 or not 0 <= r <= n or not 0 <= p <= n for n, r, p in zip(sizes, resolved, repeats)):
            raise ValueError("invalid cohort aggregate counts")
        frames[key] = MetricFrame(
            metrics={"fcr": aggregate_fcr, "repeat": aggregate_repeat},
            y_true=resolved,
            y_pred=repeats,
            sensitive_features=labels,
            sample_params={"fcr": {"sample_weight": sizes}, "repeat": {"sample_weight": sizes}},
        )
    for finding in report.findings:
        frame = frames[(finding.request_key, finding.strategy_id, finding.dimension)]
        overall = float(frame.overall["fcr"])
        cohort = float(frame.by_group.loc[finding.cohort, "fcr"])
        gap = round(overall - cohort, 1)
        observed = (
            round(overall, 1), round(cohort, 1), gap,
            round(float(frame.overall["repeat"]), 1),
            round(float(frame.by_group.loc[finding.cohort, "repeat"]), 1),
            gap > thresholds["max_cohort_gap_pts"],
        )
        expected = (
            finding.overall_fcr_pct, finding.cohort_fcr_pct, finding.gap_pts,
            finding.overall_repeat_rate_pct, finding.cohort_repeat_rate_pct, finding.breach,
        )
        if observed != expected:
            raise ValueError("MetricFrame differs from the approved aggregate audit metric")
    return report
