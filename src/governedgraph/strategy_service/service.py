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
from dataclasses import asdict
from pathlib import Path
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
from governedgraph.common.io import load_json
from governedgraph.strategy_service.catalogue import ESCALATION_STRATEGY, Catalogue
from governedgraph.strategy_service.exploration import explore
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
        self.thresholds = load_json(DATA_DIR / "fairness" / "thresholds_v1.json")
        self.signer = signer or default_signer()
        self.state_dir = state_dir or settings.run_dir / "strategy_service"
        self.control_share = settings.control_arm_share if control_share is None else control_share
        self.current: RankingSnapshot | None = None
        self.last_good: RankingSnapshot | None = None
        self.static: RankingSnapshot | None = None
        self.static_override = False
        self.frozen = False
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

        self._mutate(update, "freeze")

    def revert_to_static(self) -> None:
        """Kill switch (B10): freeze learning and serve verified expert priors where available."""
        def update(state: dict) -> None:
            state["current"] = None
            state["frozen"] = True
            state["static_override"] = True

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
        if not self.catalogue.is_active(event.strategy_id, event.strategy_version):
            raise ValueError("suspension must reference an active catalogue version")

        def update(state: dict) -> None:
            overlay = {(e["strategy_id"], e["journey_state"]): e for e in state["suspensions"]}
            overlay[(event.strategy_id, event.journey_state)] = asdict(event)
            state["suspensions"] = list(overlay.values())

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
        return sorted(sid for (sid, state) in self._suspensions if state in (journey_state, "*"))

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
        static_authorized = (
            arm == "treatment" and self._fresh and self.frozen and self.static_override
            and self.current is None and self.static is not None and key.id in self.static.rankings
        )
        if arm == "treatment" and self._fresh and self.current is not None and key.id in self.current.rankings:
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
        exploration = False
        if (self.exploration_enabled and source == "snapshot" and not self.frozen and self.current is not None
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
        return state

    def _validate_state(self, state: dict) -> tuple[RankingSnapshot | None, RankingSnapshot | None,
                                                   RankingSnapshot | None, dict]:
        required = {"current", "last_good", "frozen", "suspensions"}
        if (not required <= set(state) or set(state) - required - {"static", "static_override"}
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
        suspensions = {}
        for entry in state["suspensions"]:
            event = SuspensionEvent.from_dict(entry)
            suspensions[(event.strategy_id, event.journey_state)] = event
        return snapshots[0], snapshots[1], static, suspensions

    def _apply_state(self, state: dict, etag: str | None) -> None:
        current, last_good, static, suspensions = self._validate_state(state)
        self.current, self.last_good = current, last_good
        self.static = static
        self._suspensions, self.frozen = suspensions, state["frozen"]
        self.static_override = state.get("static_override", False)
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
