"""Closed owner decisions and signed catalogue releases; only P2 signs publication."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, fields
from itertools import product
import re

from governedgraph.common.contracts import ACCESSIBILITY_MODES, CHANNELS, ContractError, RequestKey, _strict
from governedgraph.common.io import canonical_json
from governedgraph.strategy_service.catalogue import Catalogue, ESCALATION_STRATEGY, Strategy
from governedgraph.strategy_service.snapshot import RankingSnapshot, Signer, expert_rankings, sign_snapshot

WATERMARK = "ILLUSTRATIVE - synthetic data"


def patterns() -> list[Strategy]:
    return [style for style in Catalogue.load().all() if style.strategy_id != ESCALATION_STRATEGY]


def stages() -> set[str]:
    return {stage for style in Catalogue.load().all() for stage in style.eligible_journey_states if stage != "*"}


@dataclass(frozen=True)
class StyleChange:
    action: str
    strategy_id: str = ""
    name: str = ""
    pattern_id: str = ""
    eligible_journey_states: list[str] | None = None
    channels: list[str] | None = None
    accessibility_modes: list[str] | None = None

    def __post_init__(self):
        if not isinstance(self.action, str) or self.action not in {"add", "edit", "remove", "cancel"}:
            raise ContractError("unknown style action")
        if not isinstance(self.strategy_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{0,100}", self.strategy_id):
            raise ContractError("invalid style reference")
        if not isinstance(self.name, str) or not isinstance(self.pattern_id, str):
            raise ContractError("style names and pattern references must be text")
        if self.action in {"add", "cancel"} and self.strategy_id or self.action in {"edit", "remove"} and not self.strategy_id:
            raise ContractError("add generates an ID; edit and remove require an existing ID")
        if self.action in {"remove", "cancel"}:
            if self.name or self.pattern_id or any(value is not None for value in
                    (self.eligible_journey_states, self.channels, self.accessibility_modes)):
                raise ContractError("remove and cancel accept no style settings")
            return
        if not isinstance(self.name, str) or not 1 <= len(self.name.strip()) <= 120:
            raise ContractError("style name must contain 1 to 120 characters")
        if not isinstance(self.pattern_id, str) or self.pattern_id not in {style.strategy_id for style in patterns()}:
            raise ContractError("choose an approved explanation pattern")
        for selected, allowed in ((self.eligible_journey_states, stages()), (self.channels, CHANNELS),
                                  (self.accessibility_modes, ACCESSIBILITY_MODES)):
            if (not isinstance(selected, list) or not selected or any(not isinstance(item, str) for item in selected)
                    or len(selected) != len(set(selected)) or not set(selected) <= allowed):
                raise ContractError("choose nonempty, approved and distinct eligibility settings")

    @classmethod
    def from_dict(cls, payload: dict) -> StyleChange:
        return _strict(cls, payload)


@dataclass(frozen=True)
class CatalogueDecision:
    expected_revision: str
    decision_ref: str
    reason: str
    confirmed: bool
    change: StyleChange | dict

    def __post_init__(self):
        if (not isinstance(self.expected_revision, str) or not re.fullmatch(r"[a-f0-9]{64}", self.expected_revision)
                or not isinstance(self.decision_ref, str) or not re.fullmatch(r"GB-[a-f0-9]{32}", self.decision_ref)
                or not isinstance(self.reason, str) or not 8 <= len(self.reason.strip()) <= 500
                or self.confirmed is not True):
            raise ContractError("a confirmed owner decision, reason and current revision are required")
        if isinstance(self.change, dict):
            object.__setattr__(self, "change", StyleChange.from_dict(self.change))
        if not isinstance(self.change, StyleChange):
            raise ContractError("a closed style change is required")

    @property
    def action(self) -> str:
        if not isinstance(self.change, StyleChange):
            raise ContractError("a parsed style change is required")
        return "catalogue_" + self.change.action

    @classmethod
    def from_dict(cls, payload: dict) -> CatalogueDecision:
        return _strict(cls, payload)


@dataclass(frozen=True)
class CatalogueBundle:
    catalogue: dict
    priors: dict

    def __post_init__(self):
        if (not isinstance(self.catalogue, dict)
                or set(self.catalogue) != {"catalogue_version", "strategies", "retired_versions"}
                or not isinstance(self.catalogue["catalogue_version"], str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", self.catalogue["catalogue_version"])):
            raise ContractError("invalid versioned catalogue")
        allowed = {item.name for item in fields(Strategy)}
        approved = {(style.template_id, style.template_version, style.teach_back) for style in Catalogue.load().all()}
        identities = set()
        for collection in ("strategies", "retired_versions"):
            rows = self.catalogue[collection]
            if not isinstance(rows, list) or len(rows) > 10000:
                raise ContractError("invalid bounded catalogue versions")
            seen = set()
            for row in rows:
                if not isinstance(row, dict) or set(row) != allowed:
                    raise ContractError("unknown catalogue fields")
                if (any(not isinstance(row[key], str) or not 1 <= len(row[key]) <= 120
                        for key in ("strategy_id", "version", "name", "status", "template_id", "template_version"))
                        or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", row["strategy_id"])
                        or row["status"] not in {"active", "retired"}
                        or type(row["teach_back"]) is not bool or type(row["ranked_for_resolution"]) is not bool
                        or (row["template_id"], row["template_version"], row["teach_back"]) not in approved):
                    raise ContractError("style must reference an approved explanation pattern")
                for key, values in (("eligible_journey_states", stages() | {"*"}), ("channels", CHANNELS),
                                    ("accessibility_modes", ACCESSIBILITY_MODES)):
                    items = row[key]
                    if (not isinstance(items, (list, tuple)) or not items
                            or any(not isinstance(item, str) for item in items)
                            or len(items) != len(set(items)) or not set(items) <= values):
                        raise ContractError("invalid catalogue eligibility")
                identity = row["strategy_id"] if collection == "strategies" else (row["strategy_id"], row["version"])
                if identity in seen:
                    raise ContractError("duplicate catalogue version")
                seen.add(identity)
                if collection == "strategies":
                    identities.add(row["strategy_id"])
        current = Catalogue(self.catalogue)
        if ESCALATION_STRATEGY not in identities:
            raise ContractError("human escalation cannot be removed")
        escalation = current.get(ESCALATION_STRATEGY)
        if asdict(escalation) != asdict(Catalogue.load().get(ESCALATION_STRATEGY)):
            raise ContractError("human escalation cannot be edited or removed")
        if (not isinstance(self.priors, dict)
                or set(self.priors) != {"priors_version", "approved_ref", "watermark", "default", "rankings"}
                or self.priors["watermark"] != WATERMARK or not isinstance(self.priors["approved_ref"], str)
                or not re.fullmatch(r"GB-[a-f0-9]{32}", self.priors["approved_ref"])
                or self.priors["priors_version"] != self.catalogue["catalogue_version"]
                or not isinstance(self.priors["rankings"], dict)):
            raise ContractError("invalid owner-approved expert baseline")
        for key, order in [("", self.priors["default"]), *self.priors["rankings"].items()]:
            if not isinstance(key, str) or (key and len(key.split("|")) != 4):
                raise ContractError("invalid expert situation")
            if key:
                RequestKey(*key.split("|"))
            if (not isinstance(order, list) or any(not isinstance(sid, str) for sid in order)
                    or len(order) != len(set(order)) or ESCALATION_STRATEGY in order):
                raise ContractError("invalid expert order")
            if any(sid not in identities or not current.get(sid).ranked_for_resolution
                   or current.get(sid).status != "active" for sid in order):
                raise ContractError("expert order references an unavailable style")

    @classmethod
    def from_dict(cls, payload: dict) -> CatalogueBundle:
        return _strict(cls, payload)

    def to_dict(self) -> dict:
        return asdict(self)


def prepare_bundle(catalogue: Catalogue, priors: dict, decision: CatalogueDecision) -> CatalogueBundle:
    change = decision.change
    if not isinstance(change, StyleChange) or change.action == "cancel":
        raise ContractError("a style mutation is required")
    data = catalogue.to_dict()
    sid = "STYLE-" + decision.decision_ref.removeprefix("GB-") if change.action == "add" else change.strategy_id
    if sid == ESCALATION_STRATEGY:
        raise ContractError("human escalation cannot be edited or removed")
    old = next((item for item in data["strategies"] if item["strategy_id"] == sid), None)
    if change.action != "add" and (old is None or old["status"] != "active"):
        raise ContractError("choose an existing active style")
    if old is not None:
        if not any((item["strategy_id"], item["version"]) == (sid, old["version"])
                   for item in data["retired_versions"]):
            data["retired_versions"].append(deepcopy(old))
        data["strategies"].remove(old)
    if change.action == "remove":
        if old is None:
            raise ContractError("choose an existing style")
        data["strategies"].append({**old, "status": "retired"})
    else:
        pattern = Catalogue.load().get(change.pattern_id)
        data["strategies"].append({
            **asdict(pattern), "strategy_id": sid, "version": decision.decision_ref,
            "name": change.name.strip(), "eligible_journey_states": change.eligible_journey_states,
            "channels": change.channels, "accessibility_modes": change.accessibility_modes,
        })
    data["catalogue_version"] = "owner-" + decision.decision_ref.removeprefix("GB-")
    updated = Catalogue(data)
    default = [sid for sid in priors["default"] if updated.get(sid).status == "active"]
    default.extend(style.strategy_id for style in updated.all()
                   if style.status == "active" and style.ranked_for_resolution and style.strategy_id not in default)
    intents = {key.split("|")[1] for key in priors["rankings"]}
    orders = {}
    for stage, intent, channel, mode in product(sorted(stages()), sorted(intents), sorted(CHANNELS),
                                               sorted(ACCESSIBILITY_MODES)):
        key = RequestKey(stage, intent, channel, mode)
        eligible = {style.strategy_id for style in updated.rankable_for(key)}
        previous = priors["rankings"].get(key.id, default)
        orders[key.id] = [sid for sid in previous if sid in eligible]
        orders[key.id].extend(sid for sid in default if sid in eligible and sid not in orders[key.id])
    return CatalogueBundle(data, {"priors_version": data["catalogue_version"], "approved_ref": decision.decision_ref,
                                  "watermark": WATERMARK, "default": default, "rankings": orders})


@dataclass(frozen=True)
class CatalogueRelease:
    decision_ref: str
    bundle: dict
    signature: str
    signer: str

    def __post_init__(self):
        if (not isinstance(self.decision_ref, str) or not re.fullmatch(r"GB-[a-f0-9]{32}", self.decision_ref)
                or not isinstance(self.signature, str) or not self.signature or not isinstance(self.signer, str)):
            raise ContractError("invalid signed catalogue release")
        bundle = CatalogueBundle.from_dict(self.bundle)
        if bundle.priors["approved_ref"] != self.decision_ref:
            raise ContractError("catalogue approval does not match the recorded owner decision")

    def payload(self) -> bytes:
        return canonical_json({"kind": "catalogue-release-v1", "decision_ref": self.decision_ref, "bundle": self.bundle})

    def verify(self, signer: Signer) -> bool:
        return self.signer == signer.name and signer.verify(self.payload(), self.signature)

    @classmethod
    def from_dict(cls, payload: dict) -> CatalogueRelease:
        return _strict(cls, payload)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CataloguePublication:
    release: dict
    baseline: dict

    @classmethod
    def from_dict(cls, payload: dict) -> CataloguePublication:
        return _strict(cls, payload)


def sign_release(decision_ref: str, bundle: dict, signer: Signer, threshold_version: str) -> tuple[CatalogueRelease, RankingSnapshot]:
    unsigned = CatalogueRelease(decision_ref, bundle, "pending", signer.name)
    release = CatalogueRelease(decision_ref, bundle, signer.sign(unsigned.payload()), signer.name)
    approved = CatalogueBundle.from_dict(bundle)
    baseline = sign_snapshot(RankingSnapshot(
        "expert-priors-" + approved.priors["priors_version"], approved.catalogue["catalogue_version"],
        threshold_version, "expert-priors", "expert_priors", expert_rankings(Catalogue(approved.catalogue), approved.priors),
    ), signer)
    return release, baseline
