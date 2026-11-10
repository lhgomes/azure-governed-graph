"""Retrieve deployment details from the azd environment after `azd up`; nothing here is hard-coded.

    python -m scripts.azd_env show [--task TASK]       summary of what the deployment provides (and what is missing)
    python -m scripts.azd_env export [--task TASK]     shell exports:  eval "$(python -m scripts.azd_env export --task reconcile)"
    python -m scripts.azd_env tasks                    list the tasks

Values come from `azd env get-values` (or `--env-file`). The azd environment holds URLs, names and object/client IDs
only; no secret, key or token is ever read or printed. Run `azd up` (or `azd provision`) first.
"""
from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
from io import StringIO
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from dotenv import dotenv_values

GATEWAY_HOST = re.compile(r"apim-gg-[a-z0-9]+\.azure-api\.net")
ORCHESTRATOR_HOST = re.compile(r"ca-gg-orchestrator-[a-z0-9-]+\.[a-z0-9.-]+\.azurecontainerapps\.io")

# task -> (description, required azd values, optional azd values, extra exports)
TASKS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...], dict[str, str]]] = {
    "presenter": ("Hosted browser presenter (voice demo)",
                  ("VOICE_DEMO_ORIGIN",), ("VOICE_DEMO_CLIENT_ID",), {}),
    "voice-benchmark": ("Twenty-turn in-network voice measurement (scripts/benchmark_voice.py)",
                        ("VOICE_DEMO_ORIGIN", "VOICE_DEMO_CLIENT_ID"), (), {}),
    "voice-proof": ("Hosted synthetic voice/channel proof (scripts/run_dev_proof.py)",
                    ("AZURE_SUBSCRIPTION_ID", "AZURE_RESOURCE_GROUP", "AZURE_ENV_NAME", "ORCHESTRATOR_URL",
                     "APIM_GATEWAY_URL", "AZURE_CONTAINER_REGISTRY_ENDPOINT"), (), {}),
    "reconcile": ("Explainability reconciliation (python -m governedgraph.explainability.reconcile)",
                  ("AZURE_ENV_NAME", "EXPLAINABILITY_STORAGE_URL", "EXPLAINABILITY_CONTAINER", "JOURNEY_STORAGE_URL"),
                  ("GOVERNEDGRAPH_LEGACY_CUTOVER_UTC",), {}),
    "recovery": ("Human recovery from a protective freeze (scripts/lift_dev_freeze.py reads azd itself)",
                 ("AZURE_ENV_NAME", "STRATEGY_TENANT_ID", "STRATEGY_API_CLIENT_ID", "STRATEGY_ADMIN_URL"),
                 ("STRATEGY_RECOVERY_OPERATOR_ID",), {}),
    "p2": ("Azure ML P2 hosting (scripts/run_p2_container_jobs.py reads azd itself)",
           ("AZURE_RESOURCE_GROUP", "AZURE_ML_WORKSPACE", "AZURE_ML_REGISTRY_ID", "AZURE_ML_STORAGE_ID"),
           ("P2_CONTAINER_IMAGE",), {}),
    "agents": ("Foundry agent provisioning (scripts/create_agents.py)",
               ("AZURE_AI_PROJECT_ENDPOINT", "AZURE_AI_MODEL_DEPLOYMENT"),
               ("AZURE_AI_AGENT_NAME", "AZURE_AI_AGENT_VERSION", "AZURE_AI_FAIRNESS_AGENT_NAME",
                "AZURE_AI_FAIRNESS_AGENT_VERSION", "FAIRNESS_MANAGED_IDENTITY_CLIENT_ID"),
           {}),
    "fairness": ("Fairness agent operator invocation",
                 ("AZURE_AI_PROJECT_ENDPOINT", "AZURE_RESOURCE_GROUP"),
                 ("AZURE_AI_FAIRNESS_AGENT_NAME", "AZURE_AI_FAIRNESS_AGENT_VERSION",
                  "FAIRNESS_MANAGED_IDENTITY_CLIENT_ID", "FAIRNESS_AGENT_IMAGE"), {}),
}
GUID_KEYS = ("STRATEGY_TENANT_ID", "STRATEGY_API_CLIENT_ID", "VOICE_DEMO_CLIENT_ID",
             "STRATEGY_RECOVERY_OPERATOR_ID", "FAIRNESS_MANAGED_IDENTITY_CLIENT_ID")
SHOWN_ALWAYS = ("AZURE_ENV_NAME", "AZURE_SUBSCRIPTION_ID", "AZURE_RESOURCE_GROUP", "AZURE_LOCATION")


class EnvError(RuntimeError):
    pass


def load(env_file: str | None = None, *, environment: str | None = None) -> dict[str, str]:
    """The azd environment as a plain dict; empty values are treated as missing."""
    if env_file:
        raw = dotenv_values(Path(env_file))
    else:
        try:
            flags = ["--environment", environment] if environment else []
            text = subprocess.check_output(["azd", "env", "get-values", *flags], text=True, stderr=subprocess.PIPE)
        except FileNotFoundError:
            raise EnvError("the Azure Developer CLI (azd) is not installed; install it or pass --env-file") from None
        except subprocess.CalledProcessError as error:
            raise EnvError("azd has no selected environment; run `azd env select` or `azd up` first "
                           f"({(error.stderr or '').strip()[:200]})") from None
        raw = dotenv_values(stream=StringIO(text))
    return {key: value for key, value in raw.items() if value}


def derived(values: dict[str, str]) -> dict[str, str]:
    """Values the deployment implies but azd does not store; computed, never guessed."""
    out = {}
    if origin := values.get("VOICE_DEMO_ORIGIN"):
        out["PRESENTER_URL"] = origin.rstrip("/") + "/demo/"
    return out


def matches_endpoint(value: str, host_pattern: re.Pattern[str]) -> bool:
    try:
        target = urlsplit(value)
        return (target.scheme == "https" and host_pattern.fullmatch(target.hostname or "") is not None
                and target.netloc == target.hostname and target.path in ("", "/")
                and not target.query and not target.fragment)
    except ValueError:
        return False


def problems(task: str, values: dict[str, str]) -> list[str]:
    _, required, _, _ = TASKS[task]
    found = [f"missing {key}" for key in required if key not in values]
    for key in GUID_KEYS:
        if key in values:
            try:
                UUID(values[key])
            except ValueError:
                found.append(f"{key} is not a GUID")
    if "VOICE_DEMO_ORIGIN" in values:
        host = urlsplit(values["VOICE_DEMO_ORIGIN"]).hostname or ""
        if not GATEWAY_HOST.fullmatch(host):
            found.append("VOICE_DEMO_ORIGIN is not an apim-gg-<suffix>.azure-api.net gateway")
    if "ORCHESTRATOR_URL" in values and urlsplit(values["ORCHESTRATOR_URL"]).scheme != "https":
        found.append("ORCHESTRATOR_URL must be https")
    if "STRATEGY_ADMIN_URL" in values and not values["STRATEGY_ADMIN_URL"].endswith("/strategy-admin"):
        found.append("STRATEGY_ADMIN_URL must end in /strategy-admin")
    if task == "voice-proof":
        gateway = values.get("APIM_GATEWAY_URL", "")
        orchestrator = values.get("ORCHESTRATOR_URL", "")
        if gateway and not matches_endpoint(gateway, GATEWAY_HOST):
            found.append("APIM_GATEWAY_URL must be an https apim-gg-<suffix>.azure-api.net origin")
        if orchestrator and not (matches_endpoint(orchestrator, ORCHESTRATOR_HOST)
                                 or (matches_endpoint(gateway, GATEWAY_HOST)
                                     and orchestrator.rstrip("/") == gateway.rstrip("/") + "/journey")):
            found.append("ORCHESTRATOR_URL must be an https ca-gg-orchestrator-* origin or this gateway's /journey endpoint")
    return found


def selected(task: str | None) -> list[str]:
    return [task] if task else list(TASKS)


def show(values: dict[str, str], task: str | None) -> int:
    status = 0
    print("Deployment")
    for key in SHOWN_ALWAYS:
        print(f"  {key:28} {values.get(key, '(not set)')}")
    for name in selected(task):
        description, required, optional, extra = TASKS[name]
        issues = problems(name, values)
        print(f"\n[{name}] {description}  -> {'OK' if not issues else 'NOT READY'}")
        for key in (*required, *optional):
            print(f"  {key:36} {values.get(key, '(not set)' if key in optional else '(MISSING)')}")
        for key, value in {**derived(values), **extra}.items():
            if key == "PRESENTER_URL" and "VOICE_DEMO_ORIGIN" not in required:
                continue
            print(f"  {key:36} {value}  (derived)")
        for issue in issues:
            print(f"  ! {issue}")
        status |= bool(issues)
    return status


def export(values: dict[str, str], task: str | None) -> int:
    lines = []
    for name in selected(task):
        issues = problems(name, values)
        if issues:
            print(f"# {name}: {'; '.join(issues)}", file=sys.stderr)
            return 1
        _, required, optional, extra = TASKS[name]
        pairs = {key: values[key] for key in (*required, *optional) if key in values}
        pairs.update(extra)
        if "VOICE_DEMO_ORIGIN" in required:
            pairs.update(derived(values))
        lines += [f"export {key}={shlex.quote(value)}" for key, value in pairs.items()]
    print("\n".join(dict.fromkeys(lines)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("show", "export", "tasks"))
    parser.add_argument("--task", choices=sorted(TASKS))
    parser.add_argument("--env-file", help="read values from a dotenv file instead of `azd env get-values`")
    parser.add_argument("--environment", help="explicit azd environment; otherwise use the selected environment")
    args = parser.parse_args(argv)
    if args.command == "tasks":
        for name, (description, *_rest) in TASKS.items():
            print(f"{name:16} {description}")
        return 0
    try:
        values = load(args.env_file, environment=args.environment)
    except EnvError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return show(values, args.task) if args.command == "show" else export(values, args.task)


if __name__ == "__main__":
    raise SystemExit(main())
