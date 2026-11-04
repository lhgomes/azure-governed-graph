"""B6 + worked example: S2 suspended (gap 26 pts), S5 promoted to rank 1."""
from governedgraph.agents.fairness_explainability import FairnessExplainabilityAgent, run_cohort_audit
from governedgraph.learning.ranking_pipeline import load_thresholds
from governedgraph.learning.run_cycle import run_cycle
from governedgraph.strategy_service.service import StrategyService
from governedgraph.strategy_service.snapshot import LocalHmacSigner

KEY = "first_contact|transition_impact_question|voice|standard"


def test_audit_reproduces_worked_example():
    a = run_cohort_audit(load_thresholds())
    w = a.worst_by_strategy(KEY)
    assert (w["S2"].overall_fcr_pct, w["S2"].cohort_fcr_pct, w["S2"].gap_pts, w["S2"].breach) == (78.0, 52.0, 26.0, True)
    assert (w["S5"].gap_pts, w["S5"].breach) == (4.0, False)
    assert [v[1] for v in a.vetoes] == ["S2"]


def test_small_cells_are_suppressed(tmp_path):
    t = {**load_thresholds(), "min_cell_size": 10_000}
    assert run_cohort_audit(t).findings == []


def test_veto_suspends_for_all_and_opens_case(tmp_path):
    svc = StrategyService(signer=LocalHmacSigner("t"), state_dir=tmp_path / "s", control_share=0.0)
    agent = FairnessExplainabilityAgent(svc, governance_dir=tmp_path / "g")
    events = agent.apply_veto_gate(run_cohort_audit(load_thresholds()))
    assert [e.strategy_id for e in events] == ["S2"]
    assert (tmp_path / "g" / "cases.jsonl").read_text().count("automatic_suspension") == 1
    assert not hasattr(agent, "reinstate") and not hasattr(agent, "promote")


def test_end_to_end_cycle_serves_s5_first(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVERNEDGRAPH_RUN_DIR", str(tmp_path))
    svc = StrategyService(state_dir=tmp_path / "s", control_share=0.0)
    r = run_cycle(svc)
    assert r.published and r.tier == "tier1"
    served = svc.get_ranked({"journey_id": "J-x", "journey_state": "first_contact", "intent": "transition_impact_question", "channel": "voice"})
    assert served.ranked[0].strategy_id == "S5" and served.suspended == ["S2"]
