"""Dev-only synthetic persona demos, projected without actor profiles, ages or audit cohorts."""
from dataclasses import dataclass, field

from governedgraph.agents.live_persona import PersonaCard
from governedgraph.common import domain
from governedgraph.common.config import DATA_DIR
from governedgraph.common.contracts import ACCESSIBILITY_MODES, ContractError, _strict
from governedgraph.common.io import load_json
from governedgraph.orchestrator.store import validate_id

WATERMARK = "ILLUSTRATIVE - synthetic data"


@dataclass(frozen=True)
class Simulation:
    """A demo persona played live by the persona agent; no scripted lines and no audit cohorts."""

    simulation_id: str
    display_name: str
    member_ref: str
    channel: str
    accessibility_mode: str
    persona: dict = field(default_factory=dict)

    def __post_init__(self):
        if any(not isinstance(value, str) or len(value) > 2000 for value in (
                self.simulation_id, self.display_name, self.member_ref, self.channel, self.accessibility_mode)):
            raise ContractError("bounded synthetic simulation fields required")
        validate_id(self.simulation_id)
        if not self.display_name.strip():
            raise ContractError("simulation name required")
        if self.member_ref not in domain.demo()["trace_members"]:
            raise ContractError("simulation member is outside the synthetic allowlist")
        if self.channel not in ("voice", "chat") or self.accessibility_mode not in ACCESSIBILITY_MODES:
            raise ContractError("unsupported simulation channel or accessibility mode")
        if not isinstance(self.persona, dict) or self.card().persona_id != self.simulation_id:
            raise ContractError("simulation requires its own closed persona card")
        card = self.card()
        if card.voice == (card.guide_voice or domain.demo()["persona_voices"]["counsellor"]):
            raise ContractError("a persona must not share the guide's voice")
        if card.language != "en" and self.channel == "voice":
            raise ContractError("voice transcription is configured for English; use chat for other languages")

    def card(self) -> PersonaCard:
        return PersonaCard.from_dict(self.persona)

    @property
    def opening(self) -> str:
        return self.card().opening

    @classmethod
    def from_dict(cls, body: dict):
        return _strict(cls, body)

    def summary(self) -> dict:
        return {key: getattr(self, key) for key in (
            "simulation_id", "display_name", "channel", "accessibility_mode",
        )}


def load_simulations() -> list[Simulation]:
    path = DATA_DIR / "demo" / "simulations.json"
    body = load_json(path)
    rows = body["simulations"]
    if WATERMARK not in body.get("watermark", "") or not isinstance(rows, list) or not rows:
        raise ContractError("watermarked synthetic simulations required")
    simulations = [Simulation.from_dict(row) for row in rows]
    if len({item.simulation_id for item in simulations}) != len(simulations):
        raise ContractError("duplicate synthetic simulation ID")
    return simulations


def select_simulation(simulation_id: str) -> Simulation:
    for simulation in load_simulations():
        if simulation.simulation_id == simulation_id:
            return simulation
    raise ContractError("unknown synthetic simulation")
