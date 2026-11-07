"""Post-deployment synthetic acceptance; never provisions or redeploys application services.

    python -m scripts.acceptance --environment gg-dev
    python -m scripts.acceptance --status

Each invocation retains a fresh attempt. Partial checks never certify full acceptance.
Deployment and acceptance are not approval for real member use.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from scripts.deploy import (
    AGENT_ROLES,
    ENV_NAME,
    STATE_FILE,
    STEPS,
    CommandError,
    Deployer,
    DeployError,
    Prompter,
    Runner,
    State,
    save_json,
)
from scripts.run_foundry_evals import PIN_KEYS

CHECKS = ("verify", "capture", "evaluations")


def binding(deployer: Deployer) -> dict:
    state, sample = deployer.state, deployer.sample
    values = deployer.values()
    expected = {
        "AZURE_ENV_NAME": ENV_NAME, "AZURE_SUBSCRIPTION_ID": deployer.answers.subscription_id,
        "AZURE_RESOURCE_GROUP": deployer.answers.resource_group, "AZURE_LOCATION": deployer.answers.location,
        "GOVERNEDGRAPH_NAMING_SEED": deployer.answers.naming_seed,
        "GOVERNEDGRAPH_SAMPLE": sample.package,
    }
    if any(values.get(key, "") != value or not value and key != "GOVERNEDGRAPH_NAMING_SEED"
           for key, value in expected.items()):
        raise DeployError("acceptance target differs from the saved deployment scope; no resources changed")
    if (state.deployment_status != "succeeded" or not state.deployment_id
            or not all(step in state.completed for step in STEPS) or not deployer.learning_complete()):
        raise DeployError("acceptance requires a completed deployment with learning receipts; "
                          "finish `python -m scripts.deploy --mode resume` first")
    if state.runtime_fingerprint != sample.runtime_fingerprint():
        raise DeployError("runtime inputs differ from the deployed version; "
                          "run `python -m scripts.deploy --mode resume` before acceptance")
    if not values.get("AZURE_AI_PROJECT_ENDPOINT") or any(
            not values.get(key, "").isdecimal() for key in AGENT_ROLES.values()):
        raise DeployError("acceptance requires the deployed Foundry project and every numeric agent pin")
    return {
        "deployment_id": state.deployment_id, "runtime_fingerprint": state.runtime_fingerprint,
        "acceptance_fingerprint": sample.fingerprint(), "scope": expected,
        "project_endpoint": values["AZURE_AI_PROJECT_ENDPOINT"],
        "agent_versions": {key: values[key] for key in AGENT_ROLES.values()},
        "images": {key: values.get(key, "") for key in ("P2_CONTAINER_IMAGE", "DEV_FIXTURES_IMAGE")},
    }


def current_status(deployer: Deployer) -> str:
    if not deployer.state.acceptance:
        return "not_run"
    if deployer.state.acceptance.get("binding") != binding(deployer):
        return "stale"
    return deployer.state.acceptance["status"]


def execute(deployer: Deployer, *, only: str | None = None) -> int:
    record: dict = {}

    def persist() -> None:
        save_json(Path(record["evidence"]), record)
        if State.load(deployer.state_path).deployment_id != record["binding"]["deployment_id"]:
            return
        deployer.state.acceptance = record
        deployer.state.save(deployer.state_path)

    try:
        target = binding(deployer)
        attempt = uuid4().hex
        directory = deployer.state_path.parent / "acceptance"
        evidence = directory / f"{attempt}.json"
        report_path = directory / f"{attempt}-foundry.json"
        started = datetime.now(UTC)
        record = {
            "attempt_id": attempt, "started_at": started.isoformat(),
            "label": "ILLUSTRATIVE - synthetic data", "binding": target,
            "status": "running", "checks": {}, "evidence": str(evidence.resolve()),
            "foundry_report": str(report_path.resolve()),
        }
        deployer.state.completed = [step for step in deployer.state.completed if step not in ("verify", "evaluations")]
        deployer.state.channel_proof_execution = ""
        deployer.state.sample_proof_executions = {}
        persist()
        if deployer.state.sample_fingerprint != target["acceptance_fingerprint"]:
            record["checks"]["local"] = {"status": "running"}
            persist()
            deployer.step_sample()
            record["checks"]["local"] = {"status": "passed"}
            persist()
        selected = (only,) if only else CHECKS
        for check in selected:
            record["checks"][check] = {"status": "running"}
            persist()
            deployer.say(f"\n== acceptance/{check}")
            if check == "evaluations":
                deployer.step_evaluations(report_path)
                report = json.loads(report_path.read_text())
                if not isinstance(report, dict) or not isinstance(report.get("foundry"), dict):
                    raise DeployError("Foundry acceptance report has an invalid shape")
                foundry = report.get("foundry", {})
                versions = {name: target["agent_versions"][key] for name, key in PIN_KEYS.items()}
                if (report.get("evaluation_passed") is not True or report.get("evaluation_status") != "passed"
                        or foundry.get("status") != "completed"
                        or any(not isinstance(foundry.get(key), str) or not foundry[key].strip()
                               for key in ("eval_id", "run_id"))
                        or report.get("project_endpoint") != target["project_endpoint"]
                        or report.get("versions") != versions):
                    raise DeployError("Foundry acceptance did not retain complete passing evidence for the deployed pins")
                record["checks"][check] = {"status": "passed", "eval_id": foundry["eval_id"],
                                           "run_id": foundry["run_id"], "report": str(report_path.resolve())}
                deployer.state.completed.append("evaluations")
            elif check == "verify":
                def retain_execution(mode: str, execution: str) -> None:
                    record["checks"]["verify"].setdefault("executions", {})[mode] = execution
                    persist()

                deployer.step_verify(on_execution=retain_execution)
                if not deployer.sample_proofs_complete():
                    raise DeployError("sample acceptance proofs are incomplete")
                record["checks"][check] = {"status": "passed", "executions": deployer.state.sample_proof_executions}
                deployer.state.completed.append("verify")
            else:
                deployer.step_capture(since=started)
                required = deployer.answers.voice_demo and deployer.admin_enabled()
                record["checks"][check] = {"status": "passed" if required else "not_applicable",
                                           "since": started.isoformat()}
            persist()
        latest = State.load(deployer.state_path)
        if latest.deployment_id != target["deployment_id"] or binding(deployer) != target:
            raise DeployError("deployment or evaluation inputs changed during acceptance; this attempt cannot certify them")
        record["status"] = "passed" if set(CHECKS) <= record["checks"].keys() else "incomplete"
        record["completed_at"] = datetime.now(UTC).isoformat()
        persist()
        deployer.say(f"\nAcceptance: {record['status']}. Evidence: {evidence}")
        deployer.say("Synthetic gg-dev evidence only; no automatic promotion or approval for real member use.")
        return 0
    except (DeployError, OSError, ValueError, KeyError) as error:
        status = "failed"
        if record:
            report_path = Path(record["foundry_report"])
            if isinstance(error, CommandError) and error.code == 2 and report_path.is_file():
                try:
                    report = json.loads(report_path.read_text())
                except (OSError, ValueError) as report_error:
                    error = DeployError(f"{error}; could not read evaluator status: {report_error}")
                else:
                    if (isinstance(report, dict) and report.get("evaluation_status") == "unavailable"
                            and report.get("evaluation_passed") is False):
                        status = "unavailable"
            record["status"] = status
            record["error"] = str(error)
            for check in record["checks"].values():
                if check["status"] == "running":
                    check.update(status=status, error=str(error))
            record["completed_at"] = datetime.now(UTC).isoformat()
            persist()
        print(f"\nAcceptance {status}: {error}", file=sys.stderr)
        print("Deployment status is unchanged. Fix the cause and rerun `python -m scripts.acceptance`; "
              "do not redeploy unchanged services. Failed attempts are retained.", file=sys.stderr)
        return 2 if status == "unavailable" else 1


def main(argv: list[str] | None = None, *, runner: Runner | None = None, state_path: Path = STATE_FILE,
         sleep=time.sleep) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--environment", choices=(ENV_NAME,), default=ENV_NAME)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--only", choices=("verify", "evaluations"), help="diagnostic partial run; never full acceptance")
    args = parser.parse_args(argv)
    deployer = Deployer(runner or Runner(), Prompter(interactive=False), State.load(state_path),
                        state_path=state_path, sleep=sleep, check_local=False)
    if args.status:
        try:
            status = current_status(deployer)
        except DeployError as error:
            print(f"Acceptance cannot certify this deployment: {error}", file=sys.stderr)
            return 1
        print(json.dumps({"deployment_status": deployer.state.deployment_status,
                          "acceptance_status": status, "acceptance": asdict(deployer.state)["acceptance"]}, indent=2))
        return 0
    return execute(deployer, only=args.only)


if __name__ == "__main__":
    raise SystemExit(main())
