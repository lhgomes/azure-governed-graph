"""Role-isolated initial expert priors; never replaces learned or protective state."""
from __future__ import annotations

import argparse
import json
import os

from governedgraph.common.config import DATA_DIR, settings
from governedgraph.common.io import load_json
from governedgraph.strategy_service.catalogue import Catalogue
from governedgraph.strategy_service.service import StrategyService
from governedgraph.strategy_service.snapshot import RankingSnapshot, default_signer, expert_rankings, sign_snapshot
from governedgraph.strategy_service.state import BlobStateStore, StateConflict


def expert_snapshot(catalogue, priors: dict, thresholds: dict, signer) -> RankingSnapshot:
    if priors.get("watermark") != "ILLUSTRATIVE - synthetic data" or not priors.get("approved_ref"):
        raise ValueError("human-approved illustrative expert priors required")
    rankings = expert_rankings(catalogue, priors)
    return sign_snapshot(RankingSnapshot(
        snapshot_id="expert-priors-" + priors["priors_version"],
        catalogue_version=catalogue.version, threshold_version=thresholds["threshold_version"],
        source_audit_id="expert-priors", tier="expert_priors", rankings=rankings,
    ), signer)


def initialize(store, snapshot: RankingSnapshot, signer) -> dict:
    service = StrategyService(store=store, signer=signer)
    state, etag = store.read()
    if state is not None or etag is not None:
        service.read_state()  # Signature/schema failures cannot be mistaken for fresh state.
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
        return {"initialized": False, "snapshot_id": service._snapshot_id()}
    return {"initialized": True, "snapshot_id": snapshot.snapshot_id}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("sign", "initialize"))
    args = parser.parse_args()
    if args.role == "sign":
        snapshot = expert_snapshot(
            Catalogue.load(), load_json(DATA_DIR / "catalogue/expert_priors_v1.json"),
            load_json(DATA_DIR / "fairness/thresholds_v1.json"), default_signer(signing=True),
        )
        result = {"snapshot": snapshot.to_dict()}
    else:
        snapshot = RankingSnapshot.from_dict(json.loads(os.environ["GOVERNEDGRAPH_INITIAL_SNAPSHOT"]))
        result = initialize(BlobStateStore(settings.ranking_storage_url), snapshot, default_signer())
    print("GG_BOOTSTRAP=" + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
