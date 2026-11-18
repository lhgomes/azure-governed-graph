"""Role-isolated initialization and explicitly approved source-catalogue upgrades."""
from __future__ import annotations

import argparse
import json
import os
from copy import deepcopy
from dataclasses import asdict

from governedgraph.common.config import DATA_DIR, settings
from governedgraph.common.io import load_json
from governedgraph.strategy_service.catalogue import Catalogue
from governedgraph.strategy_service.service import SnapshotRejected, StrategyService
from governedgraph.strategy_service.snapshot import (
    RankingSnapshot, default_signer, expert_rankings, sign_snapshot, verify_expert_snapshot,
)
from governedgraph.strategy_service.source_upgrade import SignedSourceUpgrade, SourceUpgrade, load_upgrade, sign_upgrade
from governedgraph.strategy_service.state import BlobStateStore, StateConflict, StateUnavailable


def expert_snapshot(catalogue, priors: dict, thresholds: dict, signer) -> RankingSnapshot:
    if priors.get("watermark") != "ILLUSTRATIVE - synthetic data" or not priors.get("approved_ref"):
        raise ValueError("human-approved illustrative expert priors required")
    rankings = expert_rankings(catalogue, priors)
    return sign_snapshot(RankingSnapshot(
        snapshot_id="expert-priors-" + priors["priors_version"],
        catalogue_version=catalogue.version, threshold_version=thresholds["threshold_version"],
        source_audit_id="expert-priors", tier="expert_priors", rankings=rankings,
    ), signer)


def initialize(store, snapshot: RankingSnapshot, signer, *, plan: SourceUpgrade | None = None,
               upgrade: SignedSourceUpgrade | None = None) -> dict:
    service = StrategyService(store=store, signer=signer, catalogue=Catalogue.load() if plan is None else None)
    state, etag = store.read()
    if state is not None or etag is not None:
        if not isinstance(state, dict) or not isinstance(etag, str) or not etag:
            raise StateUnavailable("existing strategy state has no valid conditional-write version")
        service.read_state()  # Signature/schema failures cannot be mistaken for fresh state.
        if service.catalogue.version != snapshot.catalogue_version:
            return upgrade_existing(store, state, etag, snapshot, signer, service, plan, upgrade)
        return {"initialized": False, "snapshot_id": service._snapshot_id()}
    service._verify(snapshot)
    service._verify_static(snapshot)
    state = service.read_state()
    state["current"] = snapshot.to_dict()
    state["static"] = snapshot.to_dict()
    try:
        store.write(state, None)
    except StateConflict:
        service.read_state()
        if service.catalogue.version != snapshot.catalogue_version:
            raise StateConflict("predecessor state appeared during initialization; retry a fresh signed upgrade")
        return {"initialized": False, "snapshot_id": service._snapshot_id()}
    return {"initialized": True, "snapshot_id": snapshot.snapshot_id}


def upgrade_existing(store, state: dict, etag: str, snapshot: RankingSnapshot, signer,
                     service: StrategyService, plan: SourceUpgrade | None,
                     upgrade: SignedSourceUpgrade | None) -> dict:
    if plan is None or upgrade is None or not upgrade.verify(plan, signer):
        raise SnapshotRejected("a matching P2-signed, human-approved source upgrade is required")
    plan.validate_target(DATA_DIR)
    service._validate_state(state)
    if ("catalogue_release" in state or "catalogue_pending" in state
            or state["frozen"] or state.get("static_override")
            or any(item["kind"] != "pause" for item in state.get("owner_overrides", []))):
        raise StateConflict("source upgrade blocked by frozen, owner-controlled or pending strategy state")
    if plan.predecessor_for(state) is None:
        raise SnapshotRejected("source upgrade does not match the verified predecessor state")
    if not verify_expert_snapshot(
            snapshot, Catalogue.load(), load_json(DATA_DIR / "catalogue/expert_priors_v1.json"),
            service.thresholds, signer):
        raise SnapshotRejected("source upgrade baseline is not the signed approved expert ranking")
    updated = deepcopy(state)
    updated["current"] = updated["static"] = snapshot.to_dict()
    updated["last_good"] = None
    service._validate_state(updated)
    archive = store.archive_upgrade({
        "kind": "source-catalogue-upgrade-v1", "watermark": plan.watermark,
        "plan": asdict(plan), "publication": asdict(upgrade), "previous_state": state,
        "target_snapshot": snapshot.to_dict(),
    })
    store.write(updated, etag)
    service._audit("source_catalogue_upgrade", snapshot_id=snapshot.snapshot_id,
                   governance_decision_ref=plan.decision_ref, previous_state_archive=archive)
    return {"initialized": False, "migrated": True, "snapshot_id": snapshot.snapshot_id,
            "previous_state_archive": archive, "decision_ref": plan.decision_ref}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("sign", "initialize", "sign-upgrade", "upgrade"))
    args = parser.parse_args()
    upgrading = args.role in {"sign-upgrade", "upgrade"}
    plan = load_upgrade() if upgrading else None
    if upgrading and plan is None:
        raise SnapshotRejected("explicit source upgrade requires a reviewed upgrade plan")
    if args.role in {"sign", "sign-upgrade"}:
        signer = default_signer(signing=True)
        snapshot = expert_snapshot(
            Catalogue.load(), load_json(DATA_DIR / "catalogue/expert_priors_v1.json"),
            load_json(DATA_DIR / "fairness/thresholds_v1.json"), signer,
        )
        result = {"snapshot": snapshot.to_dict()}
        if plan is not None:
            upgrade = sign_upgrade(plan, json.loads(os.environ["GOVERNEDGRAPH_SOURCE_UPGRADE_APPROVAL"]), signer)
            result["upgrade"] = asdict(upgrade)
    else:
        snapshot = RankingSnapshot.from_dict(json.loads(os.environ["GOVERNEDGRAPH_INITIAL_SNAPSHOT"]))
        upgrade = (SignedSourceUpgrade.from_dict(json.loads(os.environ["GOVERNEDGRAPH_SOURCE_UPGRADE_SIGNATURE"]))
                   if plan is not None else None)
        result = initialize(BlobStateStore(settings.ranking_storage_url), snapshot, default_signer(),
                            plan=plan, upgrade=upgrade)
    print("GG_BOOTSTRAP=" + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
