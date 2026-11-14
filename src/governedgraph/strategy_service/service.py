"""Strategy Service: low-latency runtime source of ranking.

Runtime NEVER queries the graph. It serves the current signed snapshot with:
  * signature + active-catalogue verification at load (B3)
  * suspension overlay applied at read time, for ALL members (B6)
  * randomised control arm served the static expert priors (B10)
  * fallback to expert priors when no valid snapshot exists
  * S6 (human advisor) always available (B9)
  * last good snapshot retained; freeze/rollback as a protective action
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4
from threading import RLock
from collections.abc import Callable

from governedgraph.common.config import DATA_DIR, settings
from governedgraph.common.contracts import (
    RankedStrategy,
    RequestKey,
    StrategyRequest,
    StrategyResponse,
    SuspensionEvent,
)
from governedgraph.common.io import canonical_json, load_json
from governedgraph.strategy_service.catalogue import ESCALATION_STRATEGY, Catalogue
from governedgraph.strategy_service.catalogue_release import (
    CatalogueBundle, CatalogueDecision, CatalogueRelease, StyleChange, prepare_bundle,
)
from governedgraph.strategy_service.exploration import explore
from governedgraph.strategy_service.overrides import (
    OwnerDecision, OwnerOverride, effective_suspensions, state_revision, suspension_digest,
)
from governedgraph.strategy_service.snapshot import (
    RankingSnapshot, SignatureUnavailable, Signer, default_signer, verify_expert_snapshot, verify_snapshot,
)
from governedgraph.strategy_service.state import BlobStateStore, StateConflict, StateStore, StateUnavailable

log = logging.getLogger("governedgraph.strategy_service")


class SnapshotRejected(Exception):
    pass


def assign_arm(journey_id: str, control_share: float) -> str:
    """Deterministic, uniform randomisation by journey ID (no member attributes)."""
    bucket = int(hashlib.sha256(journey_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "control" if bucket < control_share else "treatment"


class StrategyService:
    def __init__(
        self,
        catalogue: Catalogue | None = None,
        priors: dict | None = None,
        signer: Signer | None = None,
        state_dir: Path | None = None,
        control_share: float | None = None,
        store: StateStore | None = None,
        exploration_enabled: bool = False,
    ):
        if not isinstance(exploration_enabled, bool):
            raise ValueError("exploration activation must be a boolean")
        self.exploration_enabled = exploration_enabled
        self.catalogue = catalogue or Catalogue.load()
        self.priors = priors or load_json(DATA_DIR / "catalogue" / "expert_priors_v1.json")
        self._initial_catalogue, self._initial_priors = self.catalogue, self.priors
        self.catalogue_release: dict | None = None
        self.catalogue_pending: dict | None = None
        self.thresholds = load_json(DATA_DIR / "fairness" / "thresholds_v1.json")
        self.signer = signer or default_signer()
        self.state_dir = state_dir or settings.run_dir / "strategy_service"
        self.control_share = settings.control_arm_share if control_share is None else control_share
        self.current: RankingSnapshot | None = None
        self.last_good: RankingSnapshot | None = None
        self.static: RankingSnapshot | None = None
        self.static_override = False
        self.frozen = False
        self.owner_overrides: list[OwnerOverride] = []
        self.owner_revision = ""
        self._suspensions: dict[tuple[str, str], SuspensionEvent] = {}
        self._lock = RLock()
        self._etag: str | None = None
        self._fresh = True
        self.store = store or BlobStateStore(settings.ranking_storage_url)
        self._refresh_on_read = True
        self._refresh()

    # ---------- snapshot lifecycle ----------
    def load_snapshot(self, snapshot: RankingSnapshot, *, expected_snapshot_id: str | None = None,
                      static_snapshot: RankingSnapshot | None = None) -> None:
        self._verify(snapshot)
        if static_snapshot is not None:
            self._verify_static(static_snapshot)

        def update(state: dict) -> None:
            if state["frozen"]:
                raise SnapshotRejected("ranking is frozen to the last good snapshot (protective action)")
            current_id = (state["current"]["snapshot_id"] if state["current"] is not None
                          else "expert-priors-" + self.priors["priors_version"])
            if expected_snapshot_id is not None and current_id != expected_snapshot_id:
                raise SnapshotRejected("ranking baseline changed; retry a fresh bounded cycle")
            if state["current"] is not None:
                state["last_good"] = state["current"]
            if static_snapshot is not None:
                state["static"] = static_snapshot.to_dict()
            state["current"] = snapshot.to_dict()

        self._mutate(update, "snapshot_load")

    def _verify(self, snapshot: RankingSnapshot) -> None:
        if not verify_snapshot(snapshot, self.signer):
            raise SnapshotRejected("signature verification failed")
        if snapshot.catalogue_version != self.catalogue.version:
            raise SnapshotRejected(
                f"catalogue version mismatch {snapshot.catalogue_version} != {self.catalogue.version}"
            )
        for key_id, ranked in snapshot.rankings.items():
            for sid, ver in ranked:
                if not self.catalogue.is_active(sid, ver):
                    raise SnapshotRejected(f"{key_id}: references non-active strategy {sid} v{ver}")

    def _verify_static(self, snapshot: RankingSnapshot) -> None:
        if not verify_expert_snapshot(snapshot, self.catalogue, self.priors, self.thresholds, self.signer):
            raise SnapshotRejected("static baseline is not the signed approved expert ranking")

    def freeze(self) -> None:
        """Any agent may freeze ranking to the last good snapshot (protective, always autonomous)."""
        def update(state: dict) -> None:
            if not state["frozen"] and state["last_good"] is not None:
                state["current"] = state["last_good"]
            state["frozen"] = True
            state["owner_overrides"] = [item for item in state.get("owner_overrides", []) if item["kind"] == "pause"]
            if not self.owner_revision and not state["owner_overrides"]:
                state.pop("owner_overrides", None)

        self._mutate(update, "freeze")

    def revert_to_static(self) -> None:
        """Kill switch (B10): freeze learning and serve verified expert priors where available."""
        def update(state: dict) -> None:
            state["current"] = None
            state["frozen"] = True
            state["static_override"] = True
            state["owner_overrides"] = [item for item in state.get("owner_overrides", []) if item["kind"] == "pause"]
            if not self.owner_revision and not state["owner_overrides"]:
                state.pop("owner_overrides", None)

        self._mutate(update, "freeze_static")
        if self.static is None or not self.static.rankings:
            log.error("strategy_static_baseline_unavailable")

    def frozen_revision(self) -> str:
        with self._lock:
            self._refresh(required=True)
            if not self.frozen or not self._etag:
                raise StateConflict("recovery requires an existing frozen state")
            return self._etag

    def release_freeze(self, expected_revision: str, **audit_details: str) -> None:
        """Human recovery changes no rankings or suspensions; a concurrent protection always wins."""
        def update(state: dict) -> None:
            if not expected_revision or self._etag != expected_revision or not state["frozen"]:
                raise StateConflict("strategy state changed; human recovery must be reviewed again")
            state["frozen"] = False
            state.pop("static_override", None)

        self._mutate(update, "human_freeze_release", **audit_details)

    # ---------- suspension overlay (B6) ----------
    def suspend(self, event: SuspensionEvent) -> None:
        if self.catalogue.historical(event.strategy_id, event.strategy_version) is None:
            raise ValueError("suspension must reference an approved catalogue version")

        def update(state: dict) -> None:
            overlay = {(e["strategy_id"], e["journey_state"]): e for e in state["suspensions"]}
            overlay[(event.strategy_id, event.journey_state)] = asdict(event)
            state["suspensions"] = list(overlay.values())
            if "owner_overrides" in state:
                state["owner_overrides"] = [item for item in state["owner_overrides"] if not (
                    item["kind"] == "unpause" and item["strategy_id"] == event.strategy_id
                    and (event.journey_state == "*" or item["journey_state"] in ("*", event.journey_state)))]

        self._mutate(update, "suspension", strategy_id=event.strategy_id, journey_state=event.journey_state)

    def reinstate(self, strategy_id: str, journey_state: str, governance_decision_ref: str) -> None:
        """Tier 2 only: requires a recorded governance board decision (B8)."""
        if not governance_decision_ref.startswith("GB-"):
            raise PermissionError("reinstatement requires a governance board decision reference (Tier 2)")
        def update(state: dict) -> None:
            state["suspensions"] = [
                e for e in state["suspensions"]
                if (e["strategy_id"], e["journey_state"]) != (strategy_id, journey_state)
            ]

        self._mutate(update, "reinstatement", governance_decision_ref=governance_decision_ref)

    def suspended_for(self, journey_state: str) -> list[str]:
        return effective_suspensions({
            "frozen": self.frozen or not self._fresh,
            "suspensions": [asdict(event) for event in self._suspensions.values()],
            "owner_overrides": [item.to_dict() for item in self.owner_overrides if item.kind != "order"],
        }, journey_state)

    def owner_decision(self, decision: OwnerDecision, actor: str) -> dict:
        result: dict = {}

        def update(state: dict) -> None:
            if state_revision(state) != decision.expected_revision:
                raise StateConflict("strategy state changed; refresh and confirm the owner decision again")
            entries = [OwnerOverride.from_dict(item) for item in state.get("owner_overrides", [])]
            if decision.action == "revoke":
                if not any(item.override_id == decision.override_id for item in entries):
                    raise ValueError("the override is no longer active or pending")
                entries = [item for item in entries if item.override_id != decision.override_id]
            else:
                if decision.action in {"order", "unpause"} and state["frozen"]:
                    raise ValueError("protective freeze is active; use the human recovery workflow first")
                if decision.action == "order":
                    key = decision.request_key
                    baseline = (state["current"]["rankings"].get(key) if state["current"] else None)
                    baseline_ids = [sid for sid, _ in baseline] if baseline is not None else self.priors["rankings"].get(key)
                    if baseline_ids is None or set(decision.order) != set(baseline_ids):
                        raise ValueError("an owner order must only reorder the existing approved situation")
                    if ESCALATION_STRATEGY in decision.order:
                        raise ValueError("human escalation is never ranked")
                    item = OwnerOverride(decision.decision_ref, "order", request_key=key, order=decision.order)
                else:
                    strategy = self.catalogue.get(decision.strategy_id)
                    if strategy.strategy_id == ESCALATION_STRATEGY or strategy.status != "active":
                        raise ValueError("owner pause decisions require an active non-escalation style")
                    known_states = {key.split("|")[0] for key in self.priors["rankings"]}
                    if (decision.journey_state not in known_states | {"*"} or (
                            "*" not in strategy.eligible_journey_states and decision.journey_state != "*"
                            and decision.journey_state not in strategy.eligible_journey_states)):
                        raise ValueError("the style is not approved for that journey stage")
                    targets = [suspension_digest(event) for event in state["suspensions"]
                               if event["strategy_id"] == decision.strategy_id
                               and (decision.journey_state == "*" or event["journey_state"] == decision.journey_state)]
                    if decision.action == "unpause" and not targets:
                        raise ValueError("no matching suspension remains; refresh the pause evidence")
                    item = OwnerOverride(decision.decision_ref, decision.action, decision.strategy_id,
                                         decision.journey_state, target_digests=targets if decision.action == "unpause" else [])
                if any(old.override_id == item.override_id or (
                    old.kind == item.kind and old.strategy_id == item.strategy_id
                    and old.journey_state == item.journey_state and old.request_key == item.request_key) for old in entries):
                    raise ValueError("an override already exists for this scope; revoke it before replacing it")
                entries.append(item)
            state["owner_overrides"] = [item.to_dict() for item in entries]
            state["owner_revision"] = uuid4().hex
            result.update({"decision_ref": decision.decision_ref,
                           "status": "pending_signature" if decision.action == "order" else "applied"})

        self._mutate(update, "owner_decision", actor=actor, governance_decision_ref=decision.decision_ref,
                     owner_action=decision.action)
        return result

    def activate_owner_order(self, override_id: str, snapshot: RankingSnapshot) -> None:
        self._verify(snapshot)

        def update(state: dict) -> None:
            if state["frozen"]:
                raise StateConflict("a protective freeze prevents owner-order activation")
            entries = state.get("owner_overrides", [])
            item = next((entry for entry in entries if entry["override_id"] == override_id), None)
            if item is None or item["kind"] != "order" or item["snapshot"] is not None:
                raise StateConflict("owner order was revoked, replaced or already signed")
            self._verify_owner_snapshot(OwnerOverride.from_dict(item), snapshot)
            item["snapshot"] = snapshot.to_dict()
            state["owner_revision"] = uuid4().hex

        self._mutate(update, "owner_order_activation", governance_decision_ref=override_id)

    def _verify_owner_snapshot(self, item: OwnerOverride, snapshot: RankingSnapshot) -> None:
        expected = {item.request_key: [[sid, self.catalogue.get(sid).version] for sid in item.order]}
        if (snapshot.rankings != expected or snapshot.source_audit_id != item.override_id
                or snapshot.snapshot_id != "owner-" + item.override_id
                or snapshot.tier != "tier2_owner" or snapshot.threshold_version != self.thresholds["threshold_version"]):
            raise SnapshotRejected("signed owner order does not match the recorded human decision")

    def catalogue_decision(self, decision: CatalogueDecision, actor: str) -> dict:
        def update(state: dict) -> None:
            if state_revision(state) != decision.expected_revision:
                raise StateConflict("strategy state changed; refresh the style change")
            change = decision.change
            if not isinstance(change, StyleChange):
                raise ValueError("closed style change required")
            if change.action == "cancel":
                if state.get("catalogue_pending") is None:
                    raise StateConflict("no catalogue publication is pending")
                state.pop("catalogue_pending")
            else:
                if state.get("catalogue_pending") is not None:
                    raise StateConflict("publish or cancel the pending catalogue change first")
                state["catalogue_pending"] = {
                    "decision_ref": decision.decision_ref, "base_version": self.catalogue.version,
                    "bundle": prepare_bundle(self.catalogue, self.priors, decision).to_dict(),
                }
            state["owner_revision"] = uuid4().hex
            state.setdefault("owner_overrides", [])
        self._mutate(update, "owner_catalogue_decision", actor=actor, governance_decision_ref=decision.decision_ref)
        return {"decision_ref": decision.decision_ref,
                "status": "applied" if decision.action == "catalogue_cancel" else "pending_signature"}

    def activate_catalogue(self, release: CatalogueRelease, baseline: RankingSnapshot) -> None:
        def update(state: dict) -> None:
            pending = state.get("catalogue_pending")
            if (state["frozen"] or pending is None or pending["decision_ref"] != release.decision_ref
                    or pending["base_version"] != self.catalogue.version
                    or canonical_json(pending["bundle"]) != canonical_json(release.bundle)):
                raise StateConflict("catalogue publication was cancelled, replaced, frozen or superseded")
            bundle = CatalogueBundle.from_dict(release.bundle)
            if not release.verify(self.signer) or not verify_expert_snapshot(
                    baseline, Catalogue(bundle.catalogue), bundle.priors, self.thresholds, self.signer):
                raise SnapshotRejected("catalogue or expert baseline signature rejected")
            state["catalogue_release"] = release.to_dict()
            state.pop("catalogue_pending")
            state["current"] = state["static"] = baseline.to_dict()
            state["last_good"] = None
            state.pop("static_override", None)
            state["owner_overrides"] = [item for item in state.get("owner_overrides", []) if item["kind"] == "pause"]
            state["owner_revision"] = uuid4().hex
        self._mutate(update, "owner_catalogue_publication", governance_decision_ref=release.decision_ref)

    def read_catalogue(self) -> dict:
        with self._lock:
            self._refresh(required=True)
            return {"catalogue": self.catalogue.to_dict(), "priors": self.priors}

    # ---------- runtime read path ----------
    def get_ranked(self, request: StrategyRequest | dict) -> StrategyResponse:
        if isinstance(request, dict):
            request = StrategyRequest.from_dict(request)  # rejects unknown/demographic fields (B7)
        with self._lock:
            if self._refresh_on_read:
                self._refresh()
            return self._rank(request)

    def _rank(self, request: StrategyRequest) -> StrategyResponse:
        key = request.key
        arm = assign_arm(request.journey_id, self.control_share)

        ranked_ids: list[str]
        source = "snapshot"
        snapshot_id = "expert-priors-" + self.priors["priors_version"]
        owner_order = next((item for item in self.owner_overrides
                            if item.kind == "order" and item.request_key == key.id and item.snapshot is not None), None)
        static_authorized = (
            arm == "treatment" and self._fresh and self.frozen and self.static_override
            and self.current is None and self.static is not None and key.id in self.static.rankings
        )
        if arm == "treatment" and self._fresh and not self.frozen and owner_order is not None and owner_order.snapshot is not None:
            ranked_ids = list(owner_order.order)
            snapshot_id = owner_order.snapshot["snapshot_id"]
        elif arm == "treatment" and self._fresh and self.current is not None and key.id in self.current.rankings:
            ranked_ids = [sid for sid, _ in self.current.rankings[key.id]]
            snapshot_id = self.current.snapshot_id
        elif static_authorized and self.static is not None:
            ranked_ids = [sid for sid, _ in self.static.rankings[key.id]]
            snapshot_id = self.static.snapshot_id
            source = "expert_priors_fallback"
        else:
            ranked_ids = self._priors_for(key)
            source = "expert_priors" if arm == "control" else "expert_priors_fallback"
        if source == "expert_priors_fallback":
            log.warning("strategy_static_fallback", extra={"journey_id": request.journey_id,
                                                           "snapshot_id": snapshot_id, "arm": arm,
                                                           "source": source, "frozen": self.frozen})

        eligible = {s.strategy_id for s in self.catalogue.rankable_for(key)}
        suspended = self.suspended_for(key.journey_state)
        final = [sid for sid in ranked_ids if sid in eligible and sid not in suspended]
        if not self._fresh:
            final = []
        exploration = False
        if (self.exploration_enabled and owner_order is None and source == "snapshot" and not self.frozen and self.current is not None
                and self.current.threshold_version == self.thresholds["threshold_version"]):
            final, exploration = explore(
                final, request.journey_id, key.id,
                self.thresholds["exploration_rate"], self.thresholds["max_rank_shift_per_cycle"],
            )
            if exploration:
                log.info("strategy_exploration", extra={"journey_id": request.journey_id,
                                                        "snapshot_id": snapshot_id, "request_key": key.id})
        ranked = [
            RankedStrategy(sid, self.catalogue.get(sid).version, i + 1) for i, sid in enumerate(final)
        ]
        return StrategyResponse(
            snapshot_id=snapshot_id,
            signature_valid=source == "snapshot" or static_authorized,
            catalogue_version=self.catalogue.version,
            arm=arm,
            ranked=ranked,
            suspended=suspended,
            always_available=[ESCALATION_STRATEGY],
            source=source,
            exploration=exploration,
            static_fallback_authorized=static_authorized,
        )

    def _priors_for(self, key: RequestKey) -> list[str]:
        return list(self.priors["rankings"].get(key.id, self.priors["default"]))

    def _state(self) -> dict:
        state = {
            "current": self.current.to_dict() if self.current else None,
            "last_good": self.last_good.to_dict() if self.last_good else None,
            "frozen": self.frozen,
            "suspensions": [asdict(e) for e in self._suspensions.values()],
        }
        if self.static is not None:
            state["static"] = self.static.to_dict()
        if self.static_override:
            state["static_override"] = True
        if self.owner_revision:
            state["owner_revision"] = self.owner_revision
            state["owner_overrides"] = [item.to_dict() for item in self.owner_overrides]
        if self.catalogue_release is not None:
            state["catalogue_release"] = self.catalogue_release
        if self.catalogue_pending is not None:
            state["catalogue_pending"] = self.catalogue_pending
        return state

    def _validate_state(self, state: dict) -> tuple[RankingSnapshot | None, RankingSnapshot | None,
                                                   RankingSnapshot | None, dict]:
        previous = self.catalogue, self.priors
        catalogue, priors = self._catalogue_for(state)
        try:
            self.catalogue, self.priors = catalogue, priors
            return self._validate_catalogued_state(state)
        finally:
            self.catalogue, self.priors = previous

    def _catalogue_for(self, state: dict) -> tuple[Catalogue, dict]:
        if "catalogue_release" not in state:
            return self._initial_catalogue, self._initial_priors
        release = CatalogueRelease.from_dict(state["catalogue_release"])
        if not release.verify(self.signer):
            raise SnapshotRejected("catalogue release signature verification failed")
        bundle = CatalogueBundle.from_dict(release.bundle)
        return Catalogue(bundle.catalogue), bundle.priors

    def _validate_catalogued_state(self, state: dict) -> tuple[RankingSnapshot | None, RankingSnapshot | None,
                                                             RankingSnapshot | None, dict]:
        required = {"current", "last_good", "frozen", "suspensions"}
        if (not required <= set(state) or set(state) - required - {
                "static", "static_override", "owner_overrides", "owner_revision", "catalogue_release", "catalogue_pending"}
                or not isinstance(state["frozen"], bool)
                or ("static_override" in state and (
                    state["static_override"] is not True or not state["frozen"] or state["current"] is not None
                ))):
            raise ValueError("invalid strategy state contract")
        snapshots = []
        for field in ("current", "last_good"):
            snapshot = RankingSnapshot.from_dict(state[field]) if state[field] is not None else None
            if snapshot is not None:
                self._verify(snapshot)
            snapshots.append(snapshot)
        static = RankingSnapshot.from_dict(state["static"]) if state.get("static") is not None else None
        if "static" in state and static is None:
            raise ValueError("static baseline must be a signed snapshot")
        if static is not None:
            self._verify_static(static)
        if "owner_overrides" in state or "owner_revision" in state:
            if (not isinstance(state.get("owner_revision"), str)
                    or not re.fullmatch(r"[a-f0-9]{32}", state["owner_revision"])
                    or not isinstance(state.get("owner_overrides"), list)):
                raise ValueError("invalid owner decision state")
            seen = set()
            seen_ids = set()
            for entry in state["owner_overrides"]:
                item = OwnerOverride.from_dict(entry)
                scope = (item.kind, item.strategy_id, item.journey_state, item.request_key)
                if scope in seen or item.override_id in seen_ids:
                    raise ValueError("duplicate owner override scope")
                seen.add(scope)
                seen_ids.add(item.override_id)
                if item.kind == "order":
                    baseline = state["current"]["rankings"].get(item.request_key) if state["current"] else None
                    baseline_ids = [sid for sid, _ in baseline] if baseline is not None else self.priors["rankings"].get(item.request_key)
                    if baseline_ids is None or set(item.order) != set(baseline_ids) or ESCALATION_STRATEGY in item.order:
                        raise ValueError("owner order must only reorder an approved situation")
                    for sid in item.order:
                        strategy = self.catalogue.get(sid)
                        if strategy.status != "active" or not strategy.ranked_for_resolution:
                            raise ValueError("owner order requires active rankable catalogue versions")
                else:
                    strategy = self.catalogue.get(item.strategy_id)
                    known_states = {key.split("|")[0] for key in self.priors["rankings"]}
                    if (item.strategy_id == ESCALATION_STRATEGY
                            or (strategy.status != "active" and item.kind != "pause")
                            or item.journey_state not in known_states | {"*"}):
                        raise ValueError("owner pause scope is not an approved non-escalation style")
                if item.snapshot is not None:
                    signed = RankingSnapshot.from_dict(item.snapshot)
                    self._verify(signed)
                    self._verify_owner_snapshot(item, signed)
        if "catalogue_pending" in state:
            pending = state["catalogue_pending"]
            if (not isinstance(pending, dict) or set(pending) != {"decision_ref", "base_version", "bundle"}
                    or pending["base_version"] != self.catalogue.version
                    or not isinstance(pending["decision_ref"], str)
                    or not re.fullmatch(r"GB-[a-f0-9]{32}", pending["decision_ref"])):
                raise ValueError("invalid pending catalogue publication")
            bundle = CatalogueBundle.from_dict(pending["bundle"])
            if bundle.priors["approved_ref"] != pending["decision_ref"]:
                raise ValueError("pending catalogue approval mismatch")
        suspensions = {}
        for entry in state["suspensions"]:
            event = SuspensionEvent.from_dict(entry)
            suspensions[(event.strategy_id, event.journey_state)] = event
        return snapshots[0], snapshots[1], static, suspensions

    def _apply_state(self, state: dict, etag: str | None) -> None:
        current, last_good, static, suspensions = self._validate_state(state)
        self.catalogue, self.priors = self._catalogue_for(state)
        self.catalogue_release = state.get("catalogue_release")
        self.catalogue_pending = state.get("catalogue_pending")
        self.current, self.last_good = current, last_good
        self.static = static
        self._suspensions, self.frozen = suspensions, state["frozen"]
        self.static_override = state.get("static_override", False)
        self.owner_overrides = [OwnerOverride.from_dict(item) for item in state.get("owner_overrides", [])]
        self.owner_revision = state.get("owner_revision", "")
        self._etag, self._fresh = etag, True

    def _refresh(self, *, required: bool = False) -> None:
        try:
            state, etag = self.store.read(self._etag if self._fresh else None)
            if state is not None:
                self._apply_state(state, etag)
                self._audit("snapshot_load")
            elif etag is None and self._etag is not None:
                raise StateUnavailable("previously observed strategy state disappeared")
            else:
                self._fresh = True
        except (StateUnavailable, SignatureUnavailable, SnapshotRejected, ValueError, TypeError, KeyError) as error:
            self._fresh = False
            log.error("strategy_state_alert", extra={"snapshot_id": self._snapshot_id(),
                                                     "error_type": type(error).__name__})
            if required:
                raise StateUnavailable("strategy state is unavailable or rejected") from error

    def _mutate(self, update: Callable[[dict], None], event: str, **details: str) -> None:
        with self._lock:
            for _ in range(5):
                self._refresh(required=True)
                state = self._state()
                update(state)
                self._validate_state(state)
                try:
                    etag = self.store.write(state, self._etag)
                except StateConflict:
                    self._fresh = False
                    log.warning("strategy_state_conflict", extra={"snapshot_id": self._snapshot_id()})
                    continue
                except StateUnavailable:
                    log.error("strategy_state_write_failed", extra={"snapshot_id": self._snapshot_id(),
                                                                    "action": event})
                    raise
                self._apply_state(state, etag)
                self._audit(event, **details)
                return
            raise StateConflict("strategy state remained contended after five attempts")

    def _snapshot_id(self) -> str:
        return self.current.snapshot_id if self.current else "expert-priors-" + self.priors["priors_version"]

    def read_state(self) -> dict:
        with self._lock:
            self._refresh(required=True)
            return self._state()

    def _audit(self, event: str, **details: str) -> None:
        log.info("strategy_" + event, extra={"snapshot_id": self._snapshot_id(), **details})
