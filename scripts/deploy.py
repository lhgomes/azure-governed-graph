"""Deploy the GovernedGraph reference solution into a new resource group, end to end.

    python -m scripts.deploy                 # asks a few questions, then does everything
    python -m scripts.deploy --plan          # show the questions' answers and the steps; runs nothing
    python -m scripts.deploy --status        # what has completed so far

It asks whether to start fresh, redeploy or resume, then for the subscription, region, resource group name and
an on-call email. The browser presenter, synthetic-conversation capture and the admin console (with its Entra sign-in,
groups and conversation view) are on by default; --no-voice-demo turns off the presenter, capture and conversation view.
It then runs, in order: preflight checks, azd environment, first provision, Search private-link approval, Entra app
registrations (including the console's groups and sign-in), Foundry agents, service deployment, second provision,
member-token key bootstrap, final provision, safe learning bootstrap and a final admin console health/auth check.
Run `python -m scripts.acceptance` afterwards for channel/voice proofs and hosted evaluation. Every deployment step is
idempotent and its completion is recorded in `.run/deploy-state.json`, so after a failure you
fix the cause and run with --mode resume: it resumes where it stopped.
Fresh deployments persist a random naming seed; redeploy and resume keep it unchanged.

Non-interactive use: pass every answer as a flag (--subscription, --location, --resource-group, --on-call-email,
optionally --no-voice-demo) together with --yes.

The environment is always `gg-dev`: the dev token issuer, synthetic contact centre, voice presenter and proof scripts are
enabled only there. This is a dev/reference deployment, not a production one. No secret is read, created or printed; the
only key material handled is a public RSA key, which is validated to contain no private parameters.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from secrets import token_hex
from tempfile import NamedTemporaryFile
from uuid import UUID, uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
STATE_FILE = REPO_ROOT / ".run" / "deploy-state.json"
ENV_NAME = "gg-dev"
SAMPLE_NAME = os.getenv("GOVERNEDGRAPH_SAMPLE", "pension_wtp")
if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", SAMPLE_NAME):
    raise SystemExit("GOVERNEDGRAPH_SAMPLE must be the name of a Python package under examples/")
DEFAULT_SAMPLE_DEFINITION = f"examples/{SAMPLE_NAME}/deployment.json"
LEARNING_CHECKS = ("p2-cycle", "fairness-agent")
WRITERS = {"counsellor": "gg-counsellor", "voice-compact": "gg-counsellor-voice-compact"}
APPROVAL_ENV = "GOVERNEDGRAPH_WRITER_APPROVAL_JSON"
FIXTURES_JOB = "job-gg-dev-learning-fixtures"
SERVICES = ("orchestrator", "strategy-service", "member-values", "contact-centre", "admin")
DEFAULT_LOCATION = "swedencentral"
# EU regions supporting semantic ranker, ordered for the Sweden Central reference deployment.
SEARCH_REGIONS = ("swedencentral", "germanywestcentral", "northeurope", "westeurope", "francecentral",
                  "polandcentral", "italynorth", "spaincentral")
DEFAULT_ISSUER = "https://gg-dev.synthetic.invalid"
ISSUER_JOB = "job-gg-dev-member-issuer"
LINK_MARKER = "approved-content indexer"
PROVIDERS = (
    "Microsoft.App", "Microsoft.CognitiveServices", "Microsoft.Search", "Microsoft.ApiManagement",
    "Microsoft.ContainerRegistry", "Microsoft.MachineLearningServices", "Microsoft.ServiceBus", "Microsoft.KeyVault",
    "Microsoft.Storage", "Microsoft.Network", "Microsoft.Insights", "Microsoft.OperationalInsights",
    "Microsoft.ManagedIdentity", "Microsoft.Authorization",
)
# usage-name suffix -> (units needed). One unit is 1,000 tokens per minute.
QUOTA_NEEDS = {"OpenAI.DataZoneStandard.gpt-4.1": 50, "OpenAI.DataZoneStandard.gpt-4.1-mini": 50,
               "OpenAI.Standard.text-embedding-3-large": 50}
AGENT_ROLES = {  # create_agents role -> azd variable holding its pinned version
    "counsellor": "AZURE_AI_AGENT_VERSION", "voice-compact": "AZURE_AI_VOICE_COMPACT_VERSION",
    "intent": "AZURE_AI_INTENT_AGENT_VERSION", "understanding": "AZURE_AI_UNDERSTANDING_AGENT_VERSION",
    "answer-review": "AZURE_AI_REVIEW_AGENT_VERSION", "fairness": "AZURE_AI_FAIRNESS_AGENT_VERSION",
}
PUBLIC_JWK_FIELDS = {"kty", "n", "e", "alg", "use", "kid", "key_ops"}
RG_NAME = re.compile(r"[A-Za-z0-9_().-]{0,89}[A-Za-z0-9_()-]")
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
LOCATION = re.compile(r"[a-z][a-z0-9]{2,30}")


class DeployError(RuntimeError):
    """A step failed; the message says what to fix. Re-running resumes."""


class LearningCycleStopped(DeployError):
    """Inspect the existing cycle before attempting any replacement work."""


class UnsafeKey(DeployError):
    """The key material found was not a pure public key; never retry or store it."""


class CommandError(DeployError):
    def __init__(self, args, code, stderr=""):
        self.args_, self.code, self.stderr = list(args), code, (stderr or "").strip()
        super().__init__(f"`{' '.join(map(str, args))[:200]}` failed ({code}): {self.stderr[-600:]}")


@dataclass
class Answers:
    subscription_id: str = ""
    location: str = DEFAULT_LOCATION
    search_location: str = ""
    preferred_search_location: str = ""
    resource_group: str = f"rg-{ENV_NAME}"
    on_call_email: str = ""
    voice_demo: bool = True
    member_token_issuer: str = DEFAULT_ISSUER
    naming_seed: str = ""
    sample_definition: str = DEFAULT_SAMPLE_DEFINITION


@dataclass
class State:
    answers: dict = field(default_factory=dict)
    completed: list = field(default_factory=list)
    operator_id: str = ""
    search_capacity_regions: list[str] = field(default_factory=list)
    environment_reset_pending: bool = False
    new_group_required: bool = False
    channel_proof_execution: str = ""
    sample_fingerprint: str = ""
    sample_proof_executions: dict[str, str] = field(default_factory=dict)
    learning_receipts: dict[str, str] = field(default_factory=dict)
    learning_attempt: dict[str, str] = field(default_factory=dict)
    learning_recoveries: list[dict] = field(default_factory=list)
    runtime_fingerprint: str = ""
    deployment_id: str = ""
    deployment_status: str = "pending"
    acceptance: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = STATE_FILE) -> "State":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        return cls(**{item.name: raw[item.name] for item in fields(cls) if item.name in raw})

    def save(self, path: Path = STATE_FILE) -> None:
        save_json(path, asdict(self))


def save_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class SampleDefinition:
    path: Path
    name: str
    data_directory: Path
    local_checks: tuple[tuple[str, tuple[str, ...]], ...]
    deployed_checks: tuple[str, ...]
    learning_fixtures: bool
    learning_checks: tuple[str, ...]
    release_approval: Path

    @classmethod
    def load(cls, filename: str) -> "SampleDefinition":
        path = (REPO_ROOT / filename).resolve()
        if not path.is_relative_to(REPO_ROOT / "examples"):
            raise DeployError("sample definitions must live under examples/")
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise DeployError("could not read the sample deployment definition") from error
        if not isinstance(raw, dict) or set(raw) != {
                "name", "watermark", "data_directory", "local_checks", "deployed_checks", "learning",
                "release_approval"}:
            raise DeployError("sample deployment definition has missing or unknown fields")
        if (not isinstance(raw["name"], str) or not re.fullmatch(r"[a-z][a-z0-9_]*", raw["name"])
                or raw["watermark"] != "ILLUSTRATIVE - synthetic data"
                or not isinstance(raw["data_directory"], str)):
            raise DeployError("sample definition requires a name, synthetic watermark and data directory")
        data = (path.parent / raw["data_directory"]).resolve()
        if not data.is_relative_to(path.parent) or not data.is_dir():
            raise DeployError("sample data must be an existing directory inside the sample")
        if not isinstance(raw["local_checks"], list) or not raw["local_checks"]:
            raise DeployError("sample must declare its local acceptance checks")
        checks = []
        for check in raw["local_checks"]:
            if (not isinstance(check, dict) or set(check) != {"kind", "targets"}
                    or check["kind"] not in ("pytest", "module")
                    or not isinstance(check["targets"], list) or not check["targets"]
                    or any(not isinstance(target, str) for target in check["targets"])):
                raise DeployError("invalid sample local acceptance check")
            targets = check["targets"]
            if check["kind"] == "module":
                if len(targets) != 1 or not re.fullmatch(
                        re.escape(raw["name"]) + r"(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+", targets[0]):
                    raise DeployError("acceptance modules must belong to the selected sample")
            else:
                for target in targets:
                    test = (REPO_ROOT / target).resolve()
                    if (not test.is_relative_to(REPO_ROOT) or not test.is_file()
                            or test.suffix != ".py" or not test.name.startswith("test_")):
                        raise DeployError("sample test targets must be existing repository test files")
            checks.append((check["kind"], tuple(targets)))
        deployed = raw["deployed_checks"]
        if (not isinstance(deployed, list) or not deployed or any(
                not isinstance(mode, str) or mode not in ("channel", "voice") for mode in deployed)
                or len(set(deployed)) != len(deployed) or "channel" not in deployed):
            raise DeployError("sample must declare unique supported proofs including the channel hard gate")
        learning = raw["learning"]
        if (not isinstance(learning, dict) or set(learning) != {"fixtures", "checks"}
                or not isinstance(learning["fixtures"], bool) or not isinstance(learning["checks"], list)
                or any(not isinstance(check, str) or check not in LEARNING_CHECKS for check in learning["checks"])
                or len(set(learning["checks"])) != len(learning["checks"]) or "p2-cycle" not in learning["checks"]):
            raise DeployError("sample must declare unique supported learning checks including the P2 cycle")
        approval = (path.parent / str(raw["release_approval"])).resolve()
        if (not isinstance(raw["release_approval"], str) or not approval.is_relative_to(path.parent)
                or approval.suffix != ".json" or not approval.is_file()):
            raise DeployError("sample must keep its release approval record inside the sample")
        return cls(path, raw["name"], data, tuple(checks), tuple(deployed),
                   learning["fixtures"], tuple(learning["checks"]), approval)

    @property
    def package(self) -> str:
        """The Python package under examples/ this definition belongs to; the container build and Bicep take it as SAMPLE."""
        package = self.path.relative_to(REPO_ROOT / "examples").parts[0]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", package):
            raise DeployError("a sample must be a Python package under examples/ (letters, digits and underscores)")
        return package

    def fingerprint(self) -> str:
        paths = {self.path, REPO_ROOT / "Dockerfile", REPO_ROOT / "requirements-azure.txt",
                 REPO_ROOT / "requirements.txt", REPO_ROOT / "requirements-p2.txt", REPO_ROOT / "pyproject.toml",
                 REPO_ROOT / "scripts/deploy.py", REPO_ROOT / "scripts/run_dev_proof.py",
                 REPO_ROOT / "scripts/run_p2_container_jobs.py", REPO_ROOT / "scripts/configure_strategy_recovery.py",
                 REPO_ROOT / "scripts/run_foundry_evals.py", REPO_ROOT / "evals/run_model_comparison.py"}
        for directory in (self.path.parent, REPO_ROOT / "src"):
            paths.update(path for path in directory.rglob("*")
                         if path.is_file() and path.suffix in (".py", ".json", ".jsonl", ".csv", ".md", ".html"))
        for kind, targets in self.local_checks:
            if kind == "pytest":
                paths.update(REPO_ROOT / target for target in targets)
        paths.update(self.runtime_inputs())
        paths.update(path for path in (REPO_ROOT / "evals").rglob("*.py"))
        for name in ("scripts/acceptance.py",):
            if (REPO_ROOT / name).is_file():
                paths.add(REPO_ROOT / name)
        return self.hash_inputs(paths)

    def runtime_inputs(self) -> set[Path]:
        paths = {REPO_ROOT / name for name in (
            "Dockerfile", ".dockerignore", "azure.yaml", "requirements.txt", "requirements-azure.txt",
            "requirements-p2.txt", "pyproject.toml",
        ) if (REPO_ROOT / name).is_file()}
        for directory in (REPO_ROOT / "src", REPO_ROOT / "scripts", REPO_ROOT / "infra", self.path.parent):
            paths.update(path for path in directory.rglob("*")
                         if path.is_file() and path.suffix in (
                             ".py", ".json", ".jsonl", ".csv", ".md", ".html", ".bicep", ".xml", ".yaml", ".js", ".css")
                         and "__pycache__" not in path.parts)
        evaluation_only = {
            REPO_ROOT / "scripts/acceptance.py", REPO_ROOT / "scripts/run_foundry_evals.py",
            REPO_ROOT / "scripts/run_dev_proof.py",
            REPO_ROOT / "src/governedgraph/evaluation.py", REPO_ROOT / "src/governedgraph/foundry_evaluation.py",
            self.path.parent / "data/synthetic/decision_agent_fixtures.json",
        }
        operator_only = {REPO_ROOT / "scripts" / name for name in (
            "deploy.py", "run_p2_container_jobs.py", "teardown.py", "benchmark_voice.py", "lift_dev_freeze.py",
        )}
        return {path for path in paths if path not in evaluation_only | operator_only and not (
            path.is_relative_to(self.path.parent)
            and (path.relative_to(self.path.parent).parts[0] in ("evaluation", "tests", "docs")
                 or path.is_relative_to(self.path.parent / "data/personas")))}

    def runtime_fingerprint(self) -> str:
        return self.hash_inputs(self.runtime_inputs())

    @staticmethod
    def hash_inputs(paths: set[Path]) -> str:
        digest = hashlib.sha256()
        for path in sorted(paths):
            if not path.resolve().is_relative_to(REPO_ROOT):
                raise DeployError("sample verification inputs cannot point outside the repository")
            digest.update(str(path.relative_to(REPO_ROOT)).encode() + b"\0" + path.read_bytes() + b"\0")
        return digest.hexdigest()


class Runner:
    """The only place that starts processes, so tests can replace it with a simulated Azure."""

    def __init__(self, echo=print):
        self.echo = echo

    def run(self, args, *, stream=False, check=True, env=None, quiet=False) -> str:
        args = [str(a) for a in args]
        if not quiet:
            self.echo("$ " + " ".join(a if len(a) < 90 else a[:80] + "..." for a in args))
        merged = dict(os.environ)
        for key, value in (env or {}).items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        if stream:
            tail = deque(maxlen=200)
            with subprocess.Popen(args, cwd=REPO_ROOT, env=merged, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, bufsize=1) as proc:
                assert proc.stdout is not None
                for line in proc.stdout:
                    self.echo(line.rstrip("\n"))
                    tail.append(line)
                proc.wait()
            out = err = "".join(tail)
        else:
            proc = subprocess.run(args, cwd=REPO_ROOT, env=merged, capture_output=True, text=True)
            out, err = proc.stdout, proc.stderr
        if check and proc.returncode:
            raise CommandError(args, proc.returncode, err)
        return out


class Prompter:
    def __init__(self, *, interactive: bool, ask=input, echo=print):
        self.interactive, self._ask, self.echo = interactive, ask, echo

    def question(self, text: str, default: str = "", validate=None, hint: str = "") -> str:
        while True:
            if not self.interactive:
                value = default
            else:
                suffix = f" [{default}]" if default else ""
                value = (self._ask(f"{text}{suffix}: ").strip() or default)
            problem = None if not validate else validate(value)
            if not problem:
                return value
            if not self.interactive:
                raise DeployError(f"{text}: {problem}")
            self.echo(f"  {problem}" + (f" ({hint})" if hint else ""))

    def confirm(self, text: str, default: bool = False) -> bool:
        if not self.interactive:
            return default
        answer = self._ask(f"{text} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
        return default if not answer else answer in ("y", "yes")

    def choose(self, text: str, options: list[tuple[str, str]], default: str) -> str:
        if not self.interactive:
            return default
        for index, (value, label) in enumerate(options, 1):
            self.echo(f"  {index}. {label}{'  (current)' if value == default else ''}")
        while True:
            raw = self._ask(f"{text} [{default}]: ").strip()
            if not raw:
                return default
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return options[int(raw) - 1][0]
            if any(raw == value for value, _ in options):
                return raw
            self.echo("  Enter a number or a value from the list.")


def validate_guid(value: str) -> str | None:
    try:
        UUID(value)
        return None
    except ValueError:
        return "must be a GUID"


def validate_rg(value: str) -> str | None:
    if not RG_NAME.fullmatch(value) or len(value) > 90:
        return "1-90 characters: letters, digits, '_', '-', '(', ')' and '.', not ending in '.'"
    return None


def validate_email(value: str) -> str | None:
    return None if EMAIL.fullmatch(value) else "enter a valid email address"


def validate_location(value: str) -> str | None:
    return None if LOCATION.fullmatch(value) else "use the short region name, for example swedencentral"


def extract_public_jwk(text: str) -> dict:
    """Find the public RSA JWK in job logs; refuse anything carrying private key parameters."""
    for candidate in (text, text.replace('\\"', '"')):
        for match in re.finditer(r"\{[^{}]*\"kty\"\s*:\s*\"RSA\"[^{}]*\}", candidate):
            try:
                jwk = json.loads(match.group(0))
            except ValueError:
                continue
            if set(jwk) - PUBLIC_JWK_FIELDS or not jwk.get("n") or not jwk.get("e"):
                raise UnsafeKey("the issuer output is not a pure public RSA key; refusing to store it")
            return jwk
    raise DeployError("no public key found in the issuer job output")


def quota_findings(usage: list[dict]) -> list[str]:
    """Problems with model quota; an empty list means every needed model has room (or could not be checked)."""
    seen = {row.get("name", {}).get("value", ""): row for row in usage}
    problems = []
    for name, needed in QUOTA_NEEDS.items():
        row = seen.get(name)
        if row is None:
            row = seen.get(name.replace(".gpt-4.1", ".gpt4.1"))
        if row is None:
            problems.append(f"could not confirm quota for {name.split('.', 2)[-1]} ({name.split('.')[1]})")
        elif row.get("limit", 0) - row.get("currentValue", 0) < needed:
            problems.append(f"{name.split('.', 2)[-1]} ({name.split('.')[1]}) has {row['limit'] - row['currentValue']:g} "
                            f"of the {needed} units needed")
    return problems


def placeholder_jwk() -> dict:
    """A throwaway public key so the first provision can run; the private half is discarded immediately."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jwt.algorithms import RSAAlgorithm

    jwk = json.loads(RSAAlgorithm.to_jwk(rsa.generate_private_key(65537, 2048).public_key()))
    jwk.update(alg="RS256", use="sig")
    return jwk


def http_request(url: str, headers: dict | None = None, timeout: int = 30) -> tuple[int, str]:
    """A small stdlib GET so acceptance checks need no extra dependency; tests replace it."""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read(200_000).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as error:
        raise DeployError(f"could not reach {url}: {error}") from error


CAPTURE_QUERY = ('AppDependencies | where TimeGenerated > ago(3h) '
                 '| where Name == "invoke_agent governedgraph.synthetic_conversation" | summarize spans=count()')

STEP_TITLES = {
    "sample": "Run the selected sample's tests, evaluation gates and offline rehearsal",
    "preflight": "Check tools, sign-in, permissions, providers, quota and the resource group",
    "environment": f"Create the azd environment `{ENV_NAME}` and set its inputs",
    "provision-1": "Provision the infrastructure (first pass; slowest step, API Management)",
    "private-link": "Approve the Search private link on the content storage account",
    "entra": "Register the Strategy API, contact-centre and admin console Entra apps (and the console's access groups)",
    "recovery-access": "Configure delegated recovery access for this deployment's operator",
    "agents": "Create and pin the Foundry agents (prompts come from the domain pack)",
    "deploy": "Build and deploy the five services (remote builds, no local Docker)",
    "provision-2": "Provision again: real images, client IDs, agent pins, issuer and publisher jobs, content publication",
    "member-key": "Bootstrap the real member-token public key from the dev issuer job",
    "provision-3": "Final provision with the real key (and the presenter, if chosen)",
    "learning": "Build the P2 image, publish synthetic learning inputs, run the learning and fairness proofs, enable the schedule",
    "verify": "Run every in-network acceptance proof declared by the sample",
    "evaluations": "Create fresh Foundry evaluations of pinned agents on synthetic cases (paid model and judge calls)",
    "admin-console": "Check the admin console: page, health, required sign-in and closed routes",
}
ACCEPTANCE_STEPS = ("verify", "evaluations")
STEPS = [step for step in STEP_TITLES if step not in ACCEPTANCE_STEPS]


class Deployer:
    def __init__(self, runner: Runner, prompter: Prompter, state: State, *, state_path: Path = STATE_FILE,
                 sleep=time.sleep, echo=print, python: str = sys.executable, check_local: bool = True,
                 ignore_warnings: bool = False, recover_soft_deleted: bool = False,
                 environment_path: Path | None = None):
        self.run, self.prompt, self.state, self.state_path = runner, prompter, state, state_path
        self.sleep, self.echo, self.python, self.check_local = sleep, echo, python, check_local
        self.ignore_warnings = ignore_warnings
        self.recover_soft_deleted = recover_soft_deleted
        self.environment_path = environment_path or REPO_ROOT / ".azure" / ENV_NAME
        self.mode = "resume" if state.answers else "fresh"
        self.answers = Answers(**state.answers) if state.answers else Answers()
        self.resume_learning_cycle = ""
        self.learning_recovery_receipt: dict | None = None

    @property
    def sample(self) -> SampleDefinition:
        return SampleDefinition.load(self.answers.sample_definition)

    # ---- helpers -----------------------------------------------------------------------------------------------
    def say(self, text: str = "") -> None:
        self.echo(text)

    def azd(self, *args, **kw) -> str:
        scoped = args[0] in ("provision", "deploy") or args[:2] in (("env", "get-values"), ("env", "set"))
        flags = ["--environment", ENV_NAME] if scoped else []
        env = {**kw.pop("env", {}), "AZURE_ENV_NAME": ENV_NAME}
        if args[0] in ("provision", "deploy"):
            current = self.values()
            parameters = json.loads((REPO_ROOT / "infra" / "main.parameters.json").read_text())["parameters"]
            # Exported settings from an older deployment must not override this azd environment.
            for parameter in parameters.values():
                value = parameter["value"]
                match = re.fullmatch(r"\$\{([A-Z][A-Z0-9_]*)(?:=[^}]*)?\}", value) if isinstance(value, str) else None
                if match:
                    key = match.group(1)
                    env[key] = current.get(key)
            env.update(current)
            a = self.answers
            env.update(AZURE_ENV_NAME=ENV_NAME, AZURE_SUBSCRIPTION_ID=a.subscription_id, AZURE_LOCATION=a.location,
                       AZURE_SEARCH_LOCATION=a.search_location or a.location,
                       GOVERNEDGRAPH_RESOURCE_GROUP_NAME=a.resource_group,
                       GOVERNEDGRAPH_NAMING_SEED=a.naming_seed)
        return self.run.run(["azd", *args, *flags], env=env, **kw)

    def az(self, *args, **kw) -> str:
        tenant_command = args[0] in ("ad", "login", "extension") or args[:2] == ("account", "list")
        scope = (["--subscription", self.answers.subscription_id]
                 if self.answers.subscription_id and not tenant_command
                 and "--subscription" not in args and "-s" not in args else [])
        return self.run.run(["az", *args, *scope, "--only-show-errors"], **kw)

    def values(self) -> dict[str, str]:
        from dotenv import dotenv_values

        text = self.azd("env", "get-values", quiet=True)
        return {k: v for k, v in dotenv_values(stream=StringIO(text)).items() if v}

    def setenv(self, key: str, value: str) -> None:
        self.azd("env", "set", key, value)

    def definition_env(self, values: dict[str, str] | None = None) -> dict[str, str]:
        defaults = {
            "AZURE_AI_AGENT_NAME": WRITERS["counsellor"],
            "AZURE_AI_MODEL_DEPLOYMENT": "gpt-counsellor",
            "AZURE_AI_COMPACT_DEPLOYMENT": "gpt-compact",
            "AZURE_AI_FAIRNESS_AGENT_NAME": "gg-fairness-explainability",
        }
        selected = values or {}
        return {**{key: selected.get(key, default) for key, default in defaults.items()},
                "PYTHONPATH": os.pathsep.join(map(str, (REPO_ROOT / "src", REPO_ROOT / "examples"))),
                "GOVERNEDGRAPH_DATA_DIR": str(self.sample.data_directory), "GOVERNEDGRAPH_MODE": "local"}

    def tool_env(self) -> dict[str, str]:
        values = self.values()
        return {**values, **self.definition_env(values), "GOVERNEDGRAPH_MODE": "azure"}

    def mark(self, step: str) -> None:
        if step not in self.state.completed:
            self.state.completed.append(step)
        self.state.answers = asdict(self.answers)
        self.state.save(self.state_path)

    # ---- questions ---------------------------------------------------------------------------------------------
    def select_mode(self, args) -> None:
        self.mode = args.mode or self.prompt.choose("Deployment action", [
            ("fresh", "Fresh deployment: new names and resource group; old resources remain and may incur costs"),
            ("redeploy", "Redeploy/update existing: reuse saved names and run every step"),
            ("resume", "Resume failed deployment: reuse saved names and skip completed steps"),
        ], self.mode)
        if self.mode != "fresh" and not self.state.answers:
            raise DeployError(f"--mode {self.mode} needs saved deployment state; restore it or choose --mode fresh")
        if self.mode in ("fresh", "redeploy") and (args.only or args.from_step):
            raise DeployError(f"--mode {self.mode} runs the whole deployment; use --mode resume with --only or --from-step")
        if self.mode == "fresh":
            seed = uuid4().hex
            self.state = State(environment_reset_pending=True, new_group_required=True)
            self.answers = Answers(naming_seed=seed, resource_group=f"rg-{ENV_NAME}-{seed[:8]}")
            self.say("Fresh deployment: previous Azure resources will NOT be deleted or reused and may still incur costs.")
        if self.answers.naming_seed and not re.fullmatch(r"[a-f0-9]{32}", self.answers.naming_seed):
            raise DeployError("saved naming seed is invalid; restore the deployment state rather than generating new names")
        if self.state.environment_reset_pending and not self.answers.naming_seed:
            raise DeployError("fresh environment reset is missing its naming seed; restore the saved deployment state")

    def commit_selection(self) -> None:
        if self.mode == "fresh":
            archive = self.state_path.parent / "deploy-history" / self.answers.naming_seed
            archive.mkdir(parents=True, exist_ok=False)
            if self.state_path.exists():
                shutil.copy2(self.state_path, archive / "state.json")
            self.say(f"Previous state snapshot: {archive}. Old azd configuration will be archived before initialization. "
                     "Azure resources are untouched.")
        elif self.mode == "redeploy":
            self.state.completed.clear()
            self.state.channel_proof_execution = ""
            self.state.sample_proof_executions = {}
            self.state.learning_receipts = {}
        self.state.answers = asdict(self.answers)
        self.state.save(self.state_path)

    def _reset_environment(self) -> None:
        if not self.state.environment_reset_pending:
            return
        archive = self.state_path.parent / "deploy-history" / self.answers.naming_seed / "azd-environment"
        if self.environment_path.is_symlink():
            raise DeployError("refusing to archive a symlinked azd environment; review the local configuration")
        if self.environment_path.exists():
            if archive.exists():
                raise DeployError("both the old azd environment and its archive exist; review them before resuming")
            self.environment_path.rename(archive)
            self.say(f"Previous local azd configuration archived at {archive}.")
        self.state.environment_reset_pending = False
        self.state.save(self.state_path)

    def ask(self, args) -> None:
        a, p = self.answers, self.prompt
        stored = bool(self.state.answers) and not args.reconfigure
        if stored:
            for flag, attr in (("subscription", "subscription_id"), ("location", "location"),
                               ("resource_group", "resource_group"), ("on_call_email", "on_call_email")):
                given = getattr(args, flag)
                if given and given != getattr(a, attr):
                    raise DeployError(f"--{flag.replace('_', '-')} differs from the saved answer; pass --reconfigure "
                                      "to change it (changing the resource group after provisioning creates a second deployment)")
            preferred = a.preferred_search_location or a.search_location or a.location
            if args.search_location and args.search_location != preferred:
                raise DeployError("--search-location differs from the saved preference; pass --reconfigure to change it")
            self.say("Using the saved answers (pass --reconfigure to change them).")
            return
        if "provision-1" in self.state.completed:
            raise DeployError("the infrastructure is already provisioned; --reconfigure cannot change the saved answers safely")
        self.say("A few questions first. Defaults are in brackets.\n")
        if not args.subscription and p.interactive:
            rows = json.loads(self.az("account", "list", "--query",
                                      "[?state=='Enabled'].{id:id,name:name,isDefault:isDefault}", "-o", "json", quiet=True))
            options = [(r["id"], f"{r['name']} ({r['id']})") for r in rows]
            default = next((r["id"] for r in rows if r["isDefault"]), options[0][0] if options else "")
            a.subscription_id = p.choose("Subscription", options, default)
        else:
            a.subscription_id = args.subscription or os.environ.get("AZURE_SUBSCRIPTION_ID", "") or self._current_subscription()
        if validate_guid(a.subscription_id):
            raise DeployError("a valid subscription ID is required (--subscription)")
        a.location = p.question("Azure region", args.location or a.location, validate_location)
        a.search_location = args.search_location or a.location
        if a.search_location not in SEARCH_REGIONS:
            raise DeployError(f"Search region must be one of the approved EU regions: {', '.join(SEARCH_REGIONS)}")
        a.preferred_search_location = a.search_location
        a.resource_group = p.question("Resource group name (created by the deployment)",
                                      args.resource_group or a.resource_group, validate_rg)
        a.on_call_email = p.question("On-call email for alerts (Azure may send a verification email; confirm it if you get one)",
                                     args.on_call_email or "", validate_email)
        # The presenter, synthetic-conversation capture and the console's conversation view ship together; opt out
        # of all three with --no-voice-demo.
        a.voice_demo = args.voice_demo if args.voice_demo is not None else True
        a.member_token_issuer = args.member_token_issuer or a.member_token_issuer
        if args.reconfigure:
            self.state.search_capacity_regions.clear()
        self.state.answers = asdict(a)
        if self.mode != "fresh":
            self.state.save(self.state_path)

    def _current_subscription(self) -> str:
        try:
            return self.az("account", "show", "--query", "id", "-o", "tsv", quiet=True).strip()
        except DeployError:
            return ""

    def summary(self) -> None:
        a = self.answers
        self.say("\nDeployment plan")
        self.say(f"  action           {self.mode}")
        self.say(f"  environment      {ENV_NAME}  (fixed: dev stand-ins are enabled only here)")
        self.say(f"  subscription     {a.subscription_id}")
        self.say(f"  region           {a.location}" + (f"  (Search: {a.search_location})"
                                                        if a.search_location and a.search_location != a.location else ""))
        self.say(f"  resource group   {a.resource_group}")
        self.say(f"  naming seed      {a.naming_seed or '(legacy names preserved)'}")
        self.say(f"  soft-delete      {'explicit recovery enabled' if self.recover_soft_deleted else 'stop; recovery is opt-in'}")
        self.say(f"  on-call email    {a.on_call_email}")
        self.say(f"  voice presenter  {'yes' if a.voice_demo else 'no'}"
                 + ("  (with synthetic conversation capture and the console's conversation view)" if a.voice_demo else ""))
        self.say("  admin console    yes: Entra sign-in; reader and operator groups are created and you join both"
                 " (opt out: GOVERNEDGRAPH_ADMIN_ACCESS_CONTROL=false)")
        self.say("  Search fallback  automatic on confirmed capacity failure; supported EU regions only, no existing-service move")
        self.say("  duration         typically 45-90 minutes, mostly the two provisioning passes")
        self.say("  cost             not free tier: API Management StandardV2, AI Search, two registries, always-on Container Apps,")
        self.say("                   an Azure ML workspace and model deployments. Remove with `python -m scripts.teardown`.\n")

    # ---- steps -------------------------------------------------------------------------------------------------
    def _check_local_tools(self) -> None:
        for tool in ("az", "azd"):
            if not shutil.which(tool):
                raise DeployError(f"`{tool}` is not installed or not on PATH")
        if sys.version_info < (3, 11):
            raise DeployError("Python 3.11 or newer is required")
        for module in ("cryptography", "jwt", "dotenv", "azure.ai.projects", "agent_framework"):
            try:
                __import__(module)
            except ImportError:
                raise DeployError(f"Python package for `{module}` is missing; run: pip install -r requirements-azure.txt") from None

    def step_preflight(self) -> None:
        a = self.answers
        if self.check_local:
            self._check_local_tools()
        try:
            self.az("account", "show", "-o", "none", quiet=True)
        except DeployError:
            if not self.prompt.interactive:
                raise DeployError("not signed in to Azure; run `az login`") from None
            self.az("login", stream=True)
        try:
            self.azd("auth", "login", "--check-status", quiet=True)
        except DeployError:
            if not self.prompt.interactive:
                raise DeployError("not signed in to azd; run `azd auth login`") from None
            self.azd("auth", "login", stream=True)
        self.az("account", "set", "--subscription", a.subscription_id)
        self.state.operator_id = self.az("ad", "signed-in-user", "show", "--query", "id", "-o", "tsv", quiet=True).strip()
        if validate_guid(self.state.operator_id):
            raise DeployError("could not determine your Entra object ID (az ad signed-in-user show)")
        self._check_permissions()
        self._check_providers()
        self._check_quota()
        self._check_group()

    def step_sample(self) -> None:
        if self.check_local:
            self._check_local_tools()
        sample = self.sample
        env = {**self.definition_env(),
               "GOVERNEDGRAPH_RUN_DIR": str(self.state_path.parent / "sample-verification" / sample.name)}
        for kind, targets in sample.local_checks:
            command = ([self.python, "-m", "pytest", "-q", "-o", "addopts=", *targets]
                       if kind == "pytest" else [self.python, "-m", targets[0]])
            try:
                self.run.run(command, env=env, stream=True)
            except CommandError as error:
                raise DeployError(f"sample {sample.name} {kind} acceptance failed; no provisioning was attempted") from error
        self.require_release_approval()
        self.state.sample_fingerprint = sample.fingerprint()

    def _warn_and_continue(self, problems: list[str], what: str) -> None:
        if not problems:
            return
        self.say(f"\nWarning: {what}")
        for problem in problems:
            self.say(f"  - {problem}")
        if not self.prompt.confirm("Continue anyway?", default=self.ignore_warnings):
            raise DeployError(f"stopped on: {what}")

    def _check_permissions(self) -> None:
        scope = f"/subscriptions/{self.answers.subscription_id}"
        try:
            roles = set(json.loads(self.az("role", "assignment", "list", "--assignee", self.state.operator_id, "--scope", scope,
                                           "--include-inherited", "--query", "[].roleDefinitionName", "-o", "json", quiet=True)))
        except DeployError:
            roles = set()
        if "Owner" in roles or {"Contributor", "User Access Administrator"} <= roles or \
                {"Contributor", "Role Based Access Control Administrator"} <= roles:
            return
        self._warn_and_continue(["the deployment creates role assignments, so it needs Owner (or Contributor plus User Access "
                                 "Administrator) on the subscription; this could not be confirmed for you"], "Azure permissions")

    def _check_providers(self) -> None:
        pending = []
        for namespace in PROVIDERS:
            state = self.az("provider", "show", "-n", namespace, "--query", "registrationState", "-o", "tsv", quiet=True).strip()
            if state != "Registered":
                self.az("provider", "register", "-n", namespace)
                pending.append(namespace)
        for namespace in pending:
            for _ in range(60):
                state = self.az("provider", "show", "-n", namespace, "--query", "registrationState", "-o", "tsv", quiet=True).strip()
                if state == "Registered":
                    break
                self.sleep(10)
            else:
                raise DeployError(f"resource provider {namespace} did not finish registering; retry in a few minutes")

    def _check_quota(self) -> None:
        try:
            usage = json.loads(self.az("cognitiveservices", "usage", "list", "-l", self.answers.location, "-o", "json", quiet=True))
        except DeployError:
            usage = []
        self._warn_and_continue(quota_findings(usage), f"model quota in {self.answers.location}")

    def _check_group(self) -> None:
        a = self.answers
        if self.az("group", "exists", "-n", a.resource_group, quiet=True).strip().lower() != "true":
            self.state.new_group_required = False
            return
        existing = self.az("group", "show", "-n", a.resource_group, "--query", "location", "-o", "tsv", quiet=True).strip()
        if existing != a.location:
            raise DeployError(f"resource group {a.resource_group} already exists in {existing}, not {a.location}; pick another name")
        if self.state.new_group_required:
            raise DeployError("a fresh deployment requires a new resource group; the selected group already exists. "
                              "Choose --mode fresh with another group, or restore the previous deployment's archived state.")
        if self.mode == "redeploy":
            return
        self._warn_and_continue([f"resource group {a.resource_group} already exists; the deployment will add to it"],
                                "existing resource group")

    def step_environment(self) -> None:
        a = self.answers
        self._reset_environment()
        names = {row["Name"] for row in json.loads(self.azd("env", "list", "--output", "json", quiet=True) or "[]")}
        if ENV_NAME in names:
            self.azd("env", "select", ENV_NAME)
        else:
            self.azd("env", "new", ENV_NAME, "--subscription", a.subscription_id, "--location", a.location, "--no-prompt")
        current = self.values()
        if current.get("AZURE_SUBSCRIPTION_ID") not in (None, "", a.subscription_id):
            raise DeployError(f"azd environment {ENV_NAME} belongs to another subscription; remove it with `azd env delete`")
        for key, expected in (("AZURE_ENV_NAME", ENV_NAME), ("AZURE_LOCATION", a.location),
                              ("AZURE_RESOURCE_GROUP", a.resource_group),
                              ("GOVERNEDGRAPH_RESOURCE_GROUP_NAME", a.resource_group),
                              ("GOVERNEDGRAPH_NAMING_SEED", a.naming_seed)):
            if current.get(key) not in (None, "", expected):
                raise DeployError(f"azd environment {ENV_NAME}: {key} differs from the saved deployment scope; "
                                  "no resources changed. Review the environment rather than redirecting a resumed deployment.")
        for key, value in {
            "AZURE_LOCATION": a.location, "AZURE_SEARCH_LOCATION": a.search_location,
            "GOVERNEDGRAPH_RESOURCE_GROUP_NAME": a.resource_group, "ON_CALL_EMAIL": a.on_call_email,
            "GOVERNEDGRAPH_NAMING_SEED": a.naming_seed, "GOVERNEDGRAPH_SAMPLE": self.sample.package,
            "GOVERNEDGRAPH_ENABLE_DEV_MEMBER_ISSUER": "true", "MEMBER_TOKEN_ISSUER": a.member_token_issuer,
            "GOVERNEDGRAPH_SYNTHETIC_CONTACT_CENTRE": "true",
            "GOVERNEDGRAPH_TRACE_SYNTHETIC_CONVERSATIONS": "true" if a.voice_demo else "false",
            "GOVERNEDGRAPH_ADMIN_TRACE_LOOKUP": "true" if a.voice_demo else "false",
            "STRATEGY_RECOVERY_OPERATOR_ID": self.state.operator_id, "VOICE_DEMO_OPERATOR_ID": self.state.operator_id,
        }.items():
            if current.get(key) != value:
                self.setenv(key, value)
        if "member-key" not in self.state.completed and not current.get("MEMBER_TOKEN_PUBLIC_JWK"):
            self.setenv("MEMBER_TOKEN_PUBLIC_JWK", json.dumps(placeholder_jwk(), separators=(",", ":")))

    def provision(self) -> None:
        a = self.answers
        recovered: set[str] = set()
        pull_retries = 0
        # State is authoritative if a previous run stopped between saving a fallback and updating azd.
        selected = a.search_location or a.location
        if self.values().get("AZURE_SEARCH_LOCATION") != selected:
            self.setenv("AZURE_SEARCH_LOCATION", selected)
        while True:
            started = datetime.now(timezone.utc)
            try:
                self.azd("provision", "--no-prompt", stream=True)
                return
            except CommandError as error:
                try:
                    retry_pull = self.fixture_pull_pending(error.stderr)
                except (CommandError, ValueError, TypeError, KeyError) as diagnostic_error:
                    raise DeployError(f"Could not verify the fixture image-pull prerequisites: {diagnostic_error}. "
                                      f"No permissions changed. Original failure:\n{error.stderr}") from diagnostic_error
                if retry_pull:
                    if pull_retries == 5:
                        raise DeployError("Fixture managed-identity image pull still fails after five retries. "
                                          "The image and AcrPull assignment exist, but authentication is not working. "
                                          f"Inspect the registry and identity; no permissions changed.\n{error.stderr}") from error
                    pull_retries += 1
                    self.say(f"  Fixture image and AcrPull grant verified; waiting 60 seconds for managed-identity "
                             f"authorization to propagate (retry {pull_retries}/5). No permissions changed.")
                    self.sleep(60)
                    continue
                if "FlagMustBeSetForRestore" in error.stderr or (
                        "ConflictError" in error.stderr and "deleted state" in error.stderr):
                    if not self.recover_soft_deleted:
                        raise DeployError("Soft-deleted resources block this deployment. No recovery or purge attempted. "
                                          "To preserve and recover this deployment, use --mode resume --recover-soft-deleted. "
                                          "For new resources instead, choose --mode fresh with a new resource group; "
                                          f"old resources remain untouched.\n{error.stderr}") from error
                    try:
                        self._recover_soft_deleted(error.stderr, recovered)
                    except (CommandError, ValueError, TypeError, KeyError) as recovery_error:
                        raise DeployError(f"Soft-deleted resource recovery could not finish: {recovery_error}. "
                                          "No resource was purged. Review permissions and re-run.") from recovery_error
                    continue
                if "InsufficientResourcesAvailable" not in error.stderr:
                    raise DeployError(f"azd provision failed ({error.code}): {error.stderr}. "
                                      "Read the error above, fix it and re-run this script; it resumes here.") from error
                other_failures = re.findall(r"Failed:\s*([^:\n]+):", error.stderr)
                details = error.stderr.partition("Deployment Error Details:")[2] or error.stderr
                codes = set(re.findall(r"(?m)^\s*([A-Z][A-Za-z0-9]+):", details))
                blockers = codes - {"ERROR", "Suggestion", "DeploymentFailed", "ResourceDeploymentFailure",
                                    "InvalidTemplateDeployment", "InsufficientResourcesAvailable"}
                if blockers or any(kind.strip() != "Search service" for kind in other_failures):
                    raise DeployError(f"azd provision has failures besides Search capacity; no region changed:\n{error.stderr}")
                try:
                    self._fallback_search(started)
                except (CommandError, ValueError, TypeError, KeyError) as diagnostic_error:
                    raise DeployError(f"Search capacity fallback could not finish: {diagnostic_error}. "
                                      "Inspect the deployment and re-run; no resource was deleted.") from diagnostic_error

    def fixture_pull_pending(self, text: str) -> bool:
        if "InvalidParameterValueInContainerTemplate" not in text or "template.containers.publisher.image" not in text:
            return False
        codes = set(re.findall(r"(?m)^\s*([A-Z][A-Za-z0-9]+):", text))
        if codes - {"ERROR", "Suggestion", "DeploymentFailed", "ResourceDeploymentFailure",
                    "InvalidParameterValueInContainerTemplate"}:
            return False
        targets = set(re.findall(r"unable to pull image using Managed identity (\S+) for registry ([a-z0-9]+\.azurecr\.io)", text))
        if len(targets) != 1:
            return False
        identity, server = targets.pop()
        scope = f"/subscriptions/{self.answers.subscription_id}/resourceGroups/{self.answers.resource_group}/providers/"
        if not identity.lower().startswith((scope + "Microsoft.ManagedIdentity/userAssignedIdentities/id-gg-dev-fixtures-").lower()):
            return False
        values = self.values()
        registry_id = values.get("AZURE_ML_REGISTRY_ID", "")
        registry = server.split(".", 1)[0]
        image = values.get("DEV_FIXTURES_IMAGE", "")
        if (registry_id.lower() != (scope + f"Microsoft.ContainerRegistry/registries/{registry}").lower()
                or values.get("DEPLOY_DEV_FIXTURES") != "true"
                or not re.fullmatch(re.escape(server) + r"/governedgraph/dev-fixtures@sha256:[0-9a-f]{64}", image)
                or f'"{image}"' not in text):
            return False
        config = json.loads(self.az("acr", "show", "--name", registry, "--resource-group", self.answers.resource_group,
                                    "-o", "json", quiet=True))
        if (config.get("roleAssignmentMode") != "LegacyRegistryPermissions"
                or config.get("policies", {}).get("azureAdAuthenticationAsArmPolicy", {}).get("status") != "enabled"):
            raise DeployError("The fixture registry is not configured for AcrPull and ARM-audience authentication; "
                              f"no retry or permission change attempted.\n{text}")
        principal = self.az("identity", "show", "--name", identity.rsplit("/", 1)[1],
                            "--resource-group", self.answers.resource_group,
                            "--query", "principalId", "-o", "tsv", quiet=True).strip()
        assignments = json.loads(self.az("role", "assignment", "list", "--scope", registry_id, "-o", "json", quiet=True))
        if not principal or not any(
                row.get("principalId", "").lower() == principal.lower()
                and row.get("scope", "").lower() == registry_id.lower()
                and row.get("roleDefinitionId", "").lower().endswith("/7f951dda-4ed3-4680-a7ca-43fe172d538d")
                and not row.get("condition") for row in assignments):
            raise DeployError(f"The fixture identity has no unconditional registry-scoped AcrPull grant; "
                              f"no retry or permission change attempted.\n{text}")
        digest = self.az("acr", "repository", "show", "--name", registry, "--image", image.split("/", 1)[1],
                         "--query", "digest", "-o", "tsv", quiet=True).strip()
        if digest != image.rsplit("@", 1)[1]:
            raise DeployError(f"The exact fixture image digest could not be verified; no retry attempted.\n{text}")
        return True

    def _recover_soft_deleted(self, text: str, recovered: set[str]) -> None:
        a = self.answers
        scope = f"/subscriptions/{a.subscription_id}/resourceGroups/{a.resource_group}/providers/"
        account_ids = set(re.findall(
            r"/subscriptions/[^/'\"\s]+/resourceGroups/[^/'\"\s]+/providers/Microsoft\.CognitiveServices/accounts/[^/'\"\s]+",
            text))
        vault_names = set(re.findall(r"Failed:\s*Key Vault:\s*([a-z0-9-]+)", text))
        if ("FlagMustBeSetForRestore" in text and not account_ids) or (
                "ConflictError" in text and "deleted state" in text and not vault_names):
            raise DeployError(f"Soft-deleted resources block provisioning, but their exact targets could not be confirmed. "
                              f"No recovery or purge attempted:\n{text}")
        targets = []
        if vault_names:
            deleted = json.loads(self.az("keyvault", "list-deleted", "-o", "json", quiet=True))
            for name in sorted(vault_names):
                if not name.startswith("kv-gg-"):
                    raise DeployError(f"Soft-deleted vault {name} is outside the expected deployment targets; "
                                      "no recovery or purge attempted.")
                resource_id = scope + f"Microsoft.KeyVault/vaults/{name}"
                match = next((v["properties"] for v in deleted
                              if v["properties"]["vaultId"].lower() == resource_id.lower()), None)
                targets.append((resource_id, match, a.location,
                                ["keyvault", "recover", "--name", name, "--resource-group", a.resource_group,
                                 "--location", a.location, "-o", "none"]))
        if account_ids:
            deleted = json.loads(self.az("cognitiveservices", "account", "list-deleted", "-o", "json", quiet=True))
            for resource_id in sorted(account_ids):
                name = resource_id.rsplit("/", 1)[-1]
                if not resource_id.lower().startswith((scope + "Microsoft.CognitiveServices/accounts/").lower()) or \
                        not name.startswith(("cs-gg-", "ais-gg-")):
                    raise DeployError(f"Soft-deleted account is outside the expected deployment targets: {resource_id}. "
                                      "No recovery or purge attempted.")
                match = next((v for v in deleted if v["id"].lower() == resource_id.lower()), None)
                location = "francecentral" if name.startswith("cs-gg-") else a.location
                targets.append((resource_id, match, location,
                                ["cognitiveservices", "account", "recover", "--name", name,
                                 "--resource-group", a.resource_group, "--location", location, "-o", "none"]))
        problems = []
        for resource_id, metadata, location, _command in targets:
            if resource_id.lower() in recovered:
                problems.append(f"{resource_id}: still blocked after recovery; refusing repeated recovery")
            elif metadata is None:
                problems.append(f"{resource_id}: not found in the deleted-resource inventory")
            elif metadata.get("location") != location or (metadata.get("tags") or {}).get("azd-env-name") != ENV_NAME:
                problems.append(f"{resource_id}: deleted-resource region or environment ownership does not match {ENV_NAME}")
        if problems:
            raise DeployError("Soft-deleted resource blockers require review; no purge attempted:\n" + "\n".join(problems))
        for resource_id, _metadata, _location, command in targets:
            self.say(f"Recovering soft-deleted resource {resource_id}; preserving its data, keys and region. No purge.")
            self.az(*command)
            actual = self.az("resource", "show", "--ids", resource_id, "--query", "id", "-o", "tsv", quiet=True).strip()
            if actual.lower() != resource_id.lower():
                raise DeployError(f"Recovery could not be verified for {resource_id}; stop and review the resource")
            recovered.add(resource_id.lower())

    def _fallback_search(self, started: datetime) -> None:
        a = self.answers
        scope = ["--subscription", a.subscription_id, "--resource-group", a.resource_group]
        rows = json.loads(self.az("deployment", "operation", "group", "list", *scope, "--name", "data",
                                 "-o", "json", quiet=True))
        target_id = ""
        prefix = f"/subscriptions/{a.subscription_id}/resourceGroups/{a.resource_group}/providers/Microsoft.Search/searchServices/"
        for row in rows:
            props = row["properties"]
            target = props.get("targetResource") or {}
            if props.get("provisioningState") != "Failed" or \
                    target.get("resourceType", "").lower() != "microsoft.search/searchservices":
                continue
            if not props.get("timestamp") or datetime.fromisoformat(props["timestamp"].replace("Z", "+00:00")) < started:
                continue
            status = props.get("statusMessage") or {}
            if isinstance(status, str):
                status = json.loads(status)
            if not isinstance(status, dict):
                raise DeployError("Search deployment returned an invalid error object; no region changed")
            if not self._has_capacity_error(status):
                continue
            resource_id = target.get("id", "")
            if resource_id.lower().startswith(prefix.lower()) and "/" not in resource_id[len(prefix):]:
                target_id = resource_id
                break
        if not target_id:
            raise DeployError("azd provision failed with insufficient capacity, but a current Search creation failure "
                              "could not be confirmed; no region changed. Inspect the deployment error and re-run.")
        services = json.loads(self.az("search", "service", "list", *scope, "-o", "json", quiet=True))
        if any(service["id"].lower() == target_id.lower() for service in services):
            raise DeployError("Search capacity failed, but the Search service already exists. Automatic relocation or deletion "
                              "is unsafe; no region changed. Review the existing service and re-run.")
        current = a.search_location or a.location
        if current not in self.state.search_capacity_regions:
            self.state.search_capacity_regions.append(current)
        self.state.save(self.state_path)
        locations = json.loads(self.az("provider", "show", "--namespace", "Microsoft.Search",
                                       "--subscription", a.subscription_id, "--query",
                                       "resourceTypes[?resourceType=='searchServices'].locations | [0]",
                                       "-o", "json", quiet=True))
        if not isinstance(locations, list):
            raise DeployError("could not confirm supported Search regions; no region changed")
        supported = {location.replace(" ", "").lower() for location in locations}
        candidate = next((region for region in SEARCH_REGIONS
                          if region in supported and region not in self.state.search_capacity_regions), None)
        if candidate is None:
            raise DeployError("Search capacity alternatives exhausted: no untried supported EU region remains. "
                              "No existing resource was moved or deleted; retry after capacity becomes available "
                              "(--reconfigure before the first successful provision resets the attempts).")
        self.say(f"Search creation has insufficient capacity in {current}; retrying in {candidate}. "
                 "Other resource and model regions are unchanged. Regional support is not a capacity guarantee.")
        if not a.preferred_search_location:
            a.preferred_search_location = current
        a.search_location = candidate
        self.state.answers = asdict(a)
        self.state.save(self.state_path)
        self.setenv("AZURE_SEARCH_LOCATION", candidate)

    @staticmethod
    def _has_capacity_error(status: dict) -> bool:
        if status.get("code") == "InsufficientResourcesAvailable":
            return True
        return any(Deployer._has_capacity_error(child) for child in
                   [status.get("error"), *status.get("details", [])] if isinstance(child, dict))

    def step_provision_1(self) -> None:
        self.provision()
        missing = [k for k in ("AZURE_RESOURCE_GROUP", "STRATEGY_RUNTIME_PRINCIPAL_ID", "CONTENT_STORAGE_RESOURCE_ID")
                   if k not in self.values()]
        if missing:
            raise DeployError(f"provisioning finished but the environment lacks {', '.join(missing)}")

    def step_private_link(self) -> None:
        storage = self.values()["CONTENT_STORAGE_RESOURCE_ID"]
        for _ in range(30):
            rows = json.loads(self.az("network", "private-endpoint-connection", "list", "--id", storage, "-o", "json", quiet=True))
            mine = [r for r in rows if LINK_MARKER in r["properties"]["privateLinkServiceConnectionState"].get("description", "")]
            if mine:  # only the Search indexer's request is ever approved
                for row in mine:
                    if row["properties"]["privateLinkServiceConnectionState"]["status"] == "Pending":
                        self.az("network", "private-endpoint-connection", "approve", "--id", row["id"],
                                "--description", f"{LINK_MARKER} approved by the deployment script")
                return
            self.sleep(10)
        raise DeployError("the Search private link request did not appear on the content storage account; approve it in the "
                          "portal (Storage > Networking > Private endpoint connections) and re-run")

    def step_entra(self) -> None:
        v = self.values()
        env = self.tool_env()
        out = self.run.run([self.python, "-m", "governedgraph.strategy_service.provision_auth", "--environment", ENV_NAME,
                            "--pipeline", v["STRATEGY_PUBLISHER_PRINCIPAL_ID"], "--fairness", v["STRATEGY_FAIRNESS_PRINCIPAL_ID"],
                            "--runtime", v["STRATEGY_RUNTIME_PRINCIPAL_ID"],
                            *(["--admin", v["STRATEGY_ADMIN_PRINCIPAL_ID"]] if v.get("STRATEGY_ADMIN_PRINCIPAL_ID") else []),
                            "--naming-seed", self.answers.naming_seed, "--apply"], env=env)
        match = re.search(r"^STRATEGY_API_CLIENT_ID=([0-9a-fA-F-]{36})\s*$", out, re.M)
        if not match:
            raise DeployError("the Strategy API registration did not report its client ID")
        self.setenv("STRATEGY_API_CLIENT_ID", match.group(1))
        out = self.run.run([self.python, "scripts/configure_contact_centre.py", "--environment", ENV_NAME,
                            "--runtime-principal", v["STRATEGY_RUNTIME_PRINCIPAL_ID"],
                            "--naming-seed", self.answers.naming_seed, "--apply"], env=env)
        try:
            registration = json.loads(out)
            client_id, audience = registration["client_id"], registration["audience"]
        except (ValueError, KeyError) as error:
            raise DeployError("the contact-centre registration did not report its client ID and audience") from error
        for key, value in (("CONTACT_CENTRE_CLIENT_ID", client_id), ("CONTACT_CENTRE_AUDIENCE", audience)):
            self.setenv(key, value)
        self.register_admin_console(v, env)

    def register_admin_console(self, v: dict, env: dict) -> None:
        """Same step as the other registrations: groups, the deploying user and the sign-in app, recorded in the azd env."""
        if (not self.admin_enabled() or v.get("GOVERNEDGRAPH_ADMIN_ACCESS_CONTROL", "true").strip().lower() == "false"
                or not v.get("APIM_GATEWAY_URL")):
            self.say("  Admin console sign-in skipped; the console stays open with an access-control banner.")
            return
        out = self.run.run([self.python, "-m", "scripts.configure_admin_console", "--environment", ENV_NAME,
                            "--origin", v["APIM_GATEWAY_URL"].rstrip("/"), "--member", self.state.operator_id,
                            "--naming-seed", self.answers.naming_seed, "--apply"], env=env)
        try:
            registration = json.loads(out)
            settings = {"GOVERNEDGRAPH_ADMIN_CLIENT_ID": registration["client_id"],
                        "GOVERNEDGRAPH_ADMIN_READER_GROUP_ID": registration["reader_group_id"],
                        "GOVERNEDGRAPH_ADMIN_OPERATOR_GROUP_ID": registration["operator_group_id"]}
        except (ValueError, KeyError) as error:
            raise DeployError("the admin console registration did not report its client and group IDs") from error
        for key, value in settings.items():
            self.setenv(key, value)

    def step_recovery_access(self) -> None:
        values = self.values()
        api, human = values.get("STRATEGY_API_CLIENT_ID", ""), values.get("STRATEGY_RECOVERY_OPERATOR_ID", "")
        if validate_guid(api) or validate_guid(human) or human != self.state.operator_id:
            raise DeployError("recovery access requires this deployment's Strategy API and saved operator identity")
        try:
            output = self.run.run([self.python, "scripts/configure_strategy_recovery.py",
                                   "--api-client-id", api, "--human-id", human, "--apply"], env=self.tool_env())
        except CommandError as error:
            raise DeployError("delegated recovery access setup failed; an Entra administrator or authorized application "
                              "owner must configure this deployment's operator access. Keep assignment required enabled. "
                              "Retry with --mode resume --only recovery-access after resolving directory permissions.") from error
        try:
            result = json.loads(output)
        except ValueError as error:
            raise DeployError("delegated recovery access setup did not report a valid result") from error
        if (not isinstance(result, dict) or result.get("environment") != ENV_NAME
                or result.get("api_client_id") != api or result.get("human_id") != human
                or result.get("scope") != "Strategy.Recover" or result.get("apply") is not True
                or result.get("new_application_roles") is not False or result.get("credentials") != "none"):
            raise DeployError("delegated recovery access setup did not confirm the configured API, operator and scope")

    def release_approval(self) -> dict:
        """The sample's human Tier-2 release decision; shape errors fail closed."""
        def unique(pairs):
            fields = {}
            for key, value in pairs:
                if key in fields:
                    raise DeployError("duplicate field in the sample release approval")
                fields[key] = value
            return fields

        try:
            raw = json.loads(self.sample.release_approval.read_text(), object_pairs_hook=unique)
        except (OSError, json.JSONDecodeError) as error:
            raise DeployError("could not read the sample release approval") from error
        writers = raw.get("writers") if isinstance(raw, dict) else None
        if (not isinstance(raw, dict) or set(raw) != {"tier", "approved", "decision_ref", "evidence_ref", "writers"}
                or raw["tier"] != "tier2" or not isinstance(raw["approved"], bool)
                or not isinstance(raw["decision_ref"], str) or not isinstance(raw["evidence_ref"], str)
                or not isinstance(writers, dict) or set(writers) != set(WRITERS.values())
                or any(not isinstance(w, dict) or set(w) != {"definition_sha256"}
                       or not isinstance(w["definition_sha256"], str)
                       or not re.fullmatch(r"[0-9a-f]{64}", w["definition_sha256"]) for w in writers.values())):
            raise DeployError("the sample release approval is not a closed Tier-2 record for both writer definitions")
        if raw["approved"] and (not re.fullmatch(r"(?:GB|HR)-[A-Za-z0-9][A-Za-z0-9._-]{0,127}", raw["decision_ref"])
                                or not raw["evidence_ref"].strip() or len(raw["evidence_ref"]) > 256):
            raise DeployError("an approved release needs a GB- board or HR- human release decision reference and evidence")
        return raw

    @staticmethod
    def parse_agent_result(output: str) -> dict:
        for line in reversed(output.strip().splitlines()):
            try:
                result = json.loads(line)
            except ValueError:
                continue
            if isinstance(result, dict):
                return result
        return {}

    def current_writer_digests(self) -> dict[str, str]:
        env = self.definition_env()
        digests = {}
        for role, name in WRITERS.items():
            result = self.parse_agent_result(self.run.run(
                [self.python, "scripts/create_agents.py", "--role", role], env=env, quiet=True))
            if result.get("agent_name") != name:
                raise DeployError(f"the {role} definition plan did not report the expected agent name {name}; "
                                  "check the agent configuration and plan output")
            digest = result.get("definition_sha256")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise DeployError(f"the {role} definition plan did not report a valid SHA-256 digest")
            digests[name] = digest
        return digests

    def release_status(self, digests: dict[str, str] | None = None) -> tuple[str, str]:
        approval = self.release_approval()
        digests = digests or self.current_writer_digests()
        if not approval["approved"]:
            return "unapproved", "the sample release has no human Tier-2 approval"
        if any(approval["writers"][name]["definition_sha256"] != digest for name, digest in digests.items()):
            return "stale", "the approval does not cover the current writer definitions"
        return "approved", f"approved by {approval['decision_ref']}"

    def require_release_approval(self) -> None:
        digests = self.current_writer_digests()
        status, detail = self.release_status(digests)
        if status == "approved":
            self.say(f"Release approval: {detail}.")
            return
        guidance = (f"Generated answers cannot be released: {detail}. Nothing is provisioned yet. The approving human "
                    f"records `approved: true`, a `GB-` board or `HR-` human release decision reference, evidence and these exact digests "
                    f"in {self.sample.release_approval.relative_to(REPO_ROOT)}: "
                    + "; ".join(f"{name} {digest}" for name, digest in digests.items()) + ". Then re-run this command.")
        if "provision-1" in self.state.completed:
            self.say(f"Warning: {guidance} Existing deployments keep generated answers suspended.")
            return
        raise DeployError(guidance)

    def apply_release_approval(self, results: dict[str, dict]) -> None:
        digests = {WRITERS[role]: str(results[role].get("definition_sha256", "")) for role in WRITERS}
        status, detail = self.release_status(digests)
        current = self.values().get(APPROVAL_ENV, "")
        if status != "approved":
            if current:
                self.setenv(APPROVAL_ENV, "")
            self.say(f"Generated answers stay suspended: {detail}.")
            return
        approval, values = self.release_approval(), self.values()
        record = {"tier": "tier2", "approved": True, "decision_ref": approval["decision_ref"],
                  "evidence_ref": approval["evidence_ref"], "project_endpoint": values.get("AZURE_AI_PROJECT_ENDPOINT", ""),
                  "writers": {name: {"version": str(results[role]["agent_version"]), "definition_sha256": digests[name]}
                              for role, name in WRITERS.items()}}
        path = self.state_path.parent / "writer-approval.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, sort_keys=True))
        try:
            path.chmod(0o600)
            self.run.run([self.python, "-m", "scripts.writer_approval", "--file", str(path),
                          "--environment", ENV_NAME, "--apply"], env=self.tool_env())
        except CommandError as error:
            self.setenv(APPROVAL_ENV, "")
            raise DeployError("the live writer definitions could not be verified against the release approval; "
                              f"generated answers remain suspended: {error.stderr[-300:]}") from error
        finally:
            path.unlink(missing_ok=True)
        self.say(f"Writer release approved by {approval['decision_ref']} for these immutable definitions.")

    def step_agents(self) -> None:
        env = self.tool_env()
        results: dict[str, dict] = {}
        for role, variable in AGENT_ROLES.items():
            for attempt in range(1, 9):
                try:
                    out = self.run.run([self.python, "scripts/create_agents.py", "--role", role, "--apply"], env=env)
                    break
                except CommandError as error:  # a fresh role assignment can take minutes to propagate
                    if attempt == 8:
                        raise DeployError(f"could not create the {role} agent: {error.stderr[-300:]}") from error
                    self.say(f"  waiting for permissions to propagate (attempt {attempt}/8)...")
                    self.sleep(60)
            result = self.parse_agent_result(out)
            version = str(result["agent_version"]) if "agent_version" in result else None
            if not version:
                raise DeployError(f"the {role} agent did not report a version")
            results[role] = result
            self.setenv(variable, version)
        self.apply_release_approval(results)

    def missing_service_targets(self) -> list[str]:
        rows = json.loads(self.az("resource", "list", "--resource-group", self.answers.resource_group,
                                  "--resource-type", "Microsoft.App/containerApps", "--query", "[].tags",
                                  "-o", "json", quiet=True))
        counts = dict.fromkeys(SERVICES, 0)
        for tags in rows:
            service = tags.get("azd-service-name")
            if service not in counts:
                continue
            if tags.get("azd-env-name", ENV_NAME) != ENV_NAME:
                raise DeployError(f"the {service} deploy target is tagged for another environment; "
                                  "review its ownership before deploying")
            counts[service] += 1
        duplicates = [service for service, count in counts.items() if count > 1]
        if duplicates:
            raise DeployError(f"multiple Container Apps have the same azd-service-name: {', '.join(duplicates)}; "
                              "resolve ambiguous targets before deploying")
        return [service for service, count in counts.items()
                if count == 0 and (service != "admin" or self.admin_enabled())]

    def admin_enabled(self) -> bool:
        return self.values().get("GOVERNEDGRAPH_DEPLOY_ADMIN_CONSOLE", "true").strip().lower() != "false"

    def step_deploy(self) -> None:
        missing = self.missing_service_targets()
        if missing:
            self.say(f"  Missing azd service targets: {', '.join(missing)}; reconciling infrastructure before remote builds.")
            self.provision()
            missing = self.missing_service_targets()
            if missing:
                raise DeployError(f"provisioning finished but azd service targets are still missing: {', '.join(missing)}; "
                                  "check the Container Apps' azd-service-name tags before resuming")
        try:
            services = [service for service in SERVICES if service != "admin" or self.admin_enabled()]
            if len(services) == len(SERVICES):
                self.azd("deploy", "--no-prompt", stream=True)
            else:
                for service in services:
                    self.azd("deploy", service, "--no-prompt", stream=True)
        except CommandError as error:
            raise DeployError("azd deploy failed; read the build error above, fix it and re-run this script") from error
        if "SERVICE_ORCHESTRATOR_IMAGE_NAME" not in self.values():
            raise DeployError("deployment finished but no orchestrator image was recorded")

    def step_provision_2(self) -> None:
        self.provision()

    def step_member_key(self) -> None:
        group = self.values()["AZURE_RESOURCE_GROUP"]
        base = ["-g", group, "-n", ISSUER_JOB]
        execution = self.az("containerapp", "job", "start", *base, "--query", "name", "-o", "tsv").strip()
        for _ in range(60):
            status = self.az("containerapp", "job", "execution", "show", *base, "--job-execution-name", execution,
                             "--query", "properties.status", "-o", "tsv", quiet=True).strip()
            if status == "Succeeded":
                break
            if status in ("Failed", "Stopped", "Degraded"):
                raise DeployError(f"the issuer job ended with status {status}; inspect its logs ({execution})")
            self.sleep(10)
        else:
            raise DeployError("the issuer job did not finish in time")
        jwk = None
        for _ in range(6):
            try:
                logs = self.az("containerapp", "job", "logs", "show", *base, "--execution", execution,
                               "--container", "issuer", "--tail", "50", quiet=True)
                jwk = extract_public_jwk(logs)
                break
            except UnsafeKey:
                raise
            except DeployError:
                pass  # logs can lag the execution; try again
            self.sleep(20)
        if jwk is None:
            if not self.prompt.interactive:
                raise DeployError("could not read the public key from the issuer job logs; read it from Log Analytics "
                                  "(ContainerAppConsoleLogs) and set MEMBER_TOKEN_PUBLIC_JWK, then re-run")
            self.say("The issuer logs are not available yet. Paste the single-line public JWK the job printed "
                     "(Log Analytics > ContainerAppConsoleLogs):")
            jwk = extract_public_jwk(input("JWK: "))
        self.setenv("MEMBER_TOKEN_PUBLIC_JWK", json.dumps(jwk, separators=(",", ":")))

    def step_provision_3(self) -> None:
        had_client = bool(self.values().get("VOICE_DEMO_CLIENT_ID"))
        if self.answers.voice_demo:
            self.setenv("DEPLOY_VOICE_DEMO", "true")
        self.provision()
        if self.answers.voice_demo and not had_client:
            if not self.values().get("VOICE_DEMO_CLIENT_ID"):
                raise DeployError("the presenter registration did not report a client ID")
            self.provision()  # the new client ID is applied by one more pass

    def build_image(self, registry: str, repository: str, target: str) -> str:
        """Remote-build one Docker target and return its immutable digest reference."""
        if not re.fullmatch(r"[a-z0-9]{5,50}", registry) or not re.fullmatch(r"[a-z0-9][a-z0-9._/-]*", repository):
            raise DeployError("invalid registry or repository for an acceptance image")
        tag = self.state.sample_fingerprint[:16] or "unversioned"
        image = f"{repository}:{tag}"
        self.az("acr", "build", "--registry", registry, "--file", "Dockerfile", "--build-arg", f"SAMPLE={self.sample.package}",
                "--target", target, "--image", image, ".", stream=True)
        digest = self.az("acr", "repository", "show", "--name", registry, "--image", image,
                         "--query", "digest", "-o", "tsv", quiet=True).strip()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise DeployError(f"the {target} build did not return an immutable image digest")
        return f"{registry}.azurecr.io/{repository}@{digest}"

    def run_job(self, job: str, what: str, group: str) -> str:
        execution = self.az("containerapp", "job", "start", "-g", group, "-n", job, "--query", "name", "-o", "tsv").strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", execution):
            raise DeployError(f"{what} did not report a valid execution name")
        status = ""
        for _ in range(60):
            status = self.az("containerapp", "job", "execution", "show", "-g", group, "-n", job,
                             "--job-execution-name", execution, "--query", "properties.status",
                             "-o", "tsv", quiet=True).strip()
            if status not in ("Running", "Processing", "Pending", ""):
                break
            self.sleep(10)
        if status != "Succeeded":
            raise DeployError(f"{what} did not succeed (status: {status or 'unknown'}, execution {execution})")
        return execution

    def p2_source_digest(self, registry: str, image: str) -> str:
        """Verify source/manifests before retaining an original image, never promoting its rebuild."""
        # ACR embeds --cmd in task YAML: newlines and mapping separators must not escape the command.
        code = (
            "import hashlib,json,os,platform,subprocess,sys;from pathlib import Path;"
            "h=hashlib.sha256();"
            "roots=[Path('/app'),Path(sys.executable).resolve(),Path('/etc/os-release')];"
            "paths=sorted(p for r in roots for p in (r.rglob('*') if r.is_dir() else [r]) "
            "if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc');"
            "[h.update(str(p).encode()+b'\\0'+p.read_bytes()+b'\\0') for p in paths];"
            "metadata=dict(python=sys.version,platform=platform.machine(),uid=os.geteuid(),"
            "packages=subprocess.check_output(['dpkg-query','-W'],text=True),"
            "env=dict((k,os.environ.get(k)) for k in ('GOVERNEDGRAPH_SAMPLE','GOVERNEDGRAPH_MODE',"
            "'PYTHONPATH','PYTHONDONTWRITEBYTECODE','PYTHONUNBUFFERED')));"
            "h.update(json.dumps(metadata,sort_keys=True).encode());"
            "print('GG_P2_SOURCE_SHA256='+h.hexdigest(),flush=True)"
        )
        output = self.az("acr", "run", "--registry", registry, "--cmd",
                         shlex.join([image, "python", "-c", code]), "/dev/null", stream=True)
        digests = re.findall(r"\bGG_P2_SOURCE_SHA256=([0-9a-f]{64})\b", output)
        if len(digests) != 1:
            raise LearningCycleStopped("registry task did not return one conclusive P2 source digest")
        return digests[0]

    def learning_complete(self) -> bool:
        wanted = set(self.sample.learning_checks) | {"p2-image", "schedule"}
        return (set(self.state.learning_receipts) == wanted
                and all(isinstance(value, str) and value for value in self.state.learning_receipts.values()))

    def acknowledge_learning_recovery(self, receipt: dict) -> None:
        from governedgraph.strategy_service.recovery import RecoveryRequest, latest_successful_audit
        from governedgraph.strategy_service.state import StateUnavailable
        from scripts.run_p2_container_jobs import execution_history

        if (not isinstance(receipt, dict)
                or set(receipt) != {"audit_execution", "decision_ref", "frozen", "recovery_id"}
                or receipt["frozen"] is not False
                or not isinstance(receipt["recovery_id"], str)
                or not re.fullmatch(r"recovery-[0-9a-f]{32}", receipt["recovery_id"])):
            raise LearningCycleStopped("an exact successful human recovery receipt is required")
        try:
            RecoveryRequest.from_dict({"expected_audit_execution": receipt["audit_execution"],
                                       "decision_ref": receipt["decision_ref"]})
        except (ValueError, TypeError) as error:
            raise LearningCycleStopped("human recovery decision or audit reference is invalid") from error
        attempt = self.state.learning_attempt
        if (not attempt or any(attempt.get(key) != value for key, value in {
                "subscription_id": self.answers.subscription_id, "resource_group": self.answers.resource_group,
                "sample": self.sample.package}.items())):
            raise LearningCycleStopped("human recovery must match the pending deployment scope")
        if any(record["receipt"]["recovery_id"] == receipt["recovery_id"] for record in self.state.learning_recoveries):
            raise LearningCycleStopped("the human recovery receipt has already been acknowledged")
        rows = []
        for role in ("pipeline", "fairness"):
            job = {"id": (f"/subscriptions/{self.answers.subscription_id}/resourceGroups/{self.answers.resource_group}"
                          f"/providers/Microsoft.App/jobs/job-gg-p2-{role}")}
            history = execution_history(job, reader=lambda *args: json.loads(self.az(*args, "-o", "json", quiet=True)))
            for row in history:
                for container in row["properties"]["template"]["containers"]:
                    env = {item["name"]: item.get("value") for item in container.get("env", [])}
                    if env.get("GOVERNEDGRAPH_P2_CYCLE_ID") == attempt["cycle_id"]:
                        if (container["image"] != attempt["image"]
                                or (env.get("GOVERNEDGRAPH_P2_STAGE") == "audit") != (role == "fairness")):
                            raise LearningCycleStopped("recovery execution does not match the pending isolated cycle")
                        rows.append((row, env))
        watchdogs = [row for row, env in rows if env.get("GOVERNEDGRAPH_P2_STAGE") == "watchdog"]
        audits = [row for row, env in rows if env.get("GOVERNEDGRAPH_P2_STAGE") == "audit"]
        if (len(watchdogs) != 1 or watchdogs[0]["properties"]["status"] not in {"Failed", "Stopped"}
                or len(audits) != 1 or audits[0]["name"] != receipt["audit_execution"]
                or audits[0]["properties"]["status"] != "Succeeded"
                or any(row["properties"]["status"] not in {"Succeeded", "Failed", "Stopped"} for row, _ in rows)):
            raise LearningCycleStopped("recovery requires a stopped failed cycle and its exact successful audit")
        try:
            if latest_successful_audit(audits, attempt["image"]) != receipt["audit_execution"]:
                raise LearningCycleStopped("human recovery audit does not match the pending cycle")
        except StateUnavailable as error:
            raise LearningCycleStopped("human recovery audit lacks approved isolated execution evidence") from error
        values = self.values()
        remote = json.loads(self.expect_status("recovered strategy state", values["STRATEGY_ADMIN_URL"] + "/snapshots", 200))
        if remote.get("frozen") is not False:
            raise LearningCycleStopped("Strategy Service is still protected; human recovery is required")
        self.state.learning_recoveries.append({"attempt": dict(attempt), "receipt": dict(receipt)})
        self.state.learning_attempt = {}
        self.state.save(self.state_path)
        self.say("Human recovery verified; failed cycle archived, not certified. A new governed cycle is required.")

    def step_learning(self) -> None:
        sample, v = self.sample, self.values()
        group = v["AZURE_RESOURCE_GROUP"]
        self.az("account", "set", "--subscription", self.answers.subscription_id)
        registry_id = v.get("AZURE_ML_REGISTRY_ID", "")
        registry = registry_id.rsplit("/", 1)[-1]
        if "/providers/Microsoft.ContainerRegistry/registries/" not in registry_id:
            raise DeployError("provisioning did not report the dedicated Azure ML registry")
        attempt = self.state.learning_attempt
        cycle = self.resume_learning_cycle or attempt.get("cycle_id", "")
        scope = {"subscription_id": self.answers.subscription_id, "resource_group": group,
                 "sample": sample.package}
        if attempt and (any(attempt.get(key) != value for key, value in scope.items())
                        or attempt.get("cycle_id") != cycle):
            raise LearningCycleStopped("the pending learning cycle does not match the saved deployment scope")
        previous_image = attempt.get("image") or v.get("P2_CONTAINER_IMAGE", "")
        if cycle and not re.fullmatch(re.escape(registry) + r"\.azurecr\.io/governedgraph/p2@sha256:[0-9a-f]{64}",
                                      previous_image):
            raise LearningCycleStopped("recovering learning requires its original immutable dedicated-registry image")
        if cycle and not attempt:
            self.state.learning_attempt = {**scope, "cycle_id": cycle, "image": previous_image}
            self.state.save(self.state_path)
        p2_image = (previous_image if cycle and attempt.get("runtime_fingerprint") == self.state.runtime_fingerprint
                    else self.build_image(registry, "governedgraph/p2", "p2"))
        if cycle and p2_image != previous_image:
            self.say("  image digests differ; verifying application code, dependency manifests and execution defaults "
                     "before retaining the original immutable P2 image...")
            if self.p2_source_digest(registry, previous_image) != self.p2_source_digest(registry, p2_image):
                raise LearningCycleStopped("current P2 source differs from the interrupted cycle; "
                                           "the recovered cycle cannot certify changed workload code")
            p2_image = previous_image
        receipts: dict[str, str] = {}
        receipts["p2-image"] = p2_image
        self.setenv("P2_CONTAINER_IMAGE", p2_image)
        self.setenv("P2_SCHEDULE_ENABLED", "false")
        if sample.learning_fixtures and not cycle:
            self.setenv("DEV_FIXTURES_IMAGE", self.build_image(registry, "governedgraph/dev-fixtures", "dev-fixtures"))
            self.setenv("DEPLOY_DEV_FIXTURES", "true")
        if v.get("FAIRNESS_MANAGED_IDENTITY_CLIENT_ID") != v.get("FAIRNESS_CLIENT_ID"):
            self.setenv("FAIRNESS_MANAGED_IDENTITY_CLIENT_ID", v["FAIRNESS_CLIENT_ID"])
        storage = v.get("AZURE_ML_STORAGE_ID", "").rsplit("/", 1)[-1]
        if not re.fullmatch(r"[a-z0-9]{3,24}", storage):
            raise DeployError("provisioning did not report the dedicated Azure ML storage account")
        names = json.loads(self.az("storage", "container-rm", "list", "--storage-account", storage, "--resource-group", group,
                                   "--query", "[].name", "-o", "json", quiet=True))
        containers = sorted(name for name in names if re.fullmatch(r"azureml(?:-blobstore-[0-9a-f-]{36})?", name))
        if not containers:
            raise DeployError("the Azure ML workspace has not created its artifact containers on the dedicated storage account")
        wanted = json.dumps(containers, separators=(",", ":"))
        grants_change = v.get("P2_ML_ARTIFACT_CONTAINERS") != wanted
        if grants_change:
            self.setenv("P2_ML_ARTIFACT_CONTAINERS", wanted)
        self.provision()
        if grants_change:
            self.say("  waiting for the new container-scoped role assignments to propagate before the first cycle...")
            self.sleep(300)
        if sample.learning_fixtures and not cycle:
            self.run_job(FIXTURES_JOB, "synthetic learning input publication", group)
        if not cycle:
            cycle = "cycle-" + token_hex(8)
        self.state.learning_attempt = {**scope, "cycle_id": cycle, "image": p2_image,
                                       "runtime_fingerprint": self.state.runtime_fingerprint}
        self.state.save(self.state_path)
        command = [self.python, "scripts/run_p2_container_jobs.py", "--environment", ENV_NAME, "--image", p2_image]
        command += ["--resume-cycle" if self.resume_learning_cycle or attempt else "--cycle-id", cycle]
        if "fairness-agent" in sample.learning_checks:
            command.append("--fairness-agent-proof")
        try:
            output = self.run.run(command, env=self.tool_env(), stream=True)
        except CommandError as error:
            if error.code == 3:
                raise LearningCycleStopped("P2 execution status is unavailable; this is not evidence that a stage failed or "
                                  "the Strategy Service froze. The original jobs/watchdog may still be running. "
                                  "Inspect and resume the existing cycle, not a new one; do not lift any freeze. "
                                  f"Last output: {error.stderr[-300:]}") from error
            raise LearningCycleStopped("learning coordination stopped. Verify the actual stage and watchdog status before retrying. "
                              "A confirmed failed P2 stage freezes the Strategy Service by design; any confirmed freeze "
                              "must be recovered only through the human process in the runbook. "
                              f"Last output: {error.stderr[-300:]}") from error
        events = []
        for line in output.splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
        completed_cycle = next((e.get("cycle_id") for e in events
                                if isinstance(e, dict) and e.get("pipeline") == "Completed"), "")
        if completed_cycle != cycle:
            raise DeployError("the P2 cycle did not report completion for the pending cycle")
        receipts["p2-cycle"] = cycle
        if "fairness-agent" in sample.learning_checks:
            proof = next((e.get("execution") for e in events
                          if isinstance(e, dict) and e.get("fairness_agent") == "Succeeded"), "")
            if not isinstance(proof, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", proof):
                raise DeployError("the fairness-agent proof did not report success")
            receipts["fairness-agent"] = proof
        # The recurring cycle starts only after the manual acceptance cycle and agent proof have succeeded.
        self.setenv("P2_SCHEDULE_ENABLED", "true")
        self.provision()
        receipts["schedule"] = "enabled"
        self.state.learning_receipts = receipts
        self.state.learning_attempt = {}

    def step_verify(self, on_execution: Callable[[str, str], None] | None = None) -> None:
        v = self.values()
        self.run.run([self.python, "-m", "scripts.azd_env", "show", "--environment", ENV_NAME], stream=True)
        proofs = {}
        for mode in self.sample.deployed_checks:
            args = [self.python, "-m", "scripts.run_dev_proof", mode, "--environment", ENV_NAME]
            if mode == "voice":
                endpoint = v.get("AZURE_CONTAINER_REGISTRY_ENDPOINT", "")
                if not re.fullmatch(r"[a-z0-9]+\.azurecr\.io", endpoint):
                    raise DeployError("sample voice acceptance needs this deployment's registry endpoint")
                image = self.build_image(endpoint.split(".", 1)[0], "sample-voice-proof", "dev-voice")
                args += ["--image", image]
            execution = self.run.run(args, env=self.tool_env()).strip()
            if on_execution is not None and re.fullmatch(r"[a-z0-9][a-z0-9-]*", execution):
                on_execution(mode, execution)
            self.verify_execution(mode, execution, v)
            proofs[mode] = execution
        self.state.sample_proof_executions = proofs
        self.state.channel_proof_execution = proofs["channel"]

    def step_evaluations(self, output: Path) -> None:
        self.run.run([self.python, "-m", "scripts.run_foundry_evals", "--environment", ENV_NAME, "--output", str(output)],
                     env={**self.tool_env(), "GOVERNEDGRAPH_MODE": "local"}, stream=True)

    def expect_status(self, what: str, url: str, expected: int, headers: dict | None = None) -> str:
        """Fetch with a few retries: the gateway can lag a fresh revision by a minute."""
        status, body = 0, ""
        for attempt in range(6):
            status, body = http_request(url, headers)
            if status == expected:
                return body
            if attempt < 5:
                self.sleep(10)
        raise DeployError(f"admin console check failed: {what} returned HTTP {status}, expected {expected} ({url})")

    def step_admin_console(self) -> None:
        v = self.values()
        if not self.admin_enabled():
            self.say("  Admin console not deployed; nothing to check.")
            return
        gateway = v.get("APIM_GATEWAY_URL", "").rstrip("/")
        if not gateway:
            raise DeployError("the gateway URL is missing from the azd environment; cannot check the admin console")
        self.expect_status("the console health route", f"{gateway}/health/admin/healthz", 200)
        page = self.expect_status("the console page", f"{gateway}/admin/", 200)
        session = re.search(r'name="admin-session" content="([^"]+)"', page)
        if not session:
            raise DeployError("admin console check failed: the page did not issue a session capability")
        for route in ("docs", "openapi.json", "api/actions-list"):
            self.expect_status(f"the unlisted route /{route}", f"{gateway}/admin/{route}", 404)
        signed_in = bool(v.get("GOVERNEDGRAPH_ADMIN_CLIENT_ID")) and (
            v.get("GOVERNEDGRAPH_ADMIN_ACCESS_CONTROL", "true").strip().lower() != "false")
        headers = {"X-Admin-Session": session[1]}
        self.expect_status("evidence without a sign-in", f"{gateway}/admin/api/overview",
                           401 if signed_in else 200, headers)
        mode = "sign-in required" if signed_in else "open, read-only"
        self.say(f"  Admin console: page, health and closed routes verified; evidence access is {mode}.")

    def step_capture(self, since: datetime | None = None) -> None:
        if self.answers.voice_demo and self.admin_enabled():
            self.verify_conversation_capture(self.values(), since=since)
        else:
            self.say("  Conversation capture skipped: the voice presenter or admin console was not chosen.")

    def verify_conversation_capture(self, v: dict[str, str], *, since: datetime | None = None) -> None:
        """The channel proof runs synthetic journeys; their text must reach the workspace the console reads."""
        group = v["AZURE_RESOURCE_GROUP"]
        workspace = v.get("LOG_ANALYTICS_WORKSPACE_ID", "").strip()
        if not workspace:
            workspace = self.az("monitor", "log-analytics", "workspace", "list", "-g", group, "--query",
                                "[0].customerId", "-o", "tsv", quiet=True).strip()
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", workspace):
            raise DeployError("could not determine the Log Analytics workspace to check conversation capture")
        try:
            self.az("extension", "show", "--name", "log-analytics", "-o", "none", quiet=True)
        except DeployError:
            self.az("extension", "add", "--name", "log-analytics", "--yes")
        spans = ""
        query = CAPTURE_QUERY if since is None else CAPTURE_QUERY.replace("ago(3h)", f"datetime({since.isoformat()})")
        for attempt in range(15):
            try:
                spans = self.az("monitor", "log-analytics", "query", "-w", workspace, "--analytics-query",
                                query, "--query", "[0].spans", "-o", "tsv", quiet=True).strip()
            except DeployError as error:
                raise DeployError("could not read the Log Analytics workspace to check conversation capture; the "
                                  "deploying user needs read access to it. Re-run this step once that is granted: "
                                  f"{str(error)[-200:]}") from error
            if spans.isdigit() and int(spans) > 0:
                self.say(f"  Conversation capture verified: {spans} synthetic conversation turns reached the workspace.")
                return
            if attempt < 14:
                self.say(f"  Waiting for log ingestion (attempt {attempt + 1}/15)...")
                self.sleep(60)
        raise DeployError("no synthetic conversations reached the workspace within 15 minutes. Check that the "
                          "orchestrator has GOVERNEDGRAPH_TRACE_SYNTHETIC_CONVERSATIONS=true and re-run this step")

    def verify_execution(self, mode: str, execution: str, v: dict[str, str]) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", execution):
            raise DeployError(f"the {mode} proof did not report a valid execution name")
        base = ["-g", v["AZURE_RESOURCE_GROUP"], "-n", ISSUER_JOB]
        status = ""
        for _ in range(60):
            status = self.az("containerapp", "job", "execution", "show", *base, "--job-execution-name", execution,
                             "--query", "properties.status", "-o", "tsv", quiet=True).strip()
            if status not in ("Running", "Processing", "Pending", ""):
                break
            self.sleep(10)
        self.say(f"\n{mode.capitalize()} proof execution {execution}: {status}")
        if status != "Succeeded":
            raise DeployError(f"{mode} proof {execution} did not succeed (status: {status or 'unknown'}). "
                              "Read its output: az containerapp job logs show "
                              f"-g {v['AZURE_RESOURCE_GROUP']} -n {ISSUER_JOB} --execution {execution} "
                              f"--container issuer --subscription {self.answers.subscription_id}")

    # ---- driver ------------------------------------------------------------------------------------------------
    def execute(self, only: str | None = None, from_step: str | None = None, force: bool = False) -> None:
        fingerprint = self.sample.fingerprint()
        runtime = self.sample.runtime_fingerprint()
        changed_runtime = self.state.runtime_fingerprint != runtime
        if self.state.sample_fingerprint != fingerprint:
            self.state.completed = [step for step in self.state.completed if step != "sample"]
        if changed_runtime:
            affected = {"sample", "recovery-access", "agents", "deploy", "provision-2", "provision-3", "learning",
                        "verify", "evaluations", "admin-console"}
            self.state.completed = [step for step in self.state.completed if step not in affected]
            self.state.channel_proof_execution = ""
            self.state.sample_proof_executions = {}
            self.state.learning_receipts = {}
            self.state.runtime_fingerprint = runtime
        mutating = set(STEPS) - {"sample", "preflight", "admin-console"}
        selected = STEPS[STEPS.index(from_step):] if from_step else STEPS
        selected = [only] if only else selected
        pending_mutation = any(step in mutating and (force or only or step not in self.state.completed
                              or step == "learning" and (not self.learning_complete()
                                                         or self.resume_learning_cycle)) for step in selected)
        if changed_runtime or pending_mutation:
            if changed_runtime or self.state.deployment_status == "succeeded" or not self.state.deployment_id:
                self.state.deployment_id = token_hex(16)
            self.state.deployment_status = "pending"
        self.state.save(self.state_path)
        if (only and only != "sample" or from_step and from_step != "sample") and "sample" not in self.state.completed:
            self.say(f"\n== sample: {STEP_TITLES['sample']}")
            self.step_sample()
            self.mark("sample")
        start = STEPS.index(from_step) if from_step else 0
        if "environment" in self.state.completed and only != "preflight":
            self.say("Revalidating the gg-dev environment and saved deployment scope before resuming.")
            self.step_environment()
        if self.learning_recovery_receipt is not None:
            self.acknowledge_learning_recovery(self.learning_recovery_receipt)
            self.learning_recovery_receipt = None
        if self.resume_learning_cycle and not self.state.learning_attempt:
            values = self.values()
            image = values.get("P2_CONTAINER_IMAGE", "")
            registry = values.get("AZURE_ML_REGISTRY_ID", "").rsplit("/", 1)[-1]
            if not registry or not re.fullmatch(
                    re.escape(registry) + r"\.azurecr\.io/governedgraph/p2@sha256:[0-9a-f]{64}", image):
                raise LearningCycleStopped("legacy cycle recovery requires its original immutable P2 image")
            self.state.learning_attempt = {
                "subscription_id": self.answers.subscription_id, "resource_group": self.answers.resource_group,
                "sample": self.sample.package, "cycle_id": self.resume_learning_cycle, "image": image,
            }
            self.state.save(self.state_path)
        for index, step in enumerate(STEPS):
            if only and step != only or index < start:
                continue
            if (step in self.state.completed and not force and not only
                    and (step != "learning" or self.learning_complete() and not self.resume_learning_cycle)):
                self.say(f"[skip] {step}: already done")
                continue
            if step == "learning":
                if step in self.state.completed:
                    self.state.completed.remove(step)
                self.state.learning_receipts = {}
                self.state.save(self.state_path)
            self.say(f"\n== {step}: {STEP_TITLES[step]}")
            getattr(self, "step_" + step.replace("-", "_"))()
            self.mark(step)
        self.finish()

    def finish(self) -> None:
        if not all(step in self.state.completed for step in STEPS) or not self.learning_complete():
            return
        v = self.values()
        self.state.deployment_status = "succeeded"
        self.state.save(self.state_path)
        self.say("\nDeployment complete.")
        for label, key in (("Resource group", "AZURE_RESOURCE_GROUP"), ("Orchestrator", "ORCHESTRATOR_URL"),
                           ("Gateway", "APIM_GATEWAY_URL")):
            self.say(f"  {label:15} {v.get(key, '(not reported)')}")
        if v.get("ADMIN_CONSOLE_URL"):
            self.say(f"  {'Admin console':15} {v['ADMIN_CONSOLE_URL']}")
            if v.get("GOVERNEDGRAPH_ADMIN_CLIENT_ID"):
                self.say(f"  {'':15} sign in as the deploying user; add others to the console's reader and operator groups in Entra")
        if self.answers.voice_demo and v.get("VOICE_DEMO_ORIGIN"):
            self.say(f"  {'Presenter':15} {v['VOICE_DEMO_ORIGIN'].rstrip('/')}/demo/")
        self.say("\nStill manual: if Azure sent a verification email to the on-call address, open it and confirm it.")
        self.say("  Not every region asks for this yet; any alert email you receive also proves delivery. Then follow the runbook:")
        self.say("  examples/pension_wtp/docs/demo-runbook.md   (every command reads its values from the azd environment)")
        self.say("Learning runs on its approved four-hourly schedule. Remove everything with: python -m scripts.teardown")
        self.say("Deployment is not acceptance or approval for real member use.")
        self.say("Run post-deployment acceptance: python -m scripts.acceptance --environment gg-dev")

    def sample_proofs_complete(self) -> bool:
        return (bool(self.state.channel_proof_execution)
                and set(self.state.sample_proof_executions) == set(self.sample.deployed_checks)
                and all(isinstance(execution, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]*", execution)
                        for execution in self.state.sample_proof_executions.values())
                and self.state.sample_proof_executions["channel"] == self.state.channel_proof_execution)


def print_plan(d: Deployer, args) -> None:
    d.summary()
    sample = d.sample
    d.say(f"Sample: {sample.name}; definition: {sample.path.relative_to(REPO_ROOT)}")
    for kind, targets in sample.local_checks:
        d.say(f"  Local acceptance: {kind} {' '.join(targets)}")
    d.say(f"  In-network acceptance: {', '.join(sample.deployed_checks)}")
    d.say(f"  Learning acceptance: {', '.join(sample.learning_checks)}"
          + ("; synthetic dev inputs published" if sample.learning_fixtures else ""))
    try:
        approval = d.release_approval()
        state = f"approved by {approval['decision_ref']}; digests are verified before provisioning" if approval["approved"] \
            else "NOT APPROVED - deployment stops before provisioning until a human approves the release"
        d.say(f"  Release approval: {state}")
    except DeployError as error:
        d.say(f"  Release approval: unavailable - {error}")
    for step in STEPS:
        mark = "done" if step in d.state.completed else "todo"
        note = " (only if you chose the presenter)" if step == "provision-3" and d.answers.voice_demo else ""
        d.say(f"  [{mark}] {step:13} {STEP_TITLES[step]}{note}")
    d.say("\nNothing was run. Execute without --plan to deploy.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--plan", action="store_true", help="show answers and steps; run nothing")
    p.add_argument("--status", action="store_true", help="show saved answers and completed steps")
    p.add_argument("--sample-definition", help="sample-owned deployment.json under examples/; default is the bundled pension sample")
    p.add_argument("--mode", choices=("fresh", "redeploy", "resume"),
                   help="fresh: new naming seed/group; redeploy: rerun with existing names; resume: skip completed steps")
    p.add_argument("--recover-soft-deleted", action="store_true",
                   help="explicitly allow recovery of verified same-deployment soft-deleted resources; never purge")
    p.add_argument("--resume-learning-cycle",
                   help="verify/resume this original P2 cycle and finalize deployment; never replace its watchdog")
    p.add_argument("--learning-recovery-receipt",
                   help="successful human recovery JSON; verify and archive a failed cycle before a new governed cycle")
    p.add_argument("--yes", action="store_true", help="do not ask for the final confirmation")
    p.add_argument("--non-interactive", action="store_true", help="never prompt; every answer must be a flag")
    p.add_argument("--reconfigure", action="store_true", help="ask the questions again (before provisioning only)")
    p.add_argument("--subscription")
    p.add_argument("--location")
    p.add_argument("--search-location", help="preferred EU Search region; confirmed creation-capacity failures try other EU regions")
    p.add_argument("--resource-group")
    p.add_argument("--on-call-email")
    p.add_argument("--member-token-issuer")
    p.add_argument("--voice-demo", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--only", choices=[*STEPS, *ACCEPTANCE_STEPS])
    p.add_argument("--from-step", choices=[*STEPS, *ACCEPTANCE_STEPS])
    p.add_argument("--force", action="store_true", help="re-run steps already recorded as done")
    p.add_argument("--ignore-warnings", action="store_true",
                   help="unattended runs stop on quota/permission/existing-group warnings; this continues past them")
    return p


def main(argv: list[str] | None = None, *, runner: Runner | None = None, prompter: Prompter | None = None,
         state_path: Path = STATE_FILE, sleep=time.sleep, environment_path: Path | None = None) -> int:
    args = build_parser().parse_args(argv)
    interactive = not args.non_interactive and sys.stdin.isatty()
    prompter = prompter or Prompter(interactive=interactive)
    state = State.load(state_path)
    deployer = Deployer(runner or Runner(), prompter, state, state_path=state_path, sleep=sleep,
                        check_local=runner is None, ignore_warnings=args.ignore_warnings,
                        recover_soft_deleted=args.recover_soft_deleted, environment_path=environment_path)
    try:
        if args.learning_recovery_receipt:
            if (args.mode != "resume" or not state.answers or args.force or args.reconfigure or args.only
                    or args.from_step or args.sample_definition or args.resume_learning_cycle):
                raise DeployError("--learning-recovery-receipt requires a full saved-state --mode resume")
            try:
                deployer.learning_recovery_receipt = json.loads(args.learning_recovery_receipt)
            except ValueError as error:
                raise DeployError("--learning-recovery-receipt must be valid recovery JSON") from error
            if not isinstance(deployer.learning_recovery_receipt, dict):
                raise DeployError("--learning-recovery-receipt must be a recovery JSON object")
        if args.resume_learning_cycle:
            if (not re.fullmatch(r"cycle-[0-9a-f]{16}", args.resume_learning_cycle)
                    or args.mode != "resume" or not state.answers or args.force or args.reconfigure
                    or args.only or args.from_step or args.sample_definition):
                raise DeployError("--resume-learning-cycle requires saved state, --mode resume and an exact cycle ID; "
                                  "it cannot be combined with partial, forced or reconfigured deployment")
            deployer.resume_learning_cycle = args.resume_learning_cycle
        if args.sample_definition:
            if state.answers and args.sample_definition != deployer.answers.sample_definition and args.mode != "fresh":
                raise DeployError("an existing deployment cannot switch its sample definition")
            deployer.answers.sample_definition = args.sample_definition
        deployer.sample
        if args.status:
            from scripts.acceptance import current_status
            summary = asdict(state)
            try:
                summary["acceptance_status"] = current_status(deployer)
            except DeployError as error:
                summary["acceptance_status"] = "stale"
                summary["acceptance_error"] = str(error)
            deployer.say(json.dumps(summary, indent=2))
            return 0
        if args.plan:
            deployer.mode = args.mode or deployer.mode
            if deployer.mode != "fresh" and not state.answers:
                raise DeployError(f"--mode {deployer.mode} needs saved deployment state")
            if deployer.mode == "fresh" or not state.answers:
                deployer.state = State()
                deployer.answers = Answers(naming_seed="<new seed generated once on execution>",
                                           sample_definition=args.sample_definition or DEFAULT_SAMPLE_DEFINITION)
                a = deployer.answers
                a.subscription_id = args.subscription or "<chosen interactively>"
                a.location = args.location or a.location
                a.search_location = args.search_location or a.location
                a.resource_group = args.resource_group or f"rg-{ENV_NAME}-<new suffix>"
                a.on_call_email = args.on_call_email or "<asked interactively>"
                a.voice_demo = args.voice_demo is not False
            elif deployer.mode == "redeploy":
                deployer.state.completed = []
            print_plan(deployer, args)
            return 0
        if args.only in ACCEPTANCE_STEPS or args.from_step in ACCEPTANCE_STEPS:
            if args.mode not in (None, "resume") or args.force or args.reconfigure or args.sample_definition:
                raise DeployError("post-deployment checks use `python -m scripts.acceptance`; deployment flags do not apply")
            from scripts.acceptance import execute
            deployer.say("Post-deployment acceptance has moved to `python -m scripts.acceptance`. "
                         "This compatibility command runs a partial diagnostic, not full acceptance.")
            return execute(deployer, only=args.only or args.from_step)
        deployer.select_mode(args)
        if args.sample_definition:
            deployer.answers.sample_definition = args.sample_definition
        deployer.ask(args)
        deployer.summary()
        if not args.yes:
            if not prompter.interactive:
                raise DeployError("not interactive: pass --yes (and every answer as a flag) to run unattended")
            if not prompter.confirm("Start the deployment?", default=False):
                deployer.say("Nothing was changed. Run again when ready.")
                return 0
        deployer.commit_selection()
        deployer.execute(only=args.only, from_step=args.from_step, force=args.force)
        return 0
    except DeployError as error:
        print(f"\nStopped: {error}", file=sys.stderr)
        if isinstance(error, LearningCycleStopped):
            print("Do not start a replacement P2 cycle blindly. Inspect the original execution and watchdog. "
                  "The pending cycle is retained for `python -m scripts.deploy --mode resume`; "
                  "legacy recovery uses `--resume-learning-cycle <original-cycle-id>`. "
                  "An unfinished cycle requires its original active watchdog; a failed watchdog requires human recovery. "
                  "No deployment completion or learning acceptance was recorded.", file=sys.stderr)
        else:
            print("Fix the cause and resume with `python -m scripts.deploy --mode resume`; completed steps are skipped. "
                  "Do not repeat --mode fresh to retry.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
