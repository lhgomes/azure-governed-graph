"""Strategy Catalogue: approved, versioned explanation strategies S1-S6 (B3).

A new strategy or changed strategy version is ALWAYS a material change (B8, Tier 2).
This module only reads the catalogue; changes arrive as a new catalogue file
approved through plane P3.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from governedgraph.common.config import DATA_DIR
from governedgraph.common.contracts import RequestKey
from governedgraph.common.io import load_json

ESCALATION_STRATEGY = "S6"  # always available, never ranked for resolution (B9)


@dataclass(frozen=True)
class Strategy:
    strategy_id: str
    version: str
    name: str
    status: str
    template_id: str
    template_version: str
    teach_back: bool
    ranked_for_resolution: bool
    eligible_journey_states: tuple[str, ...]
    channels: tuple[str, ...]
    accessibility_modes: tuple[str, ...]

    def eligible_for(self, key: RequestKey) -> bool:
        states_ok = "*" in self.eligible_journey_states or key.journey_state in self.eligible_journey_states
        return (
            self.status == "active"
            and states_ok
            and key.channel in self.channels
            and key.accessibility_mode in self.accessibility_modes
        )


class Catalogue:
    def __init__(self, data: dict):
        self.version: str = data["catalogue_version"]
        self._by_id = {
            s["strategy_id"]: Strategy(
                strategy_id=s["strategy_id"],
                version=s["version"],
                name=s["name"],
                status=s["status"],
                template_id=s["template_id"],
                template_version=s["template_version"],
                teach_back=bool(s["teach_back"]),
                ranked_for_resolution=bool(s["ranked_for_resolution"]),
                eligible_journey_states=tuple(s["eligible_journey_states"]),
                channels=tuple(s["channels"]),
                accessibility_modes=tuple(s["accessibility_modes"]),
            )
            for s in data["strategies"]
        }

    @classmethod
    def load(cls, path: Path | None = None) -> "Catalogue":
        return cls(load_json(path or DATA_DIR / "catalogue" / "strategy_catalogue_v1.json"))

    def get(self, strategy_id: str) -> Strategy:
        return self._by_id[strategy_id]

    def is_active(self, strategy_id: str, version: str) -> bool:
        s = self._by_id.get(strategy_id)
        return bool(s and s.status == "active" and s.version == version)

    def all(self) -> list[Strategy]:
        return list(self._by_id.values())

    def rankable_for(self, key: RequestKey) -> list[Strategy]:
        return [s for s in self._by_id.values() if s.ranked_for_resolution and s.eligible_for(key)]
