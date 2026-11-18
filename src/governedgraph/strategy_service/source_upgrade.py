"""Explicit source-pack compatibility and human-approved, P2-signed upgrades."""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from governedgraph.common.config import DATA_DIR
from governedgraph.common.contracts import ContractError, RequestKey, _strict
from governedgraph.common.io import canonical_json, load_json
from governedgraph.strategy_service.catalogue import Catalogue, ESCALATION_STRATEGY, Strategy
from governedgraph.strategy_service.snapshot import RankingSnapshot, Signer

WATERMARK = "ILLUSTRATIVE - synthetic data"


@dataclass(frozen=True)
class SourceUpgrade:
    source_catalogue: dict
    source_priors: dict
    target_catalogue_sha256: str
    target_priors_sha256: str
    decision_ref: str
    evidence_ref: str
    policy: str
    watermark: str

    def __post_init__(self):
        if (self.watermark != WATERMARK or self.policy != "approved_priors"
                or not isinstance(self.decision_ref, str)
                or not re.fullmatch(r"(?:HR|GB)-[A-Za-z0-9][A-Za-z0-9._-]{0,127}", self.decision_ref)
                or not isinstance(self.evidence_ref, str) or not self.evidence_ref.strip()
                or len(self.evidence_ref) > 256
                or any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
                       for value in (self.target_catalogue_sha256, self.target_priors_sha256))):
            raise ContractError("explicit human-approved source upgrade required")
        if (not isinstance(self.source_catalogue, dict)
                or set(self.source_catalogue) != {"catalogue_version", "strategies", "retired_versions"}
                or not isinstance(self.source_catalogue["catalogue_version"], str)
                or not self.source_catalogue["catalogue_version"]):
            raise ContractError("closed predecessor catalogue required")
        strategy_fields = {item.name for item in fields(Strategy)}
        for collection in ("strategies", "retired_versions"):
            rows = self.source_catalogue[collection]
            if not isinstance(rows, list) or not rows or len(rows) > 10000:
                if collection == "retired_versions" and rows == []:
                    continue
                raise ContractError("bounded predecessor catalogue required")
            for row in rows:
                if (not isinstance(row, dict) or set(row) != strategy_fields
                        or any(type(row[key]) is not bool for key in ("teach_back", "ranked_for_resolution"))):
                    raise ContractError("unknown or invalid predecessor strategy fields")
        previous = Catalogue(self.source_catalogue)
        if canonical_json(previous.to_dict()) != canonical_json(self.source_catalogue):
            raise ContractError("predecessor catalogue must be canonical and distinct")
        priors = self.source_priors
        if (not isinstance(priors, dict)
                or set(priors) != {"priors_version", "approved_ref", "watermark", "default", "rankings"}
                or priors["watermark"] != WATERMARK
                or not isinstance(priors["priors_version"], str) or not priors["priors_version"]
                or not isinstance(priors["approved_ref"], str) or not priors["approved_ref"]
                or not isinstance(priors["rankings"], dict)):
            raise ContractError("closed predecessor expert priors required")
        for key, order in [("", priors["default"]), *priors["rankings"].items()]:
            if not isinstance(key, str) or (key and len(key.split("|")) != 4):
                raise ContractError("invalid predecessor situation")
            eligible = {style.strategy_id for style in previous.all()
                        if style.status == "active" and style.ranked_for_resolution}
            if key:
                eligible = {style.strategy_id for style in previous.rankable_for(RequestKey(*key.split("|")))}
            if (not isinstance(order, list) or any(not isinstance(sid, str) for sid in order)
                    or len(order) != len(set(order)) or not set(order) <= eligible
                    or ESCALATION_STRATEGY in order):
                raise ContractError("invalid predecessor expert order")

    @classmethod
    def from_dict(cls, payload: dict) -> SourceUpgrade:
        return _strict(cls, payload)

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json(asdict(self))).hexdigest()

    def validate_target(self, directory: Path) -> None:
        catalogue = load_json(directory / "catalogue/strategy_catalogue_v1.json")
        priors = load_json(directory / "catalogue/expert_priors_v1.json")
        if (hashlib.sha256(canonical_json(catalogue)).hexdigest() != self.target_catalogue_sha256
                or hashlib.sha256(canonical_json(priors)).hexdigest() != self.target_priors_sha256
                or priors.get("approved_ref") != self.decision_ref
                or catalogue.get("watermark") != WATERMARK or priors.get("watermark") != WATERMARK
                or catalogue["catalogue_version"] == self.source_catalogue["catalogue_version"]):
            raise ContractError("source upgrade does not match the exact approved target pack")
        target = Catalogue(catalogue)
        previous = Catalogue(self.source_catalogue)
        for style in previous.all():
            historical = target.historical(style.strategy_id, style.version)
            if historical is None or canonical_json(asdict(historical)) != canonical_json(asdict(style)):
                raise ContractError("target must retain every predecessor strategy definition")
        if asdict(target.get(ESCALATION_STRATEGY)) != asdict(previous.get(ESCALATION_STRATEGY)):
            raise ContractError("source upgrade cannot change human escalation")

    def validate_approval(self, approval: dict) -> None:
        if (not isinstance(approval, dict) or approval.get("tier") != "tier2"
                or approval.get("approved") is not True
                or approval.get("decision_ref") != self.decision_ref
                or approval.get("evidence_ref") != self.evidence_ref):
            raise ContractError("source upgrade requires the matching explicit human release approval")

    def predecessor_for(self, state: dict) -> tuple[Catalogue, dict] | None:
        versions = {RankingSnapshot.from_dict(state[field]).catalogue_version
                    for field in ("current", "last_good", "static") if state.get(field) is not None}
        if versions == {self.source_catalogue["catalogue_version"]}:
            return Catalogue(self.source_catalogue), self.source_priors
        return None


@dataclass(frozen=True)
class SignedSourceUpgrade:
    plan_sha256: str
    signature: str
    signer: str

    def __post_init__(self):
        if (not isinstance(self.plan_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", self.plan_sha256)
                or not isinstance(self.signature, str) or not self.signature
                or not isinstance(self.signer, str) or not self.signer):
            raise ContractError("invalid signed source upgrade")

    def payload(self) -> bytes:
        return canonical_json({"kind": "source-catalogue-upgrade-v1", "plan_sha256": self.plan_sha256})

    def verify(self, plan: SourceUpgrade, signer: Signer) -> bool:
        return (self.plan_sha256 == plan.digest and self.signer == signer.name
                and signer.verify(self.payload(), self.signature))

    @classmethod
    def from_dict(cls, payload: dict) -> SignedSourceUpgrade:
        return _strict(cls, payload)


def load_upgrade(directory: Path = DATA_DIR) -> SourceUpgrade | None:
    path = directory / "catalogue/source_upgrade_v1.json"
    if not path.exists():
        return None
    plan = SourceUpgrade.from_dict(load_json(path))
    plan.validate_target(directory)
    return plan


def sign_upgrade(plan: SourceUpgrade, approval: dict, signer: Signer) -> SignedSourceUpgrade:
    plan.validate_approval(approval)
    unsigned = SignedSourceUpgrade(plan.digest, "pending", signer.name)
    return SignedSourceUpgrade(plan.digest, signer.sign(unsigned.payload()), signer.name)
