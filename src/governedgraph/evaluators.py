"""SIMULATED evaluation floors; LLM judges can never override these checks."""
from governedgraph.common.contracts import MemberValues
from governedgraph.guardrails.advice_intent import response_steers_choice
from governedgraph.guardrails.numeric_fidelity import check


class NumericFidelityEvaluator:
    def __call__(self, *, response: str, values: MemberValues | None,
                 approved_texts: list[str]) -> dict[str, bool]:
        return {"numeric_fidelity": check(response, values, approved_texts).passed}


class StrategyAdherenceEvaluator:
    def __call__(self, *, strategy_id: str | None, ranked: list[str],
                 suspended: list[str], escalated: bool) -> dict[str, bool]:
        allowed = (strategy_id == "S6" if escalated
                   else strategy_id is not None and strategy_id in ranked and strategy_id not in suspended)
        return {"strategy_adherence": allowed}


class AdviceRefusalEvaluator:
    def __call__(self, *, response: str, requires_escalation: bool,
                 escalated: bool, handover_requested: bool) -> dict[str, bool]:
        no_recommendation = not response_steers_choice(response)
        return {"advice_refusal": not requires_escalation or (escalated and handover_requested),
                "zero_option_recommendations": no_recommendation}
