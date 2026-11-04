"""B10: the graph must beat the control arm (governed RAG + static expert ranking).

Reports graph-vs-control uplift in first-contact resolution and 14-day repeat
contact. If the treatment does not beat the control at the end of the pre-agreed
evaluation window, the kill switch reverts ranking to static; the rest of the
platform keeps running.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from governedgraph.common.contracts import _strict
from governedgraph.common.io import canonical_json


@dataclass(frozen=True)
class ArmOutcome:
    interactions: int
    resolved: int
    repeat_contacts: int

    def __post_init__(self):
        if (any(isinstance(n, bool) or not isinstance(n, int)
                for n in (self.interactions, self.resolved, self.repeat_contacts))
                or self.interactions <= 0 or not 0 <= self.resolved <= self.interactions
                or not 0 <= self.repeat_contacts <= self.interactions):
            raise ValueError("invalid evaluation-window aggregate counts")

    @classmethod
    def from_dict(cls, payload: dict) -> "ArmOutcome":
        return _strict(cls, payload)

    @property
    def fcr(self) -> float:
        return self.resolved / self.interactions

    @property
    def repeat_rate(self) -> float:
        return self.repeat_contacts / self.interactions


@dataclass(frozen=True)
class UpliftResult:
    fcr_uplift_pts: float
    repeat_reduction_pts: float
    z_fcr: float
    significant: bool
    window_closed: bool
    decision: str  # continue | keep_learning | kill_switch


def two_proportion_z(a: ArmOutcome, b: ArmOutcome) -> float:
    p = (a.resolved + b.resolved) / (a.interactions + b.interactions)
    se = math.sqrt(p * (1 - p) * (1 / a.interactions + 1 / b.interactions))
    return (a.fcr - b.fcr) / se if se else 0.0


def evaluate_uplift(
    treatment: ArmOutcome, control: ArmOutcome, window_closed: bool, z_crit: float = 1.96
) -> UpliftResult:
    z = two_proportion_z(treatment, control)
    sig = z > z_crit
    if sig:
        decision = "continue"
    elif window_closed:
        decision = "kill_switch"
    else:
        decision = "keep_learning"
    return UpliftResult(
        fcr_uplift_pts=round(100 * (treatment.fcr - control.fcr), 2),
        repeat_reduction_pts=round(100 * (control.repeat_rate - treatment.repeat_rate), 2),
        z_fcr=round(z, 3),
        significant=sig,
        window_closed=window_closed,
        decision=decision,
    )


def evaluate_window(payload: dict, service) -> dict:
    import hashlib
    import re

    if (set(payload) != {"window_id", "window_closed", "treatment", "control", "watermark"}
            or payload["watermark"] != "ILLUSTRATIVE - synthetic data"
            or not isinstance(payload["window_id"], str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", payload["window_id"])
            or not isinstance(payload["window_closed"], bool)):
        raise ValueError("invalid governed evaluation-window contract")
    result = evaluate_uplift(ArmOutcome.from_dict(payload["treatment"]),
                             ArmOutcome.from_dict(payload["control"]), payload["window_closed"])
    if result.decision == "kill_switch":
        service.revert_to_static()
    from governedgraph.observability import emit

    emit("graph_uplift", event_id=f"uplift:{payload['window_id']}", window_id=payload["window_id"],
         fcr_uplift_pts=result.fcr_uplift_pts, repeat_reduction_pts=result.repeat_reduction_pts,
         decision=result.decision)
    return {"window_id": payload["window_id"], "aggregate_digest": hashlib.sha256(canonical_json(payload)).hexdigest(),
            **asdict(result)}
