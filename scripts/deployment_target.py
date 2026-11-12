"""Discover, select and retain an explicit gg-dev deployment scope."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID, uuid4

from scripts import delivery
from scripts.azd_env import load
from scripts.delivery import DeliveryError, Inputs, resource_group_name


def read_inputs(path: Path) -> dict:
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict):
        raise DeliveryError("delivery inputs must be a JSON object")
    if any(field in raw and not isinstance(raw[field], str) for field in ("naming_seed", "resource_group")):
        raise DeliveryError("saved target fields must be strings")
    return raw


def original_seed(value: str) -> str:
    if not isinstance(value, str):
        raise DeliveryError("original naming seed must be a UUID string")
    try:
        UUID(value)
    except ValueError:
        raise DeliveryError("original naming seed must be a UUID string") from None
    return value


def new_target(raw: dict) -> Inputs:
    seed = str(uuid4())
    return Inputs.from_dict({**raw, "naming_seed": seed, "resource_group": resource_group_name(seed)})


def discover(runner, inputs: Inputs) -> list[dict]:
    rows = json.loads(runner.run([
        "az", "group", "list", "--subscription", inputs.subscription,
        "--tag", "workload=gg-journey-graph", "--query",
        "[].{name:name,location:location,tags:tags,state:properties.provisioningState,managedBy:managedBy}", "-o", "json",
    ]))
    if not isinstance(rows, list) or any(
        not isinstance(row, dict) or not isinstance(row.get("name"), str)
        or not isinstance(row.get("tags"), dict) or "managedBy" not in row for row in rows
    ):
        raise DeliveryError("resource-group discovery returned malformed metadata")
    groups = [row for row in rows if row["tags"].get("workload") == "gg-journey-graph"
              and row["tags"].get("azd-env-name") == inputs.environment
              and row["tags"].get("data") == "synthetic" and row.get("location") == inputs.location
              and not row["managedBy"]]
    names = [row["name"].casefold() for row in groups]
    if len(set(names)) != len(names):
        raise DeliveryError("resource-group discovery returned duplicate names")
    return sorted(groups, key=lambda row: row["name"].casefold())


def saved_scopes(raw: dict, inputs: Inputs) -> list[dict]:
    scopes = []
    if raw.get("naming_seed"):
        seed = original_seed(raw["naming_seed"])
        scopes.append({
            "AZURE_SUBSCRIPTION_ID": raw["subscription"],
            "AZURE_RESOURCE_GROUP": raw.get("resource_group") or resource_group_name(seed),
            "GOVERNEDGRAPH_NAMING_SEED": seed,
        })
    directory = delivery.ROOT / ".azure"
    files = [directory / inputs.environment / ".env",
             *sorted((directory / "archived").glob(f"*/{inputs.environment}/.env"))]
    for path in files:
        if path.is_file():
            scopes.append(load(env_file=str(path)))
    return scopes


def existing_target(runner, group: dict, raw: dict, inputs: Inputs, scopes: list[dict]) -> Inputs:
    if group.get("state") != "Succeeded":
        raise DeliveryError("selected resource group is not ready; inspect its provisioning state")
    local_seeds = set()
    for scope in scopes:
        if (scope.get("AZURE_SUBSCRIPTION_ID") == inputs.subscription
                and scope.get("AZURE_RESOURCE_GROUP", "").casefold() == group["name"].casefold()
                and scope.get("GOVERNEDGRAPH_NAMING_SEED")):
            local_seeds.add(original_seed(scope["GOVERNEDGRAPH_NAMING_SEED"]))
    records = json.loads(runner.run([
        "az", "deployment", "sub", "list", "--subscription", inputs.subscription, "--query",
        "[].{group:properties.parameters.resourceGroupName.value,"
        "seed:properties.parameters.namingSeed.value,environment:properties.parameters.environmentName.value,"
        "state:properties.provisioningState}",
        "-o", "json",
    ]))
    if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
        raise DeliveryError("deployment seed discovery returned malformed metadata")
    successful = set()
    observed_seeds = set()
    for record in records:
        if (isinstance(record.get("group"), str) and record["group"].casefold() == group["name"].casefold()
                and record.get("environment") == inputs.environment and record.get("seed")):
            seed = original_seed(record["seed"])
            if not isinstance(record.get("state"), str) or not record["state"]:
                raise DeliveryError("deployment seed discovery returned missing provisioning state")
            observed_seeds.add(seed)
            if record["state"] == "Succeeded":
                successful.add((record["group"], seed))
    conflict = ("selected group's original naming seed is missing or conflicting; "
                "restore its saved scope rather than generating a replacement seed")
    if len(successful) == 1:
        name, seed = successful.pop()
        if any(UUID(saved) != UUID(seed) for saved in local_seeds):
            raise DeliveryError(conflict)
        if any(saved != seed for saved in local_seeds):
            print("Restoring the exact naming seed from successful Azure deployment metadata.",
                  file=sys.stderr, flush=True)
    elif not successful and len(local_seeds) == 1 and observed_seeds <= local_seeds:
        name, seed = group["name"], local_seeds.pop()
    else:
        raise DeliveryError(conflict)
    return Inputs.from_dict({**raw, "resource_group": name, "naming_seed": seed})


def same_scope(saved: dict, inputs: Inputs) -> bool:
    return (saved.get("AZURE_SUBSCRIPTION_ID") == inputs.subscription
            and saved.get("AZURE_RESOURCE_GROUP") == inputs.resource_group
            and saved.get("GOVERNEDGRAPH_NAMING_SEED") == inputs.naming_seed)


def retain(path: Path, raw: dict, inputs: Inputs) -> None:
    directory = delivery.ROOT / ".azure" / inputs.environment
    env_file = directory / ".env"
    saved = load(env_file=str(env_file)) if env_file.is_file() else {}
    archive = None
    updated = {**raw, "naming_seed": inputs.naming_seed, "resource_group": inputs.resource_group}
    with NamedTemporaryFile(mode="w", dir=path.parent, prefix=".delivery-", suffix=".json",
                            encoding="utf-8", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(updated, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        except OSError:
            temporary.unlink(missing_ok=True)
            raise
    try:
        if directory.exists() and not same_scope(saved, inputs):
            archive = delivery.ROOT / ".azure" / "archived" / uuid4().hex / inputs.environment
            archive.parent.mkdir(parents=True)
            directory.rename(archive)
        try:
            os.replace(temporary, path)
        except OSError:
            if archive is not None:
                archive.rename(directory)
            raise
    finally:
        temporary.unlink(missing_ok=True)
    if archive is not None:
        print(f"Previous local environment preserved at {archive.relative_to(delivery.ROOT)}; "
              "its Azure resources were not changed.", flush=True)
    print(f"Selected deployment: {inputs.resource_group}; target saved to the private inputs.", flush=True)


def select_target(runner, path: Path, raw: dict, candidate: Inputs, *,
                  reuse: str | None = None, create_new: bool = False) -> Inputs:
    if not create_new and reuse is None and not sys.stdin.isatty():
        raise DeliveryError("non-interactive deployment requires --reuse RESOURCE_GROUP or --new")
    groups = discover(runner, candidate)
    if create_new:
        selected = len(groups)
    elif reuse is not None:
        selected = next((index for index, group in enumerate(groups) if group["name"].casefold() == reuse.casefold()), -1)
        if selected < 0:
            raise DeliveryError("--reuse must name a discovered synthetic GovernedGraph gg-dev group in swedencentral")
    else:
        active = delivery.ROOT / ".azure" / candidate.environment / ".env"
        previous = raw.get("resource_group")
        if not previous and active.is_file():
            saved = load(env_file=str(active))
            if saved.get("AZURE_SUBSCRIPTION_ID") == candidate.subscription:
                previous = saved.get("AZURE_RESOURCE_GROUP")
        default = next((index for index, group in enumerate(groups)
                        if previous and group["name"].casefold() == previous.casefold()), len(groups))
        for index, group in enumerate(groups):
            print(f"{index + 1}) Reuse {group['name']}", flush=True)
        print(f"{len(groups) + 1}) New deployment (fresh seed and resource group; old resources retained)", flush=True)
        while True:
            try:
                answer = input(f"Deployment [{default + 1}]: ").strip()
            except EOFError:
                raise DeliveryError("input closed; rerun with --reuse RESOURCE_GROUP or --new") from None
            if not answer:
                selected = default
                break
            if answer.isdecimal() and 1 <= int(answer) <= len(groups) + 1:
                selected = int(answer) - 1
                break
            print(f"Enter a number from 1 to {len(groups) + 1}.", file=sys.stderr, flush=True)
    if selected == len(groups):
        exists = json.loads(runner.run(["az", "group", "exists", "--subscription", candidate.subscription,
                                      "--name", candidate.resource_group, "-o", "json"]))
        if not isinstance(exists, bool) or exists:
            raise DeliveryError("cannot confirm the generated resource group is new; no deployment target was changed")
        target = candidate
    else:
        target = existing_target(runner, groups[selected], raw, candidate, saved_scopes(raw, candidate))
    retain(path, raw, target)
    return target
