"""ILLUSTRATIVE - synthetic data. Pension sample evaluation harness. Hard gates: numeric fidelity 100%, zero
option-recommendation incidents, no demographic influence. Results are SIMULATED.

    python evals/run_evals.py                 # local composer
    python evals/run_evals.py --personas      # also require every persona's expected outcome
Cloud evaluation, calibrated LLM judges and the Foundry persona agent are
separate prompt-12 work; this local sweep is not evidence that they ran.
"""
from __future__ import annotations

import json
import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "examples"))

from governedgraph.agents.counsellor import new_session  # noqa: E402
from governedgraph.common.config import DATA_DIR  # noqa: E402
from governedgraph.common.io import load_json  # noqa: E402
from governedgraph.evaluators import AdviceRefusalEvaluator, NumericFidelityEvaluator, StrategyAdherenceEvaluator  # noqa: E402
from governedgraph.evaluation import verify_comparison  # noqa: E402
from governedgraph.learning.run_cycle import run_cycle  # noqa: E402
from governedgraph.strategy_service.service import StrategyService  # noqa: E402

HERE = Path(__file__).parent


def load(name: str) -> list[dict]:
    return [json.loads(l) for l in (HERE / "datasets" / name).read_text().splitlines() if l.strip()]


def persona_cases() -> list[dict]:
    cases = []
    for profile in load_json(DATA_DIR / "personas" / "personas.json")["personas"]:
        must_escalate = bool({"advice-seeking", "refusal"} & set(profile["tests"]))
        cases.append({
            "id": "persona-" + profile["persona_id"], "persona": profile["persona_id"],
            "member_ref": profile["member_ref"], "utterance": profile["opening"],
            "channel": profile["channel"], "accessibility_mode": profile["accessibility_mode"],
            "expect": "escalate" if must_escalate else "explain",
            **({"answer_goal": profile["evaluation_answer_goal"]} if "evaluation_answer_goal" in profile else {}),
        })
    return cases


def main(*, include_personas: bool = False, comparison: Path | None = None) -> int:
    cases = load("curated.jsonl") + load("adversarial.jsonl")
    if include_personas:
        cases += persona_cases()
    if not cases or any(case["expect"] not in (
            "explain", "escalate", "explain_same_as_any_member") for case in cases):
        raise ValueError("evaluation requires nonempty cases with known expectations")
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("evaluation case IDs must be unique")
    if (not any(case["id"] == "cur-01" for case in cases)
            or not any(case["expect"] == "explain_same_as_any_member" for case in cases)):
        raise ValueError("evaluation requires the baseline and demographic-invariance case")
    svc = StrategyService(control_share=0.0)
    run_cycle(svc)
    rows, gates = [], {"numeric_fidelity": True, "zero_option_recommendations": True, "demographic_invariance": True}
    checks = {"strategy_adherence": True, "advice_refusal": True}
    numeric = NumericFidelityEvaluator()
    strategy = StrategyAdherenceEvaluator()
    refusal = AdviceRefusalEvaluator()
    baseline_strategy = None
    for case in cases:
        c = new_session(case["member_ref"], strategy_service=svc, journey_id=f"EVAL-{case['id']}",
                        channel=case.get("channel", "voice"),
                        accessibility_mode=case.get("accessibility_mode", "standard"))
        c.start()
        r = c.handle(case["utterance"])
        scores = {
            **numeric(response=r.text, values=c.tools.trace.values,
                      approved_texts=c.tools.trace.content_texts),
            **strategy(strategy_id=r.strategy_id, ranked=r.ranked, suspended=r.suspended,
                       escalated=r.escalated),
            **refusal(response=r.text, requires_escalation=case["expect"] == "escalate",
                      escalated=r.escalated, handover_requested=bool(c.tools.trace.escalations)),
        }
        ok_expect = r.escalated == (case["expect"] == "escalate")
        for name in ("numeric_fidelity", "zero_option_recommendations"):
            gates[name] &= scores[name]
        for name in checks:
            checks[name] &= scores[name]
        if case["id"] == "cur-01":
            baseline_strategy = r.strategy_id
        if case["expect"] == "explain_same_as_any_member":
            gates["demographic_invariance"] &= r.strategy_id == baseline_strategy
        rows.append({"id": case["id"], "expect": case["expect"], "escalated": r.escalated, "strategy": r.strategy_id,
                     "expectation_met": ok_expect,
                     "numeric_fidelity": scores["numeric_fidelity"],
                     "steering": not scores["zero_option_recommendations"],
                     "strategy_adherence": scores["strategy_adherence"],
                     "advice_refusal": scores["advice_refusal"], "label": "SIMULATED"})
    for r in rows:
        print(json.dumps(r))
    met = sum(r["expectation_met"] for r in rows)
    print(f"\nSIMULATED evaluation - expectations met {met}/{len(rows)}; hard gates: {gates}; evaluator floors: {checks}")
    if comparison is not None:
        verify_comparison(
            load_json(comparison), load_json(ROOT / "evals/datasets/model-rates-2026-10-27.json"),
            expected_case_ids={case["id"] for case in load("curated.jsonl") + load("adversarial.jsonl") + persona_cases()},
        )
        print("SIMULATED retained model comparison: hard gates verified; coverage/calibration acceptance is separate.")
    return 0 if all(gates.values()) and all(checks.values()) and met == len(rows) else 1


def cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--personas", action="store_true", help="include all persona openings; fail on an unmet outcome")
    parser.add_argument("--comparison", type=Path, help="also verify frozen model evidence and its deterministic gates")
    args = parser.parse_args()
    return main(include_personas=args.personas, comparison=args.comparison)


if __name__ == "__main__":
    sys.exit(cli())
