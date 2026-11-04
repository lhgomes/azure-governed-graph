"""SIMULATED offline evidence and calibration. Never updates runtime behaviour."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re

from governedgraph.common.contracts import ContractError, _strict

DIMENSIONS = ("groundedness", "strategy_style", "advice_refusal")
FLOORS = ("numeric_fidelity", "strategy_adherence", "advice_refusal", "zero_option_recommendations")


def evidence_digest(item: dict) -> str:
    return hashlib.sha256(json.dumps(item, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_scores(scores: dict) -> None:
    if not isinstance(scores, dict) or set(scores) != set(DIMENSIONS) or any(
            type(score) is not int or not 1 <= score <= 5 for score in scores.values()):
        raise ContractError("all three judge dimensions require integer scores from one to five")


@dataclass(frozen=True)
class HumanRating:
    case_id: str
    evidence_sha256: str
    reviewer_reference: str
    source: str
    scores: dict[str, int]

    def __post_init__(self):
        if (self.source != "human" or any(not isinstance(value, str) or not value.strip()
                                        for value in (self.case_id, self.reviewer_reference, self.evidence_sha256))
                or not re.fullmatch(r"[a-f0-9]{64}", self.evidence_sha256)):
            raise ContractError("genuine human rating and exact evidence reference required")
        validate_scores(self.scores)

    @classmethod
    def from_dict(cls, body: dict) -> HumanRating:
        return _strict(cls, body)


def calibrate(items: list[dict], ratings: list[HumanRating]) -> dict:
    if not items or not ratings:
        raise ContractError("human calibration requires nonempty evidence and human ratings")
    by_id = {item["case_id"]: item for item in items}
    if len(by_id) != len(items) or len({r.case_id for r in ratings}) != len(ratings):
        raise ContractError("calibration IDs must be unique")
    if set(by_id) != {rating.case_id for rating in ratings}:
        raise ContractError("human ratings must cover exactly the calibration evidence")
    for rating in ratings:
        item = by_id[rating.case_id]
        if evidence_digest(item["evidence"]) != rating.evidence_sha256:
            raise ContractError("human rating refers to changed evidence")
        validate_scores(item["judge_scores"])
    return {dimension: {
        "n": len(ratings),
        "mean_absolute_error": sum(abs(by_id[r.case_id]["judge_scores"][dimension] - r.scores[dimension])
                                   for r in ratings) / len(ratings),
        "exact_agreement": sum(by_id[r.case_id]["judge_scores"][dimension] == r.scores[dimension]
                               for r in ratings) / len(ratings),
    } for dimension in DIMENSIONS}


@dataclass(frozen=True)
class TokenRateCard:
    """Currency per million tokens, supplied from an explicitly verified rate card."""
    currency: str
    source: str
    input_per_million: str
    cached_input_per_million: str
    output_per_million: str

    def __post_init__(self):
        prices = (self.input_per_million, self.cached_input_per_million, self.output_per_million)
        if (not isinstance(self.currency, str) or not self.currency.strip()
                or not isinstance(self.source, str) or not self.source.strip()
                or any(not isinstance(price, str) for price in prices)):
            raise ContractError("a sourced finite nonnegative rate card is required")
        try:
            amounts = tuple(Decimal(price) for price in prices)
        except InvalidOperation as error:
            raise ContractError("invalid rate-card amount") from error
        if any(not amount.is_finite() or amount < 0 for amount in amounts):
            raise ContractError("a sourced finite nonnegative rate card is required")

    @classmethod
    def from_dict(cls, body: dict) -> TokenRateCard:
        return _strict(cls, body)

    def cost(self, *, input_tokens: int, cached_input_tokens: int, output_tokens: int) -> Decimal:
        if (any(type(value) is not int or value < 0
                for value in (input_tokens, cached_input_tokens, output_tokens))
                or cached_input_tokens > input_tokens):
            raise ContractError("invalid measured token usage")
        return (Decimal(input_tokens - cached_input_tokens) * Decimal(self.input_per_million)
                + Decimal(cached_input_tokens) * Decimal(self.cached_input_per_million)
                + Decimal(output_tokens) * Decimal(self.output_per_million)) / Decimal(1_000_000)


def summarize_candidate(rows: list[dict], rate_card: TokenRateCard | None = None) -> dict:
    if not rows or len({row["case_id"] for row in rows}) != len(rows):
        raise ContractError("candidate summary needs nonempty unique case IDs")
    for row in rows:
        if row.get("label") != "SIMULATED" or any(type(row[name]) is not bool for name in FLOORS):
            raise ContractError("simulation label and measured boolean floors are required")
        if any(type(row[name]) is not bool for name in ("resolved", "expectation_met")):
            raise ContractError("resolution and expectation verdicts must be observed booleans")
        if (type(row["latency_ms"]) not in (int, float)
                or not math.isfinite(row["latency_ms"]) or row["latency_ms"] < 0):
            raise ContractError("finite measured latency is required")
        if row.get("judge_scores") is not None:
            validate_scores(row["judge_scores"])
    resolved = sum(row["resolved"] for row in rows)
    latencies = sorted(row["latency_ms"] for row in rows)
    judged = [row for row in rows if row.get("judge_scores") is not None]
    cost = None
    if rate_card is not None and all(row.get("usage_complete") is True for row in rows):
        cost = sum((rate_card.cost(**row["usage"]) for row in rows), Decimal(0))
    return {
        "label": "SIMULATED", "n": len(rows), "resolved": resolved,
        "expectations_met": sum(row["expectation_met"] for row in rows),
        "hard_floors": {name: all(row[name] for row in rows) for name in FLOORS},
        "quality": {name: {"n": len(judged), "mean": (
            sum(row["judge_scores"][name] for row in judged) / len(judged) if judged else None)}
                    for name in DIMENSIONS},
        "latency_ms": {"p50": latencies[math.ceil(len(rows) * .5) - 1],
                       "p95": latencies[math.ceil(len(rows) * .95) - 1]},
        "currency": rate_card.currency if rate_card else None,
        "cost": str(cost) if cost is not None else None,
        "cost_per_resolved_query": str(cost / resolved) if cost is not None and resolved else None,
    }


def verify_comparison(report: dict, cards: dict, *, expected_case_ids: set[str],
                      expected_candidates: set[str] | None = None) -> None:
    candidates = {"gpt-counsellor", "gpt-compact"} if expected_candidates is None else expected_candidates
    if (report.get("label") != "SIMULATED - ILLUSTRATIVE - synthetic data"
            or len(candidates) < 2 or set(report["rows"]) != candidates
            or not expected_case_ids):
        raise ContractError("a labelled comparison covering exactly the expected candidates is required")
    for candidate, rows in report["rows"].items():
        if {row["case_id"] for row in rows} != expected_case_ids:
            raise ContractError("comparison must retain every persona and adversarial case")
        for row in rows:
            if row["candidate"] != candidate or row["evidence"]["case_id"] != candidate + "/" + row["case_id"]:
                raise ContractError("candidate evidence identity mismatch")
        summary = summarize_candidate(rows, TokenRateCard.from_dict(cards[candidate]))
        by_id = {row["case_id"]: row for row in rows}
        summary["hard_floors"]["demographic_invariance"] = (
            by_id["cur-01"]["strategy"] == by_id["adv-07"]["strategy"])
        summary["actor_generated"] = sum(row["actor_status"] == "generated" for row in rows)
        if not all(summary["hard_floors"].values()):
            raise ContractError("measured comparison failed a deterministic hard gate")
        if any(metric["n"] != len(rows) for metric in summary["quality"].values()):
            raise ContractError("every measured case requires complete judge evidence")
        if summary != report["summary"][candidate]:
            raise ContractError("reported matrix differs from the measured rows or sourced rate card")
