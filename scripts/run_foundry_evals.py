"""Publish fresh synthetic guarded-output evaluations to the selected gg-dev Foundry project.

Uses pinned deployed writers and semantic agents with local deterministic adapters.
No live member tokens, new agents, permissions or runtime changes are involved.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import shutil
from uuid import uuid4

from scripts.azd_env import EnvError, load

ROOT = Path(__file__).resolve().parents[1]
PIN_KEYS = {
    "gg-counsellor": "AZURE_AI_AGENT_VERSION",
    "gg-counsellor-voice-compact": "AZURE_AI_VOICE_COMPACT_VERSION",
    "gg-intent-agent": "AZURE_AI_INTENT_AGENT_VERSION",
    "gg-understanding-agent": "AZURE_AI_UNDERSTANDING_AGENT_VERSION",
    "gg-answer-review-agent": "AZURE_AI_REVIEW_AGENT_VERSION",
}


def validate(values: dict[str, str]) -> None:
    if values.get("AZURE_ENV_NAME") != "gg-dev":
        raise EnvError("Foundry synthetic evaluation is restricted to gg-dev")
    if not values.get("AZURE_AI_PROJECT_ENDPOINT"):
        raise EnvError("missing AZURE_AI_PROJECT_ENDPOINT")
    for key in PIN_KEYS.values():
        if not values.get(key, "").isdecimal():
            raise EnvError(f"missing numeric deployed agent pin: {key}")


def run(project, values: dict[str, str], output: Path) -> int:
    # Import the domain only after main has selected the sample and local adapters.
    from governedgraph.agents.counsellor import INSTRUCTIONS
    from governedgraph.agents.decision_agents import FoundryDecisionAgent, agent_definition as decision_definition, deployment_for
    from governedgraph.agents.foundry_runtime import FoundryComposer, agent_definition
    from governedgraph.agents.persona_simulator import ScriptedPersona
    from governedgraph.common.config import settings
    from governedgraph.evaluation import FLOORS, evidence_digest
    from evals.run_evals import load as cases, persona_cases
    from evals.run_model_comparison import UsageRecorder, collect_candidate, score_report

    validate(values)
    if settings.is_azure:
        raise PermissionError("Foundry evaluation requires local deterministic adapters")
    writers = {
        "gg-counsellor": values.get("AZURE_AI_MODEL_DEPLOYMENT", "gpt-counsellor"),
        "gg-counsellor-voice-compact": values.get("AZURE_AI_COMPACT_DEPLOYMENT", "gpt-compact"),
    }
    roles = ("intent", "understanding", "answer-review")
    expected = {name: agent_definition(INSTRUCTIONS, model=model).as_dict() for name, model in writers.items()}
    expected.update({f"gg-{role}-agent": decision_definition(role).as_dict() for role in roles})
    for name, definition in expected.items():
        version = values[PIN_KEYS[name]]
        deployed = project.agents.get_version(name, version)
        if (deployed.name != name or deployed.version != version or deployed.definition.as_dict() != definition):
            raise ValueError(f"{name}/{version} differs from the selected sample's pinned definition")
    report = {
        "label": "SIMULATED - ILLUSTRATIVE - synthetic data",
        "environment": "gg-dev", "project_endpoint": values["AZURE_AI_PROJECT_ENDPOINT"],
        "mode": "local deterministic adapters; real pinned Foundry writers, semantic agents and judges; NOT live proof",
        "actor_source": "fixed synthetic fixtures; not generated personas",
        "human_calibration": "not established", "automatic_promotion": False,
        "versions": {name: values[key] for name, key in PIN_KEYS.items()},
        "dataset_sha256": evidence_digest(cases("curated.jsonl") + cases("adversarial.jsonl") + persona_cases()),
        "rows": {},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        stream.write(json.dumps(report, indent=2) + "\n")
    with project.get_openai_client(max_retries=0, timeout=30) as client:
        recorders = {deployment_for(role): UsageRecorder(client) for role in roles}
        decisions = {role: FoundryDecisionAgent(
            role, client=recorders[deployment_for(role)], agent_version=values[PIN_KEYS[f"gg-{role}-agent"]],
        ) for role in roles}

        def save_row(row):
            report["rows"][row["candidate"]].append(row)
            output.write_text(json.dumps(report, indent=2) + "\n")

        for name, model in writers.items():
            composer = FoundryComposer(INSTRUCTIONS, client=UsageRecorder(client))
            composer.agent_name, composer.agent_version, composer.model = name, values[PIN_KEYS[name]], model
            report["rows"][model] = []
            directory = output.parent / ("work-" + uuid4().hex)
            directory.mkdir()
            try:
                collect_candidate(model, composer, lambda scenario: ScriptedPersona(asdict(scenario)), directory,
                                  on_row=save_row, decision_agents=decisions, supporting_recorders=recorders)
            finally:
                shutil.rmtree(directory)
        score_report(project, report, output, writers["gg-counsellor"], {})
    expected_ids = {case["id"] for case in cases("curated.jsonl") + cases("adversarial.jsonl") + persona_cases()}
    report["evaluation_passed"] = all(
        len(rows) == len(expected_ids) and {row["case_id"] for row in rows} == expected_ids
        and all(row["expectation_met"] and all(row[floor] is True for floor in FLOORS)
                and all(score >= 4 for score in row["judge_scores"].values()) for row in rows)
        and report["summary"][model]["hard_floors"]["demographic_invariance"] is True
        for model, rows in report["rows"].items()
    )
    report["evaluation_status"] = "passed" if report["evaluation_passed"] else "failed"
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "project_endpoint": report["project_endpoint"], **report["foundry"],
        "evaluation_passed": report["evaluation_passed"], "output": str(output),
    }), flush=True)
    return 0 if report["evaluation_passed"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", default="gg-dev")
    parser.add_argument("--output", type=Path, default=ROOT / ".run" / f"foundry-evaluation-{uuid4().hex}.json")
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("--output already exists; use a new path to retain previous evidence")
    try:
        values = load(environment=args.environment)
        validate(values)
    except EnvError as error:
        print(f"Evaluation configuration failed: {error}", file=sys.stderr)
        return 1
    os.environ.update(values)
    sys.path[:0] = [str(ROOT / "src"), str(ROOT / "examples")]
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential
    from openai import OpenAIError

    from governedgraph.foundry_evaluation import EvaluationUnavailable

    try:
        with DefaultAzureCredential() as credential, AIProjectClient(
                endpoint=values["AZURE_AI_PROJECT_ENDPOINT"], credential=credential) as project:
            return run(project, values, args.output)
    except (OpenAIError, TimeoutError, EvaluationUnavailable) as error:
        report = json.loads(args.output.read_text()) if args.output.exists() else {
            "label": "ILLUSTRATIVE - synthetic data", "environment": "gg-dev",
            "project_endpoint": values["AZURE_AI_PROJECT_ENDPOINT"],
        }
        report.update(evaluation_status="unavailable", evaluation_passed=False, error=str(error))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({"evaluation_status": "unavailable", "evaluation_passed": False,
                          "error": str(error), "output": str(args.output)}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
