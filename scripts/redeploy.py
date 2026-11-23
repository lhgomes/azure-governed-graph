"""Code-only redeploy: reuse the last successful delivery's reviewed parameters and replace image digests only."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from scripts.delivery import AGENT_NAMES, DeliveryError, source_digest

DEFINITION_ROLES = ("counsellor", "voice-compact", "fairness", "persona", "gap-review")
WORKLOAD_IMAGES = {
    "orchestratorImage": "runtime", "strategyServiceImage": "runtime", "memberValuesImage": "runtime",
    "contactCentreImage": "runtime", "adminImage": "runtime", "p2ContainerImage": "p2",
}
BOOTSTRAP_IMAGES = {"runtimeImage": "runtime", "probeImage": "probe", "fixturesImage": "fixtures"}
BOOTSTRAP_TEMPLATE = "infra/bootstrap.bicep"
REQUIRED_APPS = ("orchestrator", "strategy-service", "member-values", "contact-centre", "admin")
SCOPE = ("subscription", "resource_group", "naming_seed", "environment")
PROVISIONING = ("scripts/bootstrap.py", "scripts/create_agents.py", "scripts/configure_*.py",
                "src/governedgraph/strategy_service/provision_auth.py")
CHANGES = {
    "bicep": "infrastructure templates",
    "provisioning": "provisioning scripts",
    "inputs": "delivery inputs",
    "data_sha256": "domain data or the release approval",
    "definitions": "Foundry agent definitions",
}


class NotCodeOnly(DeliveryError):
    """The change cannot be applied as images alone; raised before any cloud change."""


def digest_files(paths, root: Path) -> dict:
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(set(paths))}


def fingerprint(composition, inputs, *, root: Path) -> dict:
    """Everything a code-only redeploy must not change; the operator may be resolved at delivery, so it is excluded."""
    from scripts.create_agents import provision

    return {
        "bicep": digest_files([*root.glob("infra/**/*.bicep"), *root.glob("examples/*/infra/*.bicep")], root),
        "provisioning": digest_files([path for pattern in PROVISIONING for path in root.glob(pattern)], root),
        "inputs": {key: value for key, value in asdict(inputs).items() if key != "operator_id"},
        "data_sha256": source_digest([composition.data_directory, composition.release_approval], {}),
        "definitions": {role: provision(None, apply=False, role=role,
                                        agent_name=AGENT_NAMES.get(role))["definition_sha256"]
                        for role in DEFINITION_ROLES},
    }


def deployment_entry(evidence: dict, template: str) -> dict | None:
    """The parameters of the latest successful deployment of one template, as recorded in delivery evidence."""
    for record in reversed(evidence.get("deployments", [])):
        if record.get("source_template") == template and record.get("status") == "Succeeded":
            if "parameters_file" in record and "parameters_sha256" in record:
                return {key: record[key] for key in ("parameters_file", "parameters_sha256")}
    return None


def baselines(evidence: dict, composition) -> dict:
    entries = {"workloads": deployment_entry(evidence, composition.workload_template),
               "bootstrap": deployment_entry(evidence, BOOTSTRAP_TEMPLATE)}
    return {name: entry for name, entry in entries.items() if entry is not None}


def load_baseline(inputs, *, root: Path) -> dict:
    try:
        receipt = json.loads((root / ".run/delivery.json").read_text())
    except (OSError, ValueError):
        raise NotCodeOnly("there is no readable receipt of a successful delivery") from None
    if not isinstance(receipt, dict):
        raise NotCodeOnly("the last delivery receipt is malformed")
    if tuple(receipt.get(key) for key in SCOPE) != tuple(getattr(inputs, key) for key in SCOPE):
        raise NotCodeOnly("the last successful delivery is for a different deployment scope")
    if receipt.get("readiness") != "passed":
        raise NotCodeOnly("the last delivery did not pass readiness")
    if not (isinstance(receipt.get("fingerprint"), dict) and set(CHANGES) <= set(receipt["fingerprint"])
            and isinstance(receipt.get("images"), dict) and isinstance(receipt.get("outputs"), dict)
            and {"workloads", "bootstrap"} <= set(receipt.get("baseline") or {})):
        raise NotCodeOnly("the last delivery predates code-only redeploys")
    return receipt


def require_unchanged(previous: dict, current: dict) -> None:
    changed = [label for key, label in CHANGES.items() if previous.get(key) != current[key]]
    if changed:
        raise NotCodeOnly("changes to " + ", ".join(changed) + " need the full deployment")


def stored_parameters(entry: dict, *, root: Path) -> dict:
    path = Path(entry["parameters_file"]).resolve()
    try:
        if not path.is_relative_to((root / ".run/deliveries").resolve()):
            raise NotCodeOnly("stored deployment parameters are outside the delivery evidence directory")
        content = path.read_bytes()
    except OSError:
        raise NotCodeOnly("stored deployment parameters are unavailable") from None
    if hashlib.sha256(content).hexdigest() != entry["parameters_sha256"]:
        raise NotCodeOnly("stored deployment parameters changed since they were deployed")
    return {key: item["value"] for key, item in json.loads(content)["parameters"].items()}


def replace_images(parameters: dict, mapping: dict[str, str], images: dict) -> dict:
    missing = sorted(set(mapping) - set(parameters))
    if missing:
        raise NotCodeOnly("stored deployment parameters lack image fields: " + ", ".join(missing))
    return {**parameters, **{key: images[role]["image"] for key, role in mapping.items()}}


def changed_images(previous: dict, images: dict) -> set[str]:
    return {role for role, image in images.items()
            if (previous.get(role) or {}).get("image") != image["image"]}
