"""Test doubles for the Strategy Service and the P2 cycle; no Key Vault, Blob Storage or Strategy API is contacted."""
import csv
from pathlib import Path

from governedgraph.agents.fairness_explainability import FairnessExplainabilityAgent, run_cohort_audit
from governedgraph.common.config import DATA_DIR
from governedgraph.common.io import load_csv
from governedgraph.learning import assurance_committee
from governedgraph.learning.pipeline_steps import current_id, previous_rankings
from governedgraph.learning.ranking_pipeline import (
    build_snapshot, compute_candidates, load_outcome_aggregates, load_thresholds,
)
from governedgraph.learning.run_cycle import CycleResult
from governedgraph.strategy_service.service import StrategyService
from governedgraph.strategy_service.snapshot import LocalHmacSigner
from governedgraph.strategy_service.state import LocalStateStore

SIGNER = LocalHmacSigner("strategy-test-double")
OUTCOMES = DATA_DIR / "synthetic" / "outcome_aggregates_illustrative.csv"
COHORTS = DATA_DIR / "fairness" / "cohort_aggregates_illustrative.csv"


def local_strategy_service(state_dir: Path, **kwargs) -> StrategyService:
    """Service with an HMAC signer and file state under ``state_dir`` instead of Azure state."""
    kwargs.setdefault("signer", SIGNER)
    return StrategyService(store=LocalStateStore(Path(state_dir) / "state.json"), **kwargs)


def pinned_to_active(service: StrategyService, source: Path, target: Path) -> Path:
    """Copy a synthetic aggregate fixture, re-pinning each row to the strategy version currently approved."""
    rows = load_csv(source)
    for row in rows:
        row["strategy_version"] = service.catalogue.get(row["strategy_id"]).version
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return target


def pinned_outcomes(directory: Path, catalogue=None) -> Path:
    """The synthetic outcome fixture pinned to the approved catalogue, for tests without a strategy service."""
    from types import SimpleNamespace

    from governedgraph.strategy_service.catalogue import Catalogue

    return pinned_to_active(SimpleNamespace(catalogue=catalogue or Catalogue.load()), OUTCOMES,
                            Path(directory) / "outcomes.csv")


def run_cycle(service: StrategyService, *, outcome_path: Path | None = None, cohort_path: Path | None = None) -> CycleResult:
    """In-process stand-in for the Azure P2 cycle: same bounded ranking, veto, signing and Tier 1 review."""
    thresholds = load_thresholds()
    previous, previous_id = previous_rankings(service), current_id(service)
    staging = Path(service.store.path).parent / "p2-inputs"
    evidence = load_outcome_aggregates(
        outcome_path or pinned_to_active(service, OUTCOMES, staging / "outcomes.csv"), catalogue=service.catalogue)
    audit = run_cohort_audit(thresholds, cohort_path or pinned_to_active(service, COHORTS, staging / "cohorts.csv"))
    candidates = compute_candidates(service.catalogue, previous, evidence, thresholds)
    governance = Path(service.store.path).parent / "governance"
    FairnessExplainabilityAgent(service, governance_dir=governance).apply_veto_gate(audit)
    snap = build_snapshot(service.catalogue, candidates, audit.audit_id, thresholds, service.signer)
    suspended = {c.request_key: service.suspended_for(c.request_key.split("|")[0]) for c in candidates}
    published = True
    if snap.tier == "tier1":
        published, _ = assurance_committee.review(snap, previous, service.catalogue, suspended,
                                                  thresholds["max_rank_shift_per_cycle"], evidence_writer=lambda _: None)
    if published:
        service.load_snapshot(snap, expected_snapshot_id=previous_id)
    steps = ["aggregate", "bounded-rank", "fairness-veto", "sign-govern-publish"]
    return CycleResult(candidates, audit, snap.snapshot_id, snap.tier, published, suspended, steps)


def import_strategy_api(monkeypatch, state_dir: Path):
    """Import the Strategy API with its module-level service backed by doubles and audit export disabled."""
    import importlib

    from governedgraph.strategy_service import service as service_module, telemetry

    monkeypatch.setattr(telemetry, "configure_audit", lambda: None)
    install_no_export_tracing(monkeypatch)
    monkeypatch.setattr(service_module, "default_signer", lambda **_: SIGNER)
    monkeypatch.setattr(service_module, "BlobStateStore", lambda *_: LocalStateStore(Path(state_dir) / "api-state.json"))
    return importlib.import_module("governedgraph.strategy_service.api")


def install_no_export_tracing(monkeypatch):
    """Tracer provider without an exporter; deployed tracing requires Entra-authenticated Application Insights."""
    from opentelemetry.sdk.trace import TracerProvider

    from governedgraph.agents import foundry_tracing

    provider = TracerProvider()
    monkeypatch.setattr(foundry_tracing, "configure_tracing", lambda: provider)
    return provider
