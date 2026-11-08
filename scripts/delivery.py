"""Explicit delivery inputs and immutable build/control-plane primitives."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
LABEL = "ILLUSTRATIVE - synthetic data"
AGENT_ROLES = {
    "counsellor": "AZURE_AI_AGENT_VERSION",
    "voice-compact": "AZURE_AI_VOICE_COMPACT_VERSION",
    "intent": "AZURE_AI_INTENT_AGENT_VERSION",
    "understanding": "AZURE_AI_UNDERSTANDING_AGENT_VERSION",
    "answer-review": "AZURE_AI_REVIEW_AGENT_VERSION",
    "fairness": "AZURE_AI_FAIRNESS_AGENT_VERSION",
}


class DeliveryError(RuntimeError):
    pass


class CommandError(DeliveryError):
    def __init__(self, args, code, stderr=""):
        self.args_, self.code, self.stderr = list(args), code, stderr.strip()
        super().__init__(f"{args[0]} {args[1]} failed ({code}): {self.stderr[-600:]}")


class Runner:
    def run(self, args, *, check=True, env=None) -> str:
        result = subprocess.run(list(map(str, args)), cwd=ROOT, env=env, capture_output=True, text=True)
        if check and result.returncode:
            raise CommandError(args, result.returncode, result.stderr)
        return result.stdout.strip()


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


@dataclass(frozen=True)
class Composition:
    name: str
    data_directory: Path
    release_approval: Path
    dockerfile: str
    p2_dockerfile: str
    workload_template: str
    member_values_module: str
    domain_environment: dict[str, str]

    @classmethod
    def load(cls, path: Path) -> Composition:
        path = path.resolve()
        if not path.is_relative_to(ROOT / "examples"):
            raise DeliveryError("composition must be an explicit extension under examples/")
        raw = json.loads(path.read_text())
        if set(raw) != {"name", "watermark", "data_directory", "release_approval", "dockerfile",
                        "p2_dockerfile", "member_values_module", "domain_environment", "workload_template"} or raw["watermark"] != LABEL:
            raise DeliveryError("composition contains obsolete delivery policy or unlabelled domain inputs")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", raw["name"]):
            raise DeliveryError("invalid extension name")
        directories = [(path.parent / raw[field]).resolve() for field in ("data_directory", "release_approval")]
        docker = (ROOT / raw["dockerfile"]).resolve()
        p2_docker = (ROOT / raw["p2_dockerfile"]).resolve()
        template = (ROOT / raw["workload_template"]).resolve()
        if any(not item.is_relative_to(path.parent) or not item.exists() for item in [*directories, docker, p2_docker, template]):
            raise DeliveryError("domain inputs must exist inside their extension")
        env = raw["domain_environment"]
        if set(env) != {"GOVERNEDGRAPH_DATA_DIR", "GOVERNEDGRAPH_LOCAL_VALUES_PROVIDER", "GOVERNEDGRAPH_TEXT_DEMO_MODULE"}:
            raise DeliveryError("domain environment must contain explicit data, values provider and demo inputs only")
        return cls(raw["name"], directories[0], directories[1], raw["dockerfile"], raw["p2_dockerfile"], raw["workload_template"],
                   raw["member_values_module"], env)


@dataclass(frozen=True)
class Inputs:
    subscription: str
    resource_group: str
    naming_seed: str
    operator_id: str
    on_call_email: str
    composition: str
    location: str = "swedencentral"
    search_location: str = "swedencentral"
    voice_demo: bool = True
    environment: str = "gg-dev"

    @classmethod
    def load(cls, path: Path) -> Inputs:
        raw = json.loads(path.read_text())
        if set(raw) - set(cls.__dataclass_fields__):
            raise DeliveryError("unknown delivery input")
        value = cls(**raw)
        for field in ("subscription", "naming_seed", "operator_id"):
            UUID(getattr(value, field))
        if value.environment != "gg-dev" or value.location != "swedencentral":
            raise DeliveryError("this approved delivery targets gg-dev in swedencentral only")
        if not re.fullmatch(r"[A-Za-z0-9_().-]{1,89}[A-Za-z0-9_()-]", value.resource_group):
            raise DeliveryError("invalid resource group")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value.on_call_email):
            raise DeliveryError("on-call destination required")
        if not isinstance(value.voice_demo, bool):
            raise DeliveryError("voice_demo must be a boolean")
        return value


def parameters(data: dict) -> dict:
    return {"$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
            "contentVersion": "1.0.0.0", "parameters": {key: {"value": value} for key, value in data.items()}}


def provision(runner, inputs: Inputs, template: str, values: dict, *, group: bool = False) -> dict:
    path = ROOT / ".run/delivery-parameters" / (Path(template).stem + ".json")
    save_json(path, parameters(values))
    scope = ["group", "create", "--resource-group", inputs.resource_group] if group else [
        "sub", "create", "--location", inputs.location]
    command = [
        "az", "deployment", *scope, "--subscription", inputs.subscription,
        "--name", "gg-" + Path(template).stem, "--template-file", template,
        "--parameters", "@" + str(path), "--only-show-errors", "-o", "json",
    ]
    for attempt in range(4):
        try:
            result = json.loads(runner.run(command))
            break
        except CommandError as error:
            if (attempt == 3 or "InvalidParameterValueInContainerTemplate" not in error.stderr
                    or "unable to pull image using Managed identity" not in error.stderr):
                raise
            print(f"Waiting for managed-identity image pull propagation ({attempt + 1}/3)...", flush=True)
            time.sleep(30 * 2 ** attempt)
    if result["properties"]["provisioningState"] != "Succeeded":
        raise DeliveryError(f"{template} did not succeed")
    return {key: value["value"] for key, value in result["properties"]["outputs"].items()}


def source_digest(paths: list[Path], metadata: dict) -> str:
    digest = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode())
    files = {file for path in paths for file in (path.rglob("*") if path.is_dir() else [path])
             if file.is_file() and "__pycache__" not in file.parts and file.suffix != ".pyc"}
    for path in sorted(files):
        digest.update(str(path.relative_to(ROOT)).encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def build(runner, registry: str, target: str, dockerfile: str, paths: list[Path], args: dict) -> dict:
    source = source_digest(paths + [ROOT / dockerfile, ROOT / ".dockerignore"], {"target": target, "args": args})
    repository = "governedgraph/" + ("framework-" if Path(dockerfile).parent == Path(".") else "extension-") + target
    tag = repository + ":" + source
    repositories = json.loads(runner.run(["az", "acr", "repository", "list", "-n", registry, "-o", "json"]))
    if not isinstance(repositories, list) or any(not isinstance(item, str) for item in repositories):
        raise DeliveryError("registry repository observation is malformed")
    tags = json.loads(runner.run(["az", "acr", "repository", "show-tags", "-n", registry,
                                 "--repository", repository, "-o", "json"])) if repository in repositories else []
    if not isinstance(tags, list) or any(not isinstance(item, str) for item in tags):
        raise DeliveryError("registry tag observation is malformed")
    if source not in tags:
        print(f"Building {repository}...", flush=True)
        runner.run(["az", "acr", "build", "-r", registry, "-f", dockerfile, "--target", target, "-t", tag,
                    *[part for key, value in args.items() for part in ("--build-arg", f"{key}={value}")],
                    "--build-arg", f"SOURCE_SHA={source}", "."])
    else:
        print(f"Reusing {repository}...", flush=True)
    existing = runner.run(["az", "acr", "repository", "show", "-n", registry, "--image", tag,
                           "--query", "digest", "-o", "tsv"])
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", existing):
        raise DeliveryError(f"immutable {target} image digest missing")
    return {"image": f"{registry}.azurecr.io/{repository}@{existing}", "source_sha256": source,
            "target": target, "dockerfile": dockerfile, "bases": args}


def run_job(runner, inputs: Inputs, name: str, *, sleep=time.sleep) -> str:
    base = ["--subscription", inputs.subscription, "-g", inputs.resource_group, "-n", name]
    history = json.loads(runner.run(["az", "containerapp", "job", "execution", "list", *base, "-o", "json"]))
    active = []
    for item in history:
        status = item.get("properties", {}).get("status")
        if status in {"Pending", "Running", "Processing"}:
            active.append(item["name"])
        elif status not in {"Succeeded", "Failed", "Stopped", "Canceled", "Cancelled"}:
            raise DeliveryError("bootstrap has unknown execution metadata; inspect it before retrying")
    if len(active) > 1:
        raise DeliveryError("bootstrap has multiple active executions; no replacement started")
    execution = active[0] if active else runner.run(
        ["az", "containerapp", "job", "start", *base, "--query", "name", "-o", "tsv"])
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", execution):
        raise DeliveryError("bootstrap execution ID missing")
    for _ in range(120):
        status = runner.run(["az", "containerapp", "job", "execution", "show", *base,
                             "--job-execution-name", execution, "--query", "properties.status", "-o", "tsv"])
        if status == "Succeeded":
            return execution
        if status not in {"Pending", "Running", "Processing"}:
            raise DeliveryError(f"bootstrap {name}/{execution} ended with {status!r}; no replacement execution started")
        sleep(10)
    raise DeliveryError(f"bootstrap {name}/{execution} exceeded bounded wait; inspect it before retrying")


def public_result(runner, inputs: Inputs, job: str, execution: str) -> dict:
    for _ in range(12):
        logs = runner.run(["az", "containerapp", "job", "logs", "show", "--subscription", inputs.subscription,
                          "-g", inputs.resource_group, "-n", job, "--execution", execution,
                          "--container", "bootstrap", "--format", "text", "--tail", "50"])
        matches = re.findall(r"GG_BOOTSTRAP=(\{[^\n]+\})", logs)
        if len(matches) == 1:
            return json.loads(matches[0])
        time.sleep(5)
    raise DeliveryError("bootstrap public receipt unavailable; never infer successful initialization")
