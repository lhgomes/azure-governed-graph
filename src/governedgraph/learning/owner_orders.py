"""P2 signs only an already-recorded human order; it cannot invent an owner decision."""
from __future__ import annotations

import logging
from collections.abc import Callable

from governedgraph.strategy_service.overrides import OwnerOverride
from governedgraph.strategy_service.snapshot import RankingSnapshot, Signer, sign_snapshot

log = logging.getLogger("governedgraph.learning")


def publish_owner_orders(service, signer: Signer, lease_check: Callable[[], None]) -> None:
    import httpx

    state = service.refresh()
    if state["frozen"]:
        return
    pending = [OwnerOverride.from_dict(item) for item in state.get("owner_overrides", [])
               if item["kind"] == "order" and item["snapshot"] is None]
    for item in pending:
        lease_check()
        baseline = state["current"]["rankings"].get(item.request_key) if state["current"] else None
        baseline_ids = [sid for sid, _ in baseline] if baseline is not None else service.priors["rankings"].get(item.request_key)
        if baseline_ids is None or set(item.order) != set(baseline_ids):
            raise ValueError("pending owner order is not a reorder of an approved situation")
        snapshot = sign_snapshot(RankingSnapshot(
            snapshot_id="owner-" + item.override_id, catalogue_version=service.catalogue.version,
            threshold_version=service.thresholds["threshold_version"], source_audit_id=item.override_id,
            tier="tier2_owner", rankings={item.request_key: [
                [sid, service.catalogue.get(sid).version] for sid in item.order]},
        ), signer)
        lease_check()
        try:
            service.activate_owner_order(item.override_id, snapshot)
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 409:
                raise
            log.warning("owner_order_publication_conflict", extra={"decision_ref": item.override_id})
        else:
            log.info("owner_order_published", extra={"decision_ref": item.override_id})
