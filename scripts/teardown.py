#!/usr/bin/env python3
"""Guided teardown of a GovernedGraph gg-dev deployment: the counterpart of scripts/deploy.py.

    python -m scripts.teardown --plan     # inventory only; nothing is changed
    python -m scripts.teardown            # inventory, typed confirmation, then the clean-up

One run removes everything the deployment left behind and reports progress as it goes:

1. the resource group (a delete that is already running is picked up, not repeated), with a live
   "N of M resources still to remove" count;
2. the soft-deleted AI and API Management records that keep model quota and names reserved, purged only
   when they belong to this resource group; Key Vaults are purged only when purge protection allows it;
3. the Entra apps and security groups, found by the exact seed-scoped names the deployment created;
4. the local azd environment and deployment state, archived (never deleted) under .run/deploy-history.

Scope is deliberately narrow: only the saved (or flagged) resource group, which must carry the
`azd-env-name=gg-dev` tag, and only Entra objects named for this deployment's naming seed. Re-running
is safe; each step looks at what is left. The script never grants access, never touches another
environment and never purges a purge-protected vault.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import UUID

from scripts.deploy import (
    ENV_NAME,
    REPO_ROOT,
    STATE_FILE,
    CommandError,
    DeployError,
    Prompter,
    Runner,
    State,
    validate_guid,
    validate_rg,
)

ENVIRONMENT_FILE = REPO_ROOT / ".azure" / ENV_NAME / ".env"
AI_ACCOUNT = "microsoft.cognitiveservices/accounts"
APIM_SERVICE = "microsoft.apimanagement/service"
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


def resolve_target(args, state: State, environment: dict[str, str]) -> Target:
    answers = state.answers
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


class Teardown:
    def __init__(self, runner: Runner, prompter: Prompter, target: Target, *, echo=print, sleep=time.sleep,
                 monotonic=time.monotonic, poll_seconds: int = 30, timeout_minutes: int = 120,
                 purge_wait_minutes: int = 10, state_path: Path = STATE_FILE, environment_path: Path | None = None):
        self.run, self.prompter, self.target, self.echo = runner, prompter, target, echo
        self.sleep, self.monotonic = sleep, monotonic
        self.poll, self.timeout, self.purge_wait = poll_seconds, timeout_minutes * 60, purge_wait_minutes * 60
        self.state_path = state_path
        self.environment_path = environment_path or REPO_ROOT / ".azure" / ENV_NAME
        self.results: list[tuple[str, bool, str]] = []

    def say(self, text: str = "") -> None:
        self.echo(text)

    def az(self, *args: str, tenant: bool = False) -> str:
        scope = [] if tenant else ["--subscription", self.target.subscription]
        return self.run.run(["az", *args, *scope, "--only-show-errors"], quiet=True)

    def az_json(self, *args: str, tenant: bool = False):
        return json.loads(self.az(*args, "-o", "json", tenant=tenant) or "null")

    def group_info(self) -> dict | None:
        try:
            return self.az_json("group", "show", "-n", self.target.resource_group)
        except CommandError as error:
            if "ResourceGroupNotFound" in error.stderr or "could not be found" in error.stderr:
                return None
            raise

    def list_resources(self) -> list[dict] | None:
        try:
            return self.az_json("resource", "list", "-g", self.target.resource_group, "--query", "[].{type:type,name:name}") or []
        except CommandError:
            return None

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

    def record(self, name: str, ok: bool, detail: str) -> None:
        self.results.append((name, ok, detail))
        self.say(f"  [{'done' if ok else 'FAILED'}] {name}: {detail}")

    def delete_group(self, inv: Inventory) -> None:
        name, group = "Azure resource group", self.target.resource_group
        self.say(f"\n== 1/4 {name}")
        if inv.group is None:
            return self.record(name, True, "already gone")
        state = (inv.group.get("properties") or {}).get("provisioningState", "")
        if state == "Deleting":
            self.say("  A delete is already running; monitoring it.")
        else:
            self.say("  Starting the delete.")
            self.az("group", "delete", "-n", group, "--yes", "--no-wait")
        total, started = len(inv.resources or []), self.monotonic()
        while self.group_info() is not None:
            remaining = self.list_resources()
            minutes = int((self.monotonic() - started) // 60)
            if remaining is None:
                self.say(f"  [+{minutes}m] still deleting")
            else:
                total = max(total, len(remaining))
                self.say(f"  [+{minutes}m] {len(remaining)} of {total} resources still to remove"
                         + (f" ({breakdown(remaining)})" if remaining else ""))
            if self.monotonic() - started >= self.timeout:
                raise DeployError(f"the resource group is still deleting after {self.timeout // 60} minutes; "
                                  "re-run this command to keep monitoring")
            self.sleep(self.poll)
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
        state_ok = self.state_path.exists() and State.load(self.state_path).answers.get("naming_seed", "") == self.target.seed
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
                                  "finished. Start clean with `python -m scripts.deploy --mode fresh`."))
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
    return p


def main(argv: list[str] | None = None, *, runner: Runner | None = None, prompter: Prompter | None = None,
         state_path: Path = STATE_FILE, environment_path: Path | None = None, sleep=time.sleep,
         monotonic=time.monotonic) -> int:
    args = build_parser().parse_args(argv)
    prompter = prompter or Prompter(interactive=not args.non_interactive and sys.stdin.isatty())
    try:
        env_dir = environment_path or REPO_ROOT / ".azure" / ENV_NAME
        target = resolve_target(args, State.load(state_path), read_environment(env_dir / ".env"))
        teardown = Teardown(runner or Runner(), prompter, target, sleep=sleep, monotonic=monotonic,
                            poll_seconds=args.poll_seconds, timeout_minutes=args.timeout_minutes,
                            state_path=state_path, environment_path=env_dir)
        return teardown.execute(plan_only=args.plan, assume_yes=args.yes, keep_entra=args.keep_entra, keep_local=args.keep_local)
    except DeployError as error:
        print(f"\nStopped: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
