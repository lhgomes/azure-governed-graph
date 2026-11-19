"""Automatic, SIMULATED model comparison. Never provisions or promotes a model."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from decimal import Decimal
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "examples"), str(ROOT)]

from governedgraph.agents.decision_agents import (  # noqa: E402
    FoundryDecisionAgent, agent_definition as decision_definition, deployment_for,
)
from governedgraph.agents.foundry_runtime import FoundryComposer, agent_definition  # noqa: E402
from governedgraph.agents.counsellor import INSTRUCTIONS  # noqa: E402
from governedgraph.agents.persona_simulator import FoundryPersona, verify_persona_version  # noqa: E402
from governedgraph.common.config import settings  # noqa: E402
from governedgraph.common.contracts import ContractError, _strict  # noqa: E402
from governedgraph.common.io import load_json  # noqa: E402
from governedgraph.evaluation import DIMENSIONS, TokenRateCard, evidence_digest, verify_comparison  # noqa: E402
from evals.run_evals import load, persona_cases  # noqa: E402
from evals.run_model_comparison import UsageRecorder, collect_candidate, score_report  # noqa: E402

LABEL = "SIMULATED - ILLUSTRATIVE - synthetic data"
ROLES = ("answer-review",)
MODELS = {"gpt-4.1", "gpt-4.1-mini", "gpt-5.4-mini"}


@dataclass(frozen=True)
class Candidate:
    deployment: str
    model: str
    model_version: str
    agent_name: str
    agent_version: str | None
    rate_card: dict

    def __post_init__(self):
        if (self.model not in MODELS or any(not isinstance(value, str) or not value.strip()
                                           for value in (self.deployment, self.model_version, self.agent_name))
                or self.agent_version is not None and (
                    not isinstance(self.agent_version, str) or not self.agent_version.isdecimal())):
            raise ContractError("candidate needs a supported model and an explicit numeric version or null")
        TokenRateCard.from_dict(self.rate_card)

    @classmethod
    def from_dict(cls, body: dict) -> Candidate:
        return _strict(cls, body)


@dataclass(frozen=True)
class Suite:
    label: str
    baseline: str
    judge: str
    persona_version: str
    decision_versions: dict[str, str | None]
    candidates: list[Candidate]

    def __post_init__(self):
        names = [candidate.deployment for candidate in self.candidates]
        if (self.label != LABEL or len(names) < 2 or len(set(names)) != len(names)
                or self.baseline not in names or self.judge not in names
                or not isinstance(self.persona_version, str) or not self.persona_version.isdecimal()
                or set(self.decision_versions) != set(ROLES)
                or any(version is not None and (not isinstance(version, str) or not version.isdecimal())
                       for version in self.decision_versions.values())
                or len({candidate.rate_card["currency"] for candidate in self.candidates}) != 1):
            raise ContractError("suite requires unique paired candidates, one currency and pinned shared roles")

    @classmethod
    def from_dict(cls, body: dict) -> Suite:
        if not isinstance(body, dict) or not isinstance(body.get("candidates"), list):
            raise ContractError("suite requires a candidate list")
        return _strict(cls, {**body, "candidates": [Candidate.from_dict(item) for item in body["candidates"]]})

    def missing_pins(self) -> list[str]:
        return ([f"candidate:{item.deployment}:agent_version" for item in self.candidates if item.agent_version is None]
                + [f"decision:{role}:agent_version" for role, version in self.decision_versions.items() if version is None])

    def cards(self) -> dict:
        return {candidate.deployment: candidate.rate_card for candidate in self.candidates}


def expected_definition(candidate: Candidate) -> dict:
    return agent_definition(
        INSTRUCTIONS, model=candidate.deployment,
        reasoning_effort="none" if candidate.model == "gpt-5.4-mini" else None,
    ).as_dict()


def preflight(project, suite: Suite) -> list[dict]:
    from azure.core.exceptions import ResourceNotFoundError

    failures = []
    for candidate in suite.candidates:
        try:
            deployed = project.deployments.get(candidate.deployment)
        except ResourceNotFoundError:
            failures.append({"id": candidate.deployment, "reason": "missing_model_deployment"})
            continue
        if (deployed.model_name, deployed.model_version) != (candidate.model, candidate.model_version):
            failures.append({"id": candidate.deployment, "reason": "model_version_mismatch"})
        if deployed.sku.name != "DataZoneStandard":
            failures.append({"id": candidate.deployment, "reason": "deployment_sku_rate_mismatch"})
        if candidate.agent_version is None:
            failures.append({"id": candidate.deployment, "reason": "missing_agent_pin"})
            continue
        try:
            agent = project.agents.get_version(candidate.agent_name, candidate.agent_version)
        except ResourceNotFoundError:
            failures.append({"id": candidate.deployment, "reason": "missing_agent_version"})
            continue
        if (agent.name != candidate.agent_name or agent.version != candidate.agent_version
                or agent.definition.as_dict() != expected_definition(candidate)):
            failures.append({"id": candidate.deployment, "reason": "candidate_definition_mismatch"})
    for role, version in suite.decision_versions.items():
        if version is None:
            failures.append({"id": role, "reason": "missing_agent_pin"})
            continue
        try:
            agent = project.agents.get_version(f"gg-{role}-agent", version)
        except ResourceNotFoundError:
            failures.append({"id": role, "reason": "missing_agent_version"})
            continue
        if (agent.name != f"gg-{role}-agent" or agent.version != version
                or agent.definition.as_dict() != decision_definition(role).as_dict()):
            failures.append({"id": role, "reason": "decision_definition_mismatch"})
        if deployment_for(role) not in suite.cards():
            failures.append({"id": role, "reason": "missing_supporting_model_rate"})
    try:
        persona = project.agents.get_version("gg-persona-simulator", suite.persona_version)
    except ResourceNotFoundError:
        failures.append({"id": "persona", "reason": "missing_agent_version"})
    else:
        if persona.name != "gg-persona-simulator" or persona.version != suite.persona_version:
            failures.append({"id": "persona", "reason": "persona_version_mismatch"})
        else:
            try:
                verify_persona_version(persona, settings.compact_deployment)
            except ValueError:
                failures.append({"id": "persona", "reason": "persona_definition_mismatch"})
    return failures


def analyze(report: dict, suite: Suite) -> dict:
    """Recompute every verdict from paired rows, never from an asserted winner."""
    cases = load("curated.jsonl") + load("adversarial.jsonl") + persona_cases()
    if (report.get("configuration_sha256") != evidence_digest(asdict(suite))
            or report.get("dataset_sha256") != evidence_digest(cases)
            or report.get("preflight") != []):
        raise ContractError("report must match the exact suite, dataset and successful preflight")
    expected_ids = {case["id"] for case in cases}
    verify_comparison(report, suite.cards(), expected_case_ids=expected_ids,
                      expected_candidates={item.deployment for item in suite.candidates})
    measured = {}
    cards = {name: TokenRateCard.from_dict(card) for name, card in suite.cards().items()}
    supporting = {deployment_for(role) for role in ROLES}
    for candidate, rows in report["rows"].items():
        costs = []
        complete = True
        for row in rows:
            case = next(case for case in cases if case["id"] == row["case_id"])
            if row["evidence"]["query"] != case["utterance"]:
                raise ContractError("paired candidates must use the exact frozen opening")
            usage = row.get("journey_model_usage", {})
            if set(usage) != supporting | {candidate}:
                raise ContractError("every journey must retain writer and supporting model usage")
            for deployment, observed in usage.items():
                if set(observed) != {"usage", "complete"} or type(observed["complete"]) is not bool:
                    raise ContractError("measured usage needs an explicit completeness verdict")
                costs.append(cards[deployment].cost(**observed["usage"]))
                complete &= observed["complete"]
            if any(usage[candidate]["usage"][name] < value for name, value in row["usage"].items()):
                raise ContractError("journey usage cannot omit writer input, output or reasoning tokens")
            complete &= row["usage_complete"] is True
        summary = report["summary"][candidate]
        cost = sum(costs, Decimal(0)) if complete else None
        measured[candidate] = {
            "journey_model_cost": str(cost) if cost is not None else None,
            "journey_model_cost_per_resolved": (
                str(cost / summary["resolved"]) if cost is not None and summary["resolved"] else None),
            "native_drafts": sum(row["composition_source"] == "foundry" for row in rows),
            "template_fallbacks": sum(row["composition_source"] == "template_fallback" for row in rows),
        }
    baseline = report["summary"][suite.baseline]
    baseline_cost = measured[suite.baseline]["journey_model_cost_per_resolved"]
    comparisons = {}
    for candidate, summary in report["summary"].items():
        details = measured[candidate]
        cost = details["journey_model_cost_per_resolved"]
        checks = {
            "expectation_coverage": summary["expectations_met"] == summary["n"],
            "actor_coverage": summary["actor_generated"] == summary["n"],
            "quality_floor": all(summary["quality"][name]["mean"] >= 4 for name in DIMENSIONS),
            "quality_not_below_baseline": all(
                summary["quality"][name]["mean"] >= baseline["quality"][name]["mean"] for name in DIMENSIONS),
            "resolutions_not_below_baseline": summary["resolved"] >= baseline["resolved"] > 0,
            "initial_response_p95_not_slower": summary["latency_ms"]["p95"] <= baseline["latency_ms"]["p95"],
            "complete_usage_and_native_drafts": cost is not None and details["native_drafts"] > 0,
            "fewer_or_equal_fallbacks": details["template_fallbacks"] <= measured[suite.baseline]["template_fallbacks"],
        }
        cheaper = (Decimal(cost) < Decimal(baseline_cost)
                   if cost is not None and baseline_cost is not None else None)
        comparisons[candidate] = {
            **details, "checks": checks, "cheaper_than_baseline": cheaper,
            "review_candidate": candidate != suite.baseline and all(checks.values()) and cheaper is True,
        }
    return {
        "baseline": suite.baseline, "candidates": comparisons,
        "evaluation_passed": all(all(item["checks"].values()) for item in comparisons.values()),
        "automatic_promotion": False, "human_calibration": "not established by this automated run",
        "latency_scope": "initial guarded text response only; NOT substantive audio or acoustic latency",
        "cost_scope": "journey model tokens including routing, understanding, review and repair; "
                      "excludes persona, offline scoring, Speech, Search and infrastructure",
        "conclusion_scope": "descriptive paired synthetic replay; not statistical certification or approval",
    }


def run_suite(project, suite: Suite, output: Path, report: dict) -> int:
    report["preflight"] = preflight(project, suite)
    output.write_text(json.dumps(report, indent=2) + "\n")
    if report["preflight"]:
        print(json.dumps({"status": "blocked", "failures": report["preflight"]}))
        return 2
    with project.get_openai_client(max_retries=0, timeout=20) as client:
        recorders = {deployment_for(role): UsageRecorder(client) for role in ROLES}
        decisions = {role: FoundryDecisionAgent(
            role, client=recorders[deployment_for(role)], agent_version=suite.decision_versions[role],
        ) for role in ROLES}

        def actor_factory(scenario):
            return FoundryPersona(scenario, client=client, agent_name="gg-persona-simulator",
                                  agent_version=suite.persona_version)

        def save_row(row):
            report["rows"][row["candidate"]].append(row)
            output.write_text(json.dumps(report, indent=2) + "\n")

        for candidate in suite.candidates:
            composer = FoundryComposer(INSTRUCTIONS, client=UsageRecorder(client))
            composer.agent_name, composer.agent_version, composer.model = (
                candidate.agent_name, candidate.agent_version, candidate.deployment,
            )
            report["rows"][candidate.deployment] = []
            with TemporaryDirectory(prefix="gg-model-suite-") as directory:
                collect_candidate(candidate.deployment, composer, actor_factory, Path(directory),
                                  on_row=save_row, decision_agents=decisions, supporting_recorders=recorders)
        score_report(project, report, output, suite.judge, suite.cards())
    report["analysis"] = analyze(report, suite)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["analysis"], indent=2))
    return 0 if report["analysis"]["evaluation_passed"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "evals/datasets/model-suite.json")
    parser.add_argument("--project-endpoint", default=settings.project_endpoint)
    parser.add_argument("--output", type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check-config", action="store_true", help="offline schema check, not a live readiness pass")
    modes.add_argument("--verify-report", type=Path, help="offline recomputation of a completed report")
    args = parser.parse_args(argv)
    suite = Suite.from_dict(load_json(args.config))
    if args.check_config:
        print(json.dumps({"configuration_valid": True, "missing_pins": suite.missing_pins(),
                          "live_readiness": "not checked; no Azure calls"}))
        return 0
    if args.verify_report:
        report = load_json(args.verify_report)
        analysis = analyze(report, suite)
        if report.get("analysis") != analysis:
            raise ContractError("saved analysis differs from the measured evidence")
        print(json.dumps(analysis, indent=2))
        return 0 if analysis["evaluation_passed"] else 1
    if args.output is None:
        parser.error("--output is required for a live comparison")
    if settings.is_azure or settings.environment_name not in ("", "gg-dev"):
        raise PermissionError("suite requires LOCAL deterministic adapters and gg-dev only")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "label": LABEL, "configuration_sha256": evidence_digest(asdict(suite)), "rows": {},
        "configuration": asdict(suite),
        "dataset_sha256": evidence_digest(load("curated.jsonl") + load("adversarial.jsonl") + persona_cases()),
        "mode": "local deterministic adapters; real Foundry journey models/persona/judges; NOT Azure live proof",
        "versions": {"persona": suite.persona_version, "decisions": suite.decision_versions,
                     **{candidate.deployment: candidate.agent_version for candidate in suite.candidates}},
        "cost_scope": "summary.cost is writer-only; analysis.journey_model_cost includes supporting journey models",
    }
    with args.output.open("x") as stream:
        stream.write(json.dumps(report, indent=2) + "\n")
    if suite.missing_pins():
        report["preflight"] = [{"id": name, "reason": "missing_agent_pin"} for name in suite.missing_pins()]
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({"status": "blocked", "failures": report["preflight"]}))
        return 2
    if not args.project_endpoint:
        parser.error("--project-endpoint or AZURE_AI_PROJECT_ENDPOINT is required")
    if settings.environment_name != "gg-dev":
        raise PermissionError("set AZURE_ENV_NAME=gg-dev explicitly before any live model calls")
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    with DefaultAzureCredential() as credential, AIProjectClient(
            args.project_endpoint, credential=credential) as project:
        return run_suite(project, suite, args.output, report)


if __name__ == "__main__":
    sys.exit(main())
