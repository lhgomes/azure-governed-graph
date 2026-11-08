#!/usr/bin/env python3
"""Scoped operator teardown of a GovernedGraph gg-dev deployment.

    python -m scripts.teardown --plan     # inventory only; nothing is changed
    python -m scripts.teardown            # inventory, typed confirmation, then the clean-up

One run removes everything the deployment left behind and reports progress as it goes:

1. apps/jobs, their environment and managed infrastructure, network consumers, then networking and
   the resource group; a group delete already running is monitored, with failures surfaced;
2. the soft-deleted AI and API Management records that keep model quota and names reserved, purged only
   when they belong to this resource group; Key Vaults are purged only when purge protection allows it;
3. the Entra apps and security groups, found by the exact seed-scoped names the deployment created;
4. the local azd environment and historical deployment scope, archived under .run/deploy-history.

Scope is deliberately narrow: only the saved (or flagged) resource group, which must carry the
`azd-env-name=gg-dev` tag, and only Entra objects named for this deployment's naming seed. Re-running
is safe; each step looks at what is left. The script never grants access, never touches another
environment and never purges a purge-protected vault.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_NAME = "gg-dev"
STATE_FILE = REPO_ROOT / ".run/deploy-state.json"  # Historical scope only; never a delivery ledger.


class DeployError(RuntimeError):
    """An operator cleanup failed without widening its scope."""


class CommandError(DeployError):
    def __init__(self, args, code, stderr=""):
        self.args_, self.code, self.stderr = list(args), code, stderr.strip()
        super().__init__(f"{args[0]} {args[1]} failed ({code}): {self.stderr[-600:]}")


class Runner:
    def run(self, args, *, quiet=False) -> str:
        result = subprocess.run(args, cwd=REPO_ROOT, capture_output=True, text=True)
        if result.returncode:
            raise CommandError(args, result.returncode, result.stderr)
        return result.stdout.strip()


class Prompter:
    def __init__(self, *, interactive: bool, ask=input, echo=print):
        self.interactive, self.ask, self.echo = interactive, ask, echo

    def question(self, text: str, default: str = "") -> str:
        if not self.interactive:
            raise DeployError("typed confirmation requires an interactive terminal or --yes")
        return self.ask(text).strip() or default


def validate_guid(value: str) -> str | None:
    try:
        UUID(value)
    except ValueError:
        return "must be a UUID"
    return None


def validate_rg(value: str) -> str | None:
    return None if re.fullmatch(r"[A-Za-z0-9_().-]{0,89}[A-Za-z0-9_()-]", value) else "invalid name"


def historical_answers(path: Path) -> dict:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    answers = raw.get("answers", {})
    if not isinstance(answers, dict):
        raise DeployError("historical teardown scope has an invalid answers object")
    return answers

ENVIRONMENT_FILE = REPO_ROOT / ".azure" / ENV_NAME / ".env"
AI_ACCOUNT = "microsoft.cognitiveservices/accounts"
APIM_SERVICE = "microsoft.apimanagement/service"
ENVIRONMENT = "microsoft.app/managedenvironments"
DELETE_ORDER = (
    ("container apps and jobs", ("microsoft.app/containerapps", "microsoft.app/jobs")),
    ("Container Apps environments", (ENVIRONMENT,)),
    ("network consumers", (APIM_SERVICE, "microsoft.machinelearningservices/workspaces",
                           "microsoft.search/searchservices", "microsoft.network/privateendpoints")),
    ("virtual networks", ("microsoft.network/virtualnetworks",)),
    ("NAT gateways", ("microsoft.network/natgateways",)),
    ("network security groups and public IPs", ("microsoft.network/networksecuritygroups",
                                              "microsoft.network/publicipaddresses")),
)
TRANSIENT_CODES = frozenset({"RetryableError", "InternalServerError", "ServiceUnavailable",
                             "TooManyRequests", "OperationTimedOut", "AnotherOperationInProgress"})
MAX_DELETE_ATTEMPTS = 3
TYPE_LABELS = {
    APIM_SERVICE: "API Management", AI_ACCOUNT: "AI accounts", "microsoft.app/containerapps": "container apps",
    "microsoft.app/jobs": "jobs", "microsoft.search/searchservices": "AI Search", "microsoft.keyvault/vaults": "key vaults",
    "microsoft.storage/storageaccounts": "storage accounts", "microsoft.machinelearningservices/workspaces": "ML workspaces",
    "microsoft.containerregistry/registries": "registries", "microsoft.network/privateendpoints": "private endpoints",
}


@dataclass
class Target:
    subscription: str
    resource_group: str
    seed: str


@dataclass
class Inventory:
    group: dict | None = None
    resources: list[dict] | None = None
    deleted_ai: list[dict] = field(default_factory=list)
    deleted_apim: list[dict] = field(default_factory=list)
    deleted_vaults: list[dict] = field(default_factory=list)
    apps: list[dict] = field(default_factory=list)
    groups: list[dict] = field(default_factory=list)
    entra_error: str = ""
    managed_groups: list[str] = field(default_factory=list)

    def expected(self, resource_type: str) -> list[str]:
        return sorted(r["name"] for r in self.resources or [] if r["type"].lower() == resource_type)


def entra_names(seed: str) -> dict[str, list[str]]:
    """The display names the deployment scripts register; they must stay in step with configure_*.py."""
    suffix = "-" + UUID(seed).hex if seed else ""
    return {
        "apps": [f"GovernedGraph {ENV_NAME} Voice Demo{suffix}",
                 f"GovernedGraph {ENV_NAME} synthetic contact centre{suffix}",
                 f"gg-strategy-{ENV_NAME}{suffix}",
                 f"GovernedGraph {ENV_NAME} admin console{suffix}"],
        "groups": [f"GovernedGraph {ENV_NAME} admin readers{suffix}",
                   f"GovernedGraph {ENV_NAME} admin operators{suffix}"],
    }


def read_environment(path: Path = ENVIRONMENT_FILE) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip():
                values[key.strip()] = value.strip().strip('"')
    return values


def resolve_target(args, answers: dict, environment: dict[str, str]) -> Target:
    subscription = args.subscription or answers.get("subscription_id") or environment.get("AZURE_SUBSCRIPTION_ID", "")
    group = args.resource_group or answers.get("resource_group") or environment.get("AZURE_RESOURCE_GROUP", "")
    seed = args.seed if args.seed is not None else (answers.get("naming_seed") or environment.get("GOVERNEDGRAPH_NAMING_SEED", ""))
    if not subscription or not group:
        raise DeployError("no saved deployment found; pass --subscription and --resource-group (and --seed if known)")
    for value, check, label in ((subscription, validate_guid, "subscription"), (group, validate_rg, "resource group")):
        problem = check(value)
        if problem:
            raise DeployError(f"{label}: {problem}")
    if seed:
        try:
            UUID(seed)
        except ValueError as error:
            raise DeployError("--seed must be the deployment's naming seed (a UUID)") from error
    return Target(subscription, group, seed)


def label(resource_type: str) -> str:
    return TYPE_LABELS.get(resource_type.lower(), resource_type.rsplit("/", 1)[-1])


def breakdown(resources: list[dict], limit: int = 4) -> str:
    counts = Counter(label(r["type"]) for r in resources)
    shown = ", ".join(f"{name} {count}" for name, count in counts.most_common(limit))
    rest = len(counts) - limit
    return shown + (f", +{rest} more types" if rest > 0 else "")


def in_group(identifier: str, group: str) -> bool:
    return f"/resourcegroups/{group.lower()}/" in identifier.lower()


def command_code(error: CommandError) -> str:
    match = re.search(r"\((\w+)\)", error.stderr)
    if match:
        return match[1]
    match = re.search(r'"code"\s*:\s*"(\w+)"', error.stderr)
    return match[1] if match else ""


def error_detail(value: dict | str) -> str:
    if isinstance(value, str):
        try:
            return error_detail(json.loads(value))
        except json.JSONDecodeError:
            return value
    if not isinstance(value, dict):
        return str(value)
    if "error" in value:
        return error_detail(value["error"])
    text = ": ".join(str(value[k]) for k in ("target", "code") if value.get(k))
    if value.get("message"):
        text += (": " if text else "") + error_detail(value["message"])
    details = [error_detail(d) for d in value.get("details", [])]
    return "; ".join([text, *details]).strip("; ")


class Teardown:
    def __init__(self, runner: Runner, prompter: Prompter, target: Target, *, echo=print, sleep=time.sleep,
                 monotonic=time.monotonic, poll_seconds: int = 30, timeout_minutes: int = 120,
                 purge_wait_minutes: int = 10, stall_minutes: int = 30,
                 state_path: Path = STATE_FILE, environment_path: Path | None = None):
        self.run, self.prompter, self.target, self.echo = runner, prompter, target, echo
        self.sleep, self.monotonic = sleep, monotonic
        self.poll, self.timeout, self.purge_wait = poll_seconds, timeout_minutes * 60, purge_wait_minutes * 60
        self.stall = stall_minutes * 60
        if min(self.poll, self.timeout, self.stall) <= 0:
            raise DeployError("poll seconds, timeout minutes and stall minutes must all be positive")
        self.state_path = state_path
        self.environment_path = environment_path or REPO_ROOT / ".azure" / ENV_NAME
        self.results: list[tuple[str, bool, str]] = []
        self.deleted_resources: set[str] = set()

    def say(self, text: str = "") -> None:
        self.echo(text)

    def az(self, *args: str, tenant: bool = False) -> str:
        scope = [] if tenant else ["--subscription", self.target.subscription]
        return self.run.run(["az", *args, *scope, "--only-show-errors"], quiet=True)

    def az_json(self, *args: str, tenant: bool = False):
        return json.loads(self.az(*args, "-o", "json", tenant=tenant) or "null")

    def group_info(self) -> dict | None:
        return self.group_named(self.target.resource_group)

    def group_named(self, name: str) -> dict | None:
        try:
            info = self.az_json("group", "show", "-n", name)
            if not isinstance(info, dict):
                raise DeployError(f"unexpected resource group response for {name}; absence was not verified")
            return info
        except CommandError as error:
            if command_code(error) == "ResourceGroupNotFound":
                return None
            raise

    def list_resources(self) -> list[dict]:
        try:
            resources = self.az_json("resource", "list", "-g", self.target.resource_group,
                                     "--query", "[].{type:type,name:name}")
            if not isinstance(resources, list):
                raise DeployError("unexpected resource list response; refusing to assume the group is empty")
            return resources
        except CommandError as error:
            if command_code(error) == "ResourceGroupNotFound":
                return []
            raise

    def resource_id(self, row: dict) -> str:
        return (f"/subscriptions/{self.target.subscription}/resourceGroups/{self.target.resource_group}"
                f"/providers/{row['type']}/{row['name']}")

    def resource_info(self, identifier: str) -> dict | None:
        try:
            info = self.az_json("resource", "show", "--ids", identifier)
            if not isinstance(info, dict):
                raise DeployError(f"unexpected resource response for {identifier}; absence was not verified")
            return info
        except CommandError as error:
            if command_code(error) in {"ResourceNotFound", "ResourceGroupNotFound", "ParentResourceNotFound"}:
                return None
            raise

    def delete_failure(self, identifier: str, since: str | None) -> dict | None:
        window = ["--start-time", since] if since else ["--offset", "24h"]
        events = self.az_json("monitor", "activity-log", "list", "--resource-id", identifier,
                              *window, "--max-events", "100",
                              "--query", "[?contains(operationName.value, '/delete')]")
        if not isinstance(events, list):
            raise DeployError(f"unexpected Activity Log response for {identifier}; delete status could not be verified")
        events = [e for e in events if e.get("resourceId", "").lower() == identifier.lower()
                  and e.get("operationName", {}).get("value", "").lower().endswith("/delete")]
        if not events:
            return None
        latest = max(events, key=lambda e: e.get("eventTimestamp", ""))
        if latest.get("status", {}).get("value", "").lower() != "failed":
            return None
        detail = (latest.get("properties") or {}).get("statusMessage") or "Azure reported a failed delete"
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except json.JSONDecodeError:
                return {"code": "", "message": detail}
        return detail.get("error", detail) if isinstance(detail, dict) else {"message": str(detail)}

    def check_wait(self, started: float, changed: float, context: str, remaining: list[str]) -> None:
        now = self.monotonic()
        if now - started >= self.timeout or now - changed >= self.stall:
            for identifier in remaining:
                self.say(f"  blocked/pending: {identifier}")
            reason = (f"after {self.timeout // 60} minutes" if now - started >= self.timeout
                      else f"with no progress for {self.stall // 60} minutes")
            raise DeployError(f"{context} stopped {reason}; check Azure delete errors and dependent resources, "
                              "coordinate an approved teardown exclusion with governance automation, then "
                              "re-run this command to keep monitoring. No controls were disabled.")

    def request_delete(self, args: tuple[str, ...], attempts: int, started: float) -> int:
        while attempts < MAX_DELETE_ATTEMPTS:
            self.check_wait(started, self.monotonic(), "delete retry", [])
            attempts += 1
            try:
                self.az(*args)
                return attempts
            except CommandError as error:
                if command_code(error) in {"ResourceNotFound", "ResourceGroupNotFound", "ParentResourceNotFound"}:
                    return attempts
                if command_code(error) not in TRANSIENT_CODES or attempts == MAX_DELETE_ATTEMPTS:
                    raise
                self.say(f"  transient delete error: {error.stderr}; retry {attempts}/{MAX_DELETE_ATTEMPTS - 1}")
                self.check_wait(started, self.monotonic(), "delete retry", [])
                self.sleep(self.poll * attempts)
        raise DeployError(f"delete failed after {MAX_DELETE_ATTEMPTS} attempts")

    def delete_resources(self, title: str, kinds: tuple[str, ...], inv: Inventory, started: float) -> None:
        pending = {self.resource_id(r): r for r in self.list_resources() if r["type"].lower() in kinds}
        if not pending:
            return
        self.say(f"  Removing {title}: {len(pending)} resource(s)")
        attempts: dict[str, int] = {}
        since: dict[str, str | None] = {}
        changed, previous = self.monotonic(), None
        while pending:
            states = []
            for identifier, row in list(pending.items()):
                info = self.resource_info(identifier)
                if info is None:
                    self.deleted_resources.add(identifier)
                    del pending[identifier]
                    continue
                props = info.get("properties") or {}
                if row["type"].lower() == ENVIRONMENT:
                    managed = props.get("infrastructureResourceGroup")
                    if managed and managed not in inv.managed_groups:
                        inv.managed_groups.append(managed)
                state = props.get("provisioningState", "")
                states.append((identifier, state))
                args = ("resource", "delete", "--ids", identifier, "--no-wait")
                if identifier not in attempts:
                    if row["type"].lower() == "microsoft.search/searchservices" and state.lower() != "deleting":
                        links = self.az_json("search", "shared-private-link-resource", "list",
                                             "--service-name", row["name"], "-g", self.target.resource_group)
                        if not isinstance(links, list):
                            raise DeployError("unexpected Search shared private link list; refusing parent deletion")
                        for link in links:
                            name = link.get("name")
                            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
                                raise DeployError("invalid Search shared private link name; refusing deletion")
                            self.say(f"  removing Search shared private link {row['name']}/{name}")
                            self.request_delete(("search", "shared-private-link-resource", "delete",
                                                 "--service-name", row["name"], "-g", self.target.resource_group,
                                                 "--name", name, "--yes"), 0, started)
                    since[identifier] = None if state.lower() == "deleting" else datetime.now(UTC).isoformat()
                    attempts[identifier] = (0 if state.lower() == "deleting"
                                            else self.request_delete(args, 0, started))
                    continue
                failure = self.delete_failure(identifier, since[identifier])
                if failure:
                    if failure.get("code") in TRANSIENT_CODES and attempts[identifier] < MAX_DELETE_ATTEMPTS:
                        self.say(f"  transient asynchronous delete error: {error_detail(failure)}; retrying")
                        self.sleep(self.poll)
                        since[identifier] = datetime.now(UTC).isoformat()
                        attempts[identifier] = self.request_delete(args, attempts[identifier], started)
                        continue
                    raise DeployError(f"delete failed for {identifier}: {error_detail(failure)}")
            signature = tuple(sorted(states))
            if signature != previous:
                changed, previous = self.monotonic(), signature
            if pending:
                self.say(f"  waiting for {title}: {len(pending)} resource(s)")
                self.check_wait(started, changed, title, list(pending))
                self.sleep(self.poll)

    def wait_managed_groups(self, inv: Inventory, started: float) -> None:
        changed, previous = self.monotonic(), None
        while True:
            pending = [g for g in inv.managed_groups if self.group_named(g) is not None]
            if not pending:
                return
            signature = tuple(sorted(pending))
            if signature != previous:
                changed, previous = self.monotonic(), signature
            self.say("  Waiting for Azure-managed Container Apps infrastructure: " + ", ".join(pending)
                     + "; no managed load balancers will be force-deleted")
            self.check_wait(started, changed, "managed infrastructure cleanup", pending)
            self.sleep(self.poll)

    def list_deleted(self) -> tuple[list[dict], list[dict], list[dict]]:
        group = self.target.resource_group
        ai = [d for d in self.az_json("cognitiveservices", "account", "list-deleted") or [] if in_group(d.get("id", ""), group)]
        apim = [d for d in self.az_json("apim", "deletedservice", "list") or [] if in_group(d.get("serviceId", ""), group)]
        vaults = [d for d in self.az_json("keyvault", "list-deleted") or []
                  if in_group((d.get("properties") or {}).get("vaultId", ""), group)]
        return ai, apim, vaults

    def find_entra(self) -> tuple[list[dict], list[dict]]:
        names = entra_names(self.target.seed)
        apps, groups = [], []
        for name in names["apps"]:
            apps += [{**row, "name": name} for row in self.az_json(
                "ad", "app", "list", "--filter", f"displayName eq '{name}'", "--query", "[].{id:id,appId:appId}", tenant=True) or []]
        for name in names["groups"]:
            groups += [{**row, "name": name} for row in self.az_json(
                "ad", "group", "list", "--filter", f"displayName eq '{name}'", "--query", "[].{id:id}", tenant=True) or []]
        return apps, groups

    def inventory(self) -> Inventory:
        inv = Inventory(group=self.group_info())
        if inv.group is not None:
            tag = (inv.group.get("tags") or {}).get("azd-env-name")
            if tag != ENV_NAME:
                raise DeployError(f"resource group {self.target.resource_group} is not tagged azd-env-name={ENV_NAME}; refusing to touch it")
            inv.resources = self.list_resources()
        prefix = (f"/subscriptions/{self.target.subscription}/resourcegroups/{self.target.resource_group}"
                  f"/providers/{ENVIRONMENT}/").lower()
        groups = self.az_json("group", "list", "--query", "[?managedBy!=null].{name:name,managedBy:managedBy}")
        if not isinstance(groups, list):
            raise DeployError("unexpected managed resource group list; ownership could not be verified")
        inv.managed_groups = [g["name"] for g in groups if (g.get("managedBy") or "").lower().startswith(prefix)
                              and "/" not in g["managedBy"][len(prefix):]]
        inv.deleted_ai, inv.deleted_apim, inv.deleted_vaults = self.list_deleted()
        try:
            inv.apps, inv.groups = self.find_entra()
        except CommandError as error:
            inv.entra_error = error.stderr.splitlines()[-1] if error.stderr else "the Entra directory could not be read"
        return inv

    def show(self, inv: Inventory) -> None:
        t = self.target
        self.say(f"Teardown plan - {ENV_NAME}, subscription {t.subscription}")
        if inv.group is None:
            self.say(f"  resource group    {t.resource_group}: already gone")
        else:
            state = (inv.group.get("properties") or {}).get("provisioningState", "unknown")
            if inv.resources is None:
                self.say(f"  resource group    {t.resource_group} ({state}); resources could not be listed")
            else:
                self.say(f"  resource group    {t.resource_group} ({state}): {len(inv.resources)} resources"
                         + (f" - {breakdown(inv.resources)}" if inv.resources else ""))
        ai, apim = inv.expected(AI_ACCOUNT), inv.expected(APIM_SERVICE)
        self.say(f"  quota and names   purge after the group is gone: {len(ai)} AI account(s), {len(apim)} API Management service(s)"
                 f"; already soft-deleted: {len(inv.deleted_ai)} AI, {len(inv.deleted_apim)} API Management")
        retained = [v for v in inv.deleted_vaults if (v.get("properties") or {}).get("purgeProtectionEnabled")]
        self.say(f"  key vaults        {len(inv.deleted_vaults)} already soft-deleted ({len(retained)} purge-protected and kept until their retention ends)")
        if inv.entra_error:
            self.say(f"  Entra ID          could not be read ({inv.entra_error}); sign in with `az login` and re-run")
        elif not self.target.seed:
            self.say("  Entra ID          no naming seed known, so the legacy unsuffixed names are searched: "
                     f"{len(inv.apps)} app(s), {len(inv.groups)} group(s)")
        else:
            self.say(f"  Entra ID          {len(inv.apps)} app registration(s) and {len(inv.groups)} security group(s) named for this seed")
        self.say(f"  local state       azd environment .azure/{ENV_NAME} and the deployment state are archived, not deleted")
        if inv.managed_groups:
            self.say("  managed networking wait for Azure to remove: " + ", ".join(inv.managed_groups))
        self.say("  prerequisite      coordinate an approved teardown exclusion with governance automation "
                 "(including MCAPSGovernance-AutomationApp where present), so it does not recreate NSGs. "
                 "This command never disables policies or changes permissions.")

    def record(self, name: str, ok: bool, detail: str) -> None:
        self.results.append((name, ok, detail))
        self.say(f"  [{'done' if ok else 'FAILED'}] {name}: {detail}")

    def delete_group(self, inv: Inventory) -> None:
        name, group = "Azure resource group", self.target.resource_group
        self.say(f"\n== 1/4 {name}")
        if inv.group is None:
            self.wait_managed_groups(inv, self.monotonic())
            return self.record(name, True, "already gone")
        total, started = len(inv.resources or []), self.monotonic()
        state = (inv.group.get("properties") or {}).get("provisioningState", "")
        since = None
        attempts = 0
        if state == "Deleting":
            self.say("  A delete is already running; monitoring it.")
        else:
            for title, kinds in DELETE_ORDER:
                self.delete_resources(title, kinds, inv, started)
                if ENVIRONMENT in kinds:
                    self.wait_managed_groups(inv, started)
            self.say("  Starting the delete.")
            since = datetime.now(UTC).isoformat()
            attempts = self.request_delete(("group", "delete", "-n", group, "--yes", "--no-wait"), 0, started)
        changed, previous, inactive_polls = self.monotonic(), None, 0
        group_id = f"/subscriptions/{self.target.subscription}/resourceGroups/{group}"
        while (info := self.group_info()) is not None:
            failure = self.delete_failure(group_id, since)
            if failure:
                if failure.get("code") in TRANSIENT_CODES and attempts < MAX_DELETE_ATTEMPTS:
                    self.say(f"  transient group delete error: {error_detail(failure)}; retrying")
                    self.sleep(self.poll)
                    since = datetime.now(UTC).isoformat()
                    attempts = self.request_delete(("group", "delete", "-n", group, "--yes", "--no-wait"), attempts, started)
                    inactive_polls = 0
                    continue
                raise DeployError(f"resource group deletion failed: {error_detail(failure)}; local state retained")
            state = (info.get("properties") or {}).get("provisioningState", "")
            inactive_polls = inactive_polls + 1 if state.lower() != "deleting" else 0
            if inactive_polls >= 2:
                raise DeployError(f"resource group is no longer deleting (provisioningState={state}); "
                                  "inspect the Azure Activity Log and resolve dependencies before re-running; local state retained")
            remaining = self.list_resources()
            minutes = int((self.monotonic() - started) // 60)
            identifiers = frozenset(self.resource_id(r) for r in remaining)
            if identifiers != previous:
                added = identifiers & self.deleted_resources
                if previous is not None:
                    added |= identifiers - previous
                if added:
                    self.say("  WARNING: resources appeared/reappeared during deletion: " + ", ".join(sorted(added))
                             + ". Check external governance automation; no controls will be disabled.")
                changed, previous = self.monotonic(), identifiers
            total = max(total, len(remaining))
            self.say(f"  [+{minutes}m] {len(remaining)} of {total} resources still to remove"
                     + (f" ({breakdown(remaining)})" if remaining else ""))
            self.check_wait(started, changed, "resource group deletion", sorted(identifiers))
            self.sleep(self.poll)
        self.wait_managed_groups(inv, started)
        self.record(name, True, f"deleted ({total} resources)" if total else "deleted")

    def purge_soft_deleted(self, inv: Inventory) -> None:
        name, group = "Quota and name release", self.target.resource_group
        self.say(f"\n== 2/4 {name}")
        expected = {n.lower() for n in inv.expected(AI_ACCOUNT) + inv.expected(APIM_SERVICE)}
        done: set[str] = set()
        failures: list[str] = []
        failed: set[str] = set()
        waited = 0
        while True:
            ai, apim, _ = self.list_deleted()
            found = {d["name"].lower() for d in ai + apim}
            pending = [("AI account", d) for d in ai] + [("API Management", d) for d in apim]
            total = len(expected | found)
            for kind, item in pending:
                key = item["name"].lower()
                if key in done or key in failed:
                    continue
                try:
                    if kind == "AI account":
                        self.az("cognitiveservices", "account", "purge", "--name", item["name"],
                                "--resource-group", group, "--location", item["location"])
                    else:
                        self.az("apim", "deletedservice", "purge", "--service-name", item["name"], "--location", item["location"])
                    done.add(key)
                    self.say(f"  purged {kind} {item['name']} ({len(done)} of {total})")
                except CommandError as error:
                    reason = error.stderr.splitlines()[-1] if error.stderr else "purge failed"
                    failed.add(key)
                    failures.append(f"{item['name']}: {reason}")
                    self.say(f"  could not purge {kind} {item['name']}: {reason}")
            missing = expected - done - failed
            if not missing:
                break
            if waited >= self.purge_wait:
                failures += [f"{m}: no soft-deleted record appeared yet; re-run later" for m in sorted(missing)]
                break
            self.say(f"  waiting for {len(missing)} soft-deleted record(s) to appear: {', '.join(sorted(missing))}")
            self.sleep(self.poll)
            waited += self.poll
        kept = purged_vaults = 0
        for vault in self.list_deleted()[2]:
            props = vault.get("properties") or {}
            if props.get("purgeProtectionEnabled"):
                kept += 1
                self.say(f"  key vault {vault['name']} kept: purge protection until {props.get('scheduledPurgeDate', 'its retention ends')}")
                continue
            try:
                self.az("keyvault", "purge", "--name", vault["name"], "--location", vault["location"])
                purged_vaults += 1
            except CommandError:
                failures.append(f"{vault['name']}: key vault purge failed")
        detail = f"{len(done)} AI/API Management record(s) purged"
        if purged_vaults or kept:
            detail += f", key vaults: {purged_vaults} purged, {kept} kept by purge protection"
        self.record(name, not failures, detail + ("; " + "; ".join(failures) if failures else ""))

    def delete_entra(self, inv: Inventory) -> None:
        name = "Entra ID"
        self.say(f"\n== 3/4 {name}")
        if inv.entra_error:
            return self.record(name, False, inv.entra_error)
        apps, groups = self.find_entra()
        total, deleted, failures = len(apps) + len(groups), 0, []
        for kind, row in [("app", a) for a in apps] + [("group", g) for g in groups]:
            try:
                if kind == "app":
                    self.az("ad", "app", "delete", "--id", row["appId"], tenant=True)
                else:
                    self.az("ad", "group", "delete", "--group", row["id"], tenant=True)
                deleted += 1
                self.say(f"  deleted {kind} {row['name']} ({deleted} of {total})")
            except CommandError as error:
                failures.append(f"{row['name']}: {error.stderr.splitlines()[-1] if error.stderr else 'delete failed'}")
        if not total:
            self.say("  nothing to delete")
        self.record(name, not failures, f"{deleted} of {total} object(s) deleted"
                    + ("; " + "; ".join(failures) if failures else "; deleted apps stay recoverable in Entra for 30 days"))

    def archive_local(self) -> None:
        name = "Local state"
        self.say(f"\n== 4/4 {name}")
        state_ok = self.state_path.exists() and historical_answers(self.state_path).get("naming_seed", "") == self.target.seed
        env_ok = self.environment_path.exists() and not self.environment_path.is_symlink()
        if not (state_ok or env_ok):
            return self.record(name, True, "nothing to archive")
        base = self.state_path.parent / "deploy-history" / (self.target.seed or "legacy") / "torn-down"
        archive = base.with_name(f"torn-down-{datetime.now():%Y%m%d%H%M%S}") if base.exists() else base
        archive.mkdir(parents=True)
        if state_ok:
            shutil.move(str(self.state_path), archive / "state.json")
        if env_ok:
            shutil.move(str(self.environment_path), archive / "azd-environment")
        self.record(name, True, f"archived at {archive}")

    def execute(self, *, plan_only: bool, assume_yes: bool, keep_entra: bool, keep_local: bool) -> int:
        inv = self.inventory()
        self.show(inv)
        if plan_only:
            self.say("\nNothing was changed. Run without --plan to tear this deployment down.")
            return 0
        if not assume_yes:
            if not self.prompter.interactive:
                raise DeployError("not interactive: pass --yes to tear down unattended")
            typed = self.prompter.question(f"\nThis permanently deletes the resources above. Type the resource group name "
                                           f"({self.target.resource_group}) to continue")
            if typed != self.target.resource_group:
                self.say("Nothing was changed.")
                return 0
        self.delete_group(inv)
        self.purge_soft_deleted(inv)
        if keep_entra:
            self.say("\n== 3/4 Entra ID\n  skipped (--keep-entra)")
        else:
            self.delete_entra(inv)
        if keep_local or not all(ok for _, ok, _ in self.results):
            self.say("\n== 4/4 Local state\n  left in place" + (" (--keep-local)" if keep_local else " because a step failed; re-run to finish"))
        else:
            self.archive_local()
        failed = [r for r in self.results if not r[1]]
        self.say("\nTeardown " + ("finished with problems; re-run `python -m scripts.teardown` after fixing them." if failed else
                                  "finished. Deliver with `python -m scripts.deploy --inputs <private-delivery.json>`."))
        self.say("Log Analytics workspace names stay reserved for 14 days after deletion; a fresh deployment uses a new seed, so it is not affected.")
        return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--plan", action="store_true", help="show what would be removed and change nothing")
    p.add_argument("--yes", action="store_true", help="skip the typed confirmation")
    p.add_argument("--non-interactive", action="store_true")
    p.add_argument("--subscription")
    p.add_argument("--resource-group")
    p.add_argument("--seed", help="the deployment's naming seed; scopes the Entra clean-up (default: saved state)")
    p.add_argument("--keep-entra", action="store_true", help="leave the Entra apps and groups in place")
    p.add_argument("--keep-local", action="store_true", help="do not archive the local azd environment and state")
    p.add_argument("--poll-seconds", type=int, default=30)
    p.add_argument("--timeout-minutes", type=int, default=120)
    p.add_argument("--stall-minutes", type=int, default=30, help="stop after this many minutes without deletion progress")
    return p


def main(argv: list[str] | None = None, *, runner: Runner | None = None, prompter: Prompter | None = None,
         state_path: Path = STATE_FILE, environment_path: Path | None = None, sleep=time.sleep,
         monotonic=time.monotonic) -> int:
    args = build_parser().parse_args(argv)
    prompter = prompter or Prompter(interactive=not args.non_interactive and sys.stdin.isatty())
    try:
        env_dir = environment_path or REPO_ROOT / ".azure" / ENV_NAME
        target = resolve_target(args, historical_answers(state_path), read_environment(env_dir / ".env"))
        teardown = Teardown(runner or Runner(), prompter, target, sleep=sleep, monotonic=monotonic,
                            poll_seconds=args.poll_seconds, timeout_minutes=args.timeout_minutes,
                            stall_minutes=args.stall_minutes,
                            state_path=state_path, environment_path=env_dir)
        return teardown.execute(plan_only=args.plan, assume_yes=args.yes, keep_entra=args.keep_entra, keep_local=args.keep_local)
    except DeployError as error:
        print(f"\nStopped: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
