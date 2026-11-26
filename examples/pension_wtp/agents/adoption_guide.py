"""Transition Adoption Guide (case name: Guided Choice Agent, L2) - DESIGN DEPTH.

Recommends ONLY the next journey step from an approved list. It does not sell,
does not compare or explain multiple pension options to steer a choice and
does not recommend an option. Option-level questions -> warm handover.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable, Literal

from pension_wtp.agents.design_contracts import (
    DesignContract, Escalate, EscalationReason, IdempotentWrites, JourneyState, SelectNextStep, domain_id,
)
from governedgraph.common.contracts import ContractError, EscalationRequest, StrategyResponse

APPROVED_NEXT_STEPS = {
    "NS-01": ("Read your personal transition letter", "C-004", "1.1"),
    "NS-02": ("Check your figures in the fund's online portal", "C-007", "1.2"),
    "NS-03": ("Learn how yearly adjustments work", "C-003", "1.2"),
    "NS-04": ("How to raise a question or an objection", "C-005", "1.1"),
    "NS-05": ("Talk to a human advisor", "C-006", "1.1"),
}


@dataclass(frozen=True)
class NextStep(DesignContract):
    step_id: Literal["NS-01", "NS-02", "NS-03", "NS-04", "NS-05"]
    title: str
    content_id: str
    content_version: str

    def __post_init__(self):
        super().__post_init__()
        if APPROVED_NEXT_STEPS.get(self.step_id) != (self.title, self.content_id, self.content_version):
            raise ContractError("only approved, pinned next-step content is permitted (GNA)")


class AdoptionGuide:
    TOOLS = ("get_journey_state", "get_ranked_strategies", "search_approved_content", "escalate_to_advisor")

    def __init__(self, *, journey_id: str = "J-SYNTHETIC", journey_stage: str = "first_contact",
                 open_items: list[str] | None = None,
                 ranked_reader: Callable[[], StrategyResponse] | None = None):
        self.state = JourneyState(journey_id, journey_stage, list(open_items or []))
        self.ranked_reader = ranked_reader
        self.escalations: list[EscalationRequest] = []
        self._writes = IdempotentWrites()

    def get_journey_state(self) -> JourneyState:
        return self.state

    def get_ranked_strategies(self) -> StrategyResponse:
        if self.ranked_reader is None:
            raise NotImplementedError("host-bound governed Strategy Service adapter is not configured")
        return self.ranked_reader()

    def search_approved_content(self, query: str) -> list[NextStep]:
        return [NextStep(sid, *step) for sid, step in APPROVED_NEXT_STEPS.items()
                if query.casefold() in step[0].casefold() or query == sid]

    def escalate_to_advisor(self, reason_code: EscalationReason = "member_request", *,
                            idempotency_key: str) -> EscalationRequest:
        request = Escalate(reason_code)

        def enqueue() -> EscalationRequest:
            result = EscalationRequest(
                self.state.journey_id, reason_code,
                f"Journey stage: {self.state.journey_stage}. "
                "Warm handover to a licensed human advisor.",
                [], escalation_id=domain_id("ESC", {"journey_id": self.state.journey_id,
                                                   "key": idempotency_key}),
            )
            self.escalations.append(result)
            return result

        return self._writes.apply("escalate", idempotency_key, asdict(request), enqueue)

    def next_step(self, journey_stage: str, open_items: list[str], advice_seeking: bool, *,
                  option_level: bool = False, idempotency_key: str | None = None) -> NextStep:
        SelectNextStep(advice_seeking, option_level)
        if advice_seeking or option_level:
            self.escalate_to_advisor("advice_intent", idempotency_key=(
                idempotency_key if idempotency_key is not None
                else domain_id("handover", {"journey_id": self.state.journey_id})
            ))
            sid = "NS-05"
        elif "objection" in open_items or journey_stage == "objection_raised":
            sid = "NS-04"
        elif journey_stage in ("letter_received", "first_contact"):
            sid = "NS-01"
        elif journey_stage == "values_reviewed":
            sid = "NS-03"
        else:
            sid = "NS-02"
        return NextStep(sid, *APPROVED_NEXT_STEPS[sid])
