"""Private, transcript-free descriptive evaluation of an owner-approved improvement.

Consumes two operator-verified, complete aggregate windows. Never observes user text,
modifies runtime policy, publishes a passage or authorizes a model release (B4/B8/B10).
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
import re

from governedgraph.common.contracts import ContractError

HEX = re.compile(r"[a-f0-9]{64}")
REF = re.compile(r"GB-[a-f0-9]{32}")
PROPOSAL = re.compile(r"GP-[a-f0-9]{24}")
SITUATION_KEYS = ("journey_state", "topic", "channel", "accessibility_mode", "language")
MIN_COMPARABLE = 30


def time(value: str) -> datetime:
    if not isinstance(value, str):
        raise ContractError("window timestamps must be timezone-aware strings")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ContractError("window timestamps require a timezone")
    return result


@dataclass(frozen=True)
class Window:
    start: str
    end: str
    eligible: int
    declined: int
    observed_journeys: int
    coverage_complete: bool
    source_digest: str

    @classmethod
    def parse(cls, value: object) -> "Window":
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ContractError("closed aggregate window expected")
        result = cls(**value)
        if (time(result.start) >= time(result.end)
                or any(type(n) is not int or n < 0 for n in
                       (result.eligible, result.declined, result.observed_journeys))
                or result.declined > result.eligible
                or result.observed_journeys > result.eligible
                or type(result.coverage_complete) is not bool
                or not isinstance(result.source_digest, str)
                or not HEX.fullmatch(result.source_digest)):
            raise ContractError("invalid closed aggregate counts or provenance")
        return result


def evaluate(payload: dict) -> dict:
    """Compare only the same closed situation with provenance and publication boundary.

    A descriptive rate difference is not statistical or causal evidence of uplift.
    Insufficient or incomplete evidence never becomes a success.
    """
    if not isinstance(payload, dict) or set(payload) != {
        "decision_ref", "proposal_id", "situation", "published_at", "before", "after"
    }:
        raise ContractError("unexpected improvement effectiveness input")
    decision, proposal = payload["decision_ref"], payload["proposal_id"]
    if (not isinstance(decision, str) or not REF.fullmatch(decision)
            or not isinstance(proposal, str) or not PROPOSAL.fullmatch(proposal)):
        raise ContractError("valid owner decision and proposal IDs required")
    situation = payload["situation"]
    if (not isinstance(situation, dict) or set(situation) != set(SITUATION_KEYS)
            or any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_*-]{1,80}", value)
                   for value in situation.values())):
        raise ContractError("closed, non-personal situation required")
    published = time(payload["published_at"])
    before, after = Window.parse(payload["before"]), Window.parse(payload["after"])
    if not (time(before.end) <= published <= time(after.start)):
        raise ContractError("comparison windows must be nonoverlapping and bracket publication")
    if before.source_digest == after.source_digest:
        raise ContractError("before/after must have independent evidence provenance")
    ready = (before.coverage_complete and after.coverage_complete
             and min(before.eligible, after.eligible) >= MIN_COMPARABLE
             and min(before.observed_journeys, after.observed_journeys) >= 3)
    pre = (before.declined / before.eligible) if before.eligible else None
    post = (after.declined / after.eligible) if after.eligible else None
    return {
        "decision_ref": decision,
        "proposal_id": proposal,
        "situation": situation,
        "published_at": payload["published_at"],
        "status": "observed_descriptive_change" if ready else "insufficient_evidence",
        "reason": ("Complete comparable closed aggregates; descriptive only, not causal proof"
                   if ready else "Insufficient complete coverage or sample; no improvement claim"),
        "before": asdict(before),
        "after": asdict(after),
        "before_decline_rate": round(pre, 6) if ready else None,
        "after_decline_rate": round(post, 6) if ready else None,
        "decline_rate_delta_percentage_points": round((post - pre) * 100, 2) if ready else None,
        "causal_uplift_proven": False,
        "model_training_input": False,
        "governance_action": "none",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("closed_aggregates", nargs="?", type=Path,
                        help="private operator-provided aggregate JSON (descriptive, unverified)")
    parser.add_argument("--azure-scope", type=Path,
                        help="closed situation and two windows only; read evidence from Azure under operator RBAC")
    options = parser.parse_args(argv)
    if (options.closed_aggregates is None) == (options.azure_scope is None):
        parser.error("provide either closed_aggregates or --azure-scope, not both")
    if options.azure_scope is not None:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        from governedgraph.common.config import settings
        from governedgraph.learning.improvement_evidence import evaluate_azure

        if not all((settings.explainability_account_url, settings.journey_storage_url,
                    settings.content_storage_url)):
            parser.error("protected evidence, journey and approved-content Azure endpoints are required")
        scope = json.loads(options.azure_scope.read_text(encoding="utf-8"))
        with DefaultAzureCredential() as credential:
            audits = BlobServiceClient(settings.explainability_account_url, credential=credential)
            journeys = BlobServiceClient(settings.journey_storage_url, credential=credential)
            content = BlobServiceClient(settings.content_storage_url, credential=credential)
            report = evaluate_azure(
                scope,
                audits=audits.get_container_client(settings.explainability_container),
                journeys=journeys.get_container_client("journey-state"),
                approvals=audits.get_container_client(settings.approvals_container),
                journal=audits.get_container_client(settings.admin_actions_container),
                content_blob=content.get_blob_client(settings.content_container, "corpus.jsonl"),
            )
    else:
        report = evaluate(json.loads(options.closed_aggregates.read_text(encoding="utf-8")))
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "observed_descriptive_change" else 1


if __name__ == "__main__":
    raise SystemExit(main())
