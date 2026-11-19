"""In-process replacements for every Azure dependency of the Counsellor.

The deployed runtime has no local mode; these doubles exist only so tests can exercise the host-owned guardrail
pipeline (gates, binding, audit, journey state) without Azure credentials, Foundry or network access.
"""
import json
import re
from dataclasses import asdict
from pathlib import Path

from opentelemetry.sdk.trace import TracerProvider

from governedgraph.agents import azure_safety, counsellor, decision_agents, foundry_tracing
from governedgraph.agents.counsellor import Counsellor
from governedgraph.common.config import DATA_DIR
from governedgraph.common.contracts import DecisionEvidence, IntentDecision, StrategyResponse, UnderstandingDecision
from governedgraph.orchestrator import service as orchestrator_service
from governedgraph.explainability.store import LocalExplainabilityStore, QueuedExplainabilityStore
from governedgraph.search.approved_content import ContentHit
from governedgraph.search.corpus import load_corpus
# Bound at import time to the real run_decision; import it here so a first import under a patch cannot capture a double.
from governedgraph.voice import warmup  # noqa: F401
from pension_wtp.member_values_api.data import get_member_values


class LocalValuesClient:
    def get(self, member_ref, authenticated_member_ref, *, journey_id="", trace_parent=""):
        return get_member_values(member_ref, authenticated_member_ref)


class LocalStrategyClient:
    def __init__(self, service):
        self.service = service

    def get_ranked(self, request: dict) -> StrategyResponse:
        return self.service.get_ranked(request)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-zA-Z]{3,}", text.lower()))


class LocalContentSearch:
    def __init__(self):
        rows = [asdict(d) for d in load_corpus((DATA_DIR / "content" / "approved_content.jsonl").read_text())]
        self.docs = [row for row in rows if row["status"] == "approved"]

    def search(self, query: str, journey_state: str, language: str = "en", top: int = 3) -> list[ContentHit]:
        wanted = _tokens(query)
        scored = []
        for doc in self.docs:
            if doc["language"] != language:
                continue
            if "*" not in doc["journey_states"] and journey_state not in doc["journey_states"]:
                continue
            scored.append((len(wanted & _tokens(doc["title"] + " " + doc["body"] + " " + doc.get("answers", ""))), doc))
        scored.sort(key=lambda item: (-item[0], item[1]["id"]))
        return [ContentHit(d["id"], d["content_id"], d["version"], d["title"], d["body"], d["language"])
                for score, d in scored[:top] if score > 0]


def pinned_search(*passage_ids: str) -> LocalContentSearch:
    """Search double that returns exactly these approved passages, for tests that are not about retrieval."""
    search = LocalContentSearch()
    search.docs = [doc for doc in search.docs if doc["id"] in passage_ids]
    if {doc["id"] for doc in search.docs} != set(passage_ids):
        raise AssertionError(f"passages are not approved: {sorted(set(passage_ids) - {d['id'] for d in search.docs})}")
    search.search = lambda query, journey_state, language="en", top=3: [
        ContentHit(d["id"], d["content_id"], d["version"], d["title"], d["body"], d["language"])
        for d in search.docs]
    return search


class LocalShields(azure_safety.AzurePromptShields):
    """Keeps the deterministic local screen and skips the Content Safety service call."""

    def __init__(self):
        super().__init__("https://content-safety.invalid")

    def _screen(self, utterance, authenticated_member_ref, documents):
        return self.local.screen(utterance, authenticated_member_ref)


class FixtureDecisionAgent:
    """Labelled synthetic decisions for exact utterances; never a semantic fallback."""

    def __init__(self, role: str):
        self.role = role
        self.agent_name, self.agent_version = f"offline-fixture-{role}", "fixture-1"

    def run(self, tools, utterance, context):
        fixtures = json.loads((DATA_DIR / "synthetic" / "decision_agent_fixtures.json").read_text())
        key = utterance.strip().casefold()
        row = next((row for row in fixtures[self.role] if key in {text.casefold() for text in row["texts"]}), None)
        if row is None:
            raise decision_agents.DecisionUnavailable("fixture agent cannot interpret an unseen example")
        parser = IntentDecision if self.role == "intent" else UnderstandingDecision
        decision = parser.from_dict(row["decision"])
        tools.trace.decision_evidence.append(
            DecisionEvidence(self.role, self.agent_name, self.agent_version, asdict(decision)))
        return decision


def install_tracing(monkeypatch) -> TracerProvider:
    """Tracing provider without an exporter; deployed tracing requires Entra-authenticated Application Insights."""
    provider = TracerProvider()
    monkeypatch.setattr(foundry_tracing, "configure_tracing", lambda: provider)
    return provider


def install(monkeypatch, run_dir: Path, *, template_composer: bool = True, fixture_decisions: bool = True) -> None:
    """Replace the Counsellor's Azure boundaries; optionally also the Foundry composer and decision agents."""
    monkeypatch.setattr(counsellor, "default_values_client", lambda member_token="": LocalValuesClient())
    monkeypatch.setattr(counsellor, "default_strategy_client", lambda service=None: LocalStrategyClient(service))
    monkeypatch.setattr(counsellor, "default_search", LocalContentSearch)

    def store(journey=None, enqueue=None):
        primary = LocalExplainabilityStore(Path(run_dir) / "explainability" / "records.jsonl")
        return QueuedExplainabilityStore(primary, enqueue) if enqueue is not None else primary

    monkeypatch.setattr(counsellor, "default_store", store)
    monkeypatch.setattr(orchestrator_service, "default_store", store)

    install_tracing(monkeypatch)
    original = Counsellor.__init__

    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.screen = LocalShields()
        self.screen.journey_id = self.journey.journey_id
        self.tools.prompt_screen = self.screen
        if template_composer:
            self.tools.model_deployment = self.tools.agent_name = "local-template-composer"
            self.tools.agent_version = counsellor.TEMPLATES["template_set_version"]

    monkeypatch.setattr(Counsellor, "__init__", init)
    if template_composer:
        monkeypatch.setattr(Counsellor, "_compose", lambda self, strategy, utterance: self._template_for(strategy))
    if fixture_decisions:
        monkeypatch.setattr(decision_agents, "run_decision", lambda role, tools, utterance, context:
                            FixtureDecisionAgent(role).run(tools, utterance, context))


def new_session(member_ref, *args, step_up_verified=True, **kwargs):
    """``counsellor.new_session`` with the step-up default the deterministic tests assume."""
    return counsellor.new_session(member_ref, *args, step_up_verified=step_up_verified, **kwargs)
