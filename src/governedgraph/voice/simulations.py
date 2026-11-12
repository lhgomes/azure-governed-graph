"""Dev-only synthetic scripts, projected without actor profiles or audit cohorts."""
from dataclasses import asdict, dataclass, field

from governedgraph.common import domain
from governedgraph.common.config import DATA_DIR
from governedgraph.common.contracts import ACCESSIBILITY_MODES, ContractError, _strict
from governedgraph.common.io import load_json
from governedgraph.orchestrator.store import validate_id

WATERMARK = "ILLUSTRATIVE - synthetic data"


@dataclass(frozen=True)
class Simulation:
    simulation_id: str
    display_name: str
    member_ref: str
    channel: str
    accessibility_mode: str
    opening: str
    teach_back_answer: str = ""
    follow_up_questions: list[str] = field(default_factory=list)
    closing: str = ""

    def __post_init__(self):
        if any(not isinstance(value, str) or len(value) > 2000
               for key, value in asdict(self).items() if key != "follow_up_questions"):
            raise ContractError("bounded synthetic simulation fields required")
        if (not isinstance(self.follow_up_questions, list) or len(self.follow_up_questions) > 2
                or any(not isinstance(question, str) or not question.strip() or len(question) > 2000
                       for question in self.follow_up_questions)):
            raise ContractError("bounded synthetic follow-up questions required")
        if self.follow_up_questions and not self.closing.strip():
            raise ContractError("synthetic follow-up scripts require an explicit closing")
        validate_id(self.simulation_id)
        if not self.display_name.strip() or not self.opening.strip():
            raise ContractError("simulation name and opening required")
        if self.member_ref not in domain.demo()["trace_members"]:
            raise ContractError("simulation member is outside the synthetic allowlist")
        if self.channel not in ("voice", "chat") or self.accessibility_mode not in ACCESSIBILITY_MODES:
            raise ContractError("unsupported simulation channel or accessibility mode")

    @classmethod
    def from_dict(cls, body: dict):
        return _strict(cls, body)

    def summary(self) -> dict:
        return {key: getattr(self, key) for key in (
            "simulation_id", "display_name", "channel", "accessibility_mode",
        )}

    def script(self) -> dict:
        return {key: getattr(self, key) for key in (
            "opening", "teach_back_answer", "follow_up_questions", "closing",
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
