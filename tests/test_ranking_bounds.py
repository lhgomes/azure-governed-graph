"""B5: bounded continuous ranking of already-approved strategies only."""
from governedgraph.learning.ranking_pipeline import bounded_reorder, compute_candidates, load_outcome_aggregates, load_thresholds, max_shift
from governedgraph.strategy_service.catalogue import Catalogue


def test_rank_shift_is_capped():
    prev = ["S1", "S5", "S2", "S4", "S3"]
    scores = {"S2": 0.9, "S5": 0.8, "S4": 0.7, "S1": 0.1, "S3": 0.05}
    out = bounded_reorder(prev, scores, max_shift=2)
    assert max_shift(prev, out) <= 2
    assert out[0] == "S2"  # S2 may move up exactly 2 places
    assert out.index("S1") == 2  # S1 wanted to fall to 4th but is capped at 2 places


def test_candidates_never_add_or_remove_strategies():
    cands = compute_candidates(Catalogue.load(), {"first_contact|transition_impact_question|voice|standard": ["S1", "S5", "S2", "S4", "S3"]},
                               load_outcome_aggregates(), load_thresholds())
    c = cands[0]
    assert set(c.candidate) == set(c.previous)
    assert "S6" not in c.candidate


def test_min_sample_keeps_prior():
    from governedgraph.learning.ranking_pipeline import StrategyEvidence, prior_rate, score_strategies

    s = score_strategies(["S1", "S2"], {"S2": StrategyEvidence("S2", 10, 10, 0)}, min_sample=300, pseudo_count=200)
    assert s["S2"] == prior_rate(2)


def test_runtime_aggregates_reject_cohort_columns(tmp_path):
    import pytest

    p = tmp_path / "bad.csv"
    p.write_text("request_key,strategy_id,strategy_version,interactions,resolved_first_contact,repeat_contact_14d,age_band\nk,S1,1.0,1,1,0,75+\n")
    with pytest.raises(ValueError, match="B7"):
        load_outcome_aggregates(p)


def test_sparse_snapshot_retains_learned_orders_and_explicit_empty_rankings(tmp_path):
    from dataclasses import replace

    from governedgraph.learning.pipeline_steps import previous_rankings
    from governedgraph.learning.run_cycle import run_cycle
    from governedgraph.strategy_service.service import StrategyService

    service = StrategyService(state_dir=tmp_path)
    run_cycle(service)
    voice = "first_contact|transition_impact_question|voice|standard"
    chat = "first_contact|transition_impact_question|chat|standard"
    learned = list(reversed(service.current.rankings[voice]))
    service.current = replace(service.current, rankings={voice: learned, chat: []})
    previous = previous_rankings(service)
    assert previous[voice] == [sid for sid, _ in learned]
    assert previous[chat] == []
    assert set(previous) == set(service.priors["rankings"])
    previous[voice].clear()
    assert service.current.rankings[voice] == learned
    assert service.priors["rankings"][voice]


def test_sparse_snapshot_publishes_already_approved_chat_priors_without_outcomes(tmp_path):
    from dataclasses import replace

    from governedgraph.learning.pipeline_steps import previous_rankings
    from governedgraph.learning.run_cycle import run_cycle
    from governedgraph.strategy_service.service import StrategyService
    from governedgraph.strategy_service.snapshot import sign_snapshot

    service = StrategyService(state_dir=tmp_path, control_share=0)
    run_cycle(service)
    voice = "first_contact|transition_impact_question|voice|standard"
    chat = "first_contact|transition_impact_question|chat|standard"
    sparse = replace(service.current, snapshot_id="snap-sparse",
                     rankings={voice: service.current.rankings[voice]})
    service.load_snapshot(sign_snapshot(sparse, service.signer))
    evidence = load_outcome_aggregates()
    assert chat not in evidence
    approved = service.priors["rankings"][chat]
    baseline = previous_rankings(service)
    assert baseline[chat] == approved
    candidates = compute_candidates(service.catalogue, baseline, evidence, load_thresholds())
    candidate = next(c for c in candidates if c.request_key == chat)
    assert candidate.previous == candidate.candidate == approved
    assert candidate.shift == 0
    result = run_cycle(service)
    assert result.published
    served = service.get_ranked({
        "journey_id": "J-CHAT-APPROVED", "journey_state": "first_contact",
        "intent": "transition_impact_question", "channel": "chat", "accessibility_mode": "standard",
    })
    assert served.source == "snapshot"
    assert served.signature_valid
    assert [row.strategy_id for row in served.ranked] == [sid for sid in approved if sid != "S2"]
    assert "S2" in served.suspended


def test_b3_b5_english_chat_has_signed_approved_priors_without_borrowing_standard_outcomes(tmp_path):
    from governedgraph.agents.counsellor import new_session
    from governedgraph.learning.pipeline_steps import previous_rankings
    from governedgraph.learning.run_cycle import run_cycle
    from governedgraph.strategy_service.service import StrategyService

    service = StrategyService(state_dir=tmp_path, control_share=0)
    key = "first_contact|transition_impact_question|chat|english"
    approved = service.priors["default"]
    assert previous_rankings(service)[key] == approved
    assert key not in load_outcome_aggregates()
    result = run_cycle(service)
    assert result.published
    candidate = next(c for c in result.candidates if c.request_key == key)
    assert candidate.previous == candidate.candidate == approved
    assert candidate.shift == 0
    counsellor = new_session("MBR-9C1M", strategy_service=service, journey_id="J-ENGLISH-CHAT",
                            channel="chat", accessibility_mode="english")
    counsellor.start()
    answer = counsellor.handle("Can you explain the invaren letter in English please?")
    assert not answer.escalated
    assert answer.numeric_fidelity_passed
    assert counsellor.tools.trace.ranked.source == "snapshot"
    assert counsellor.tools.trace.ranked.signature_valid
    assert "S2" in counsellor.tools.trace.ranked.suspended
    assert "S2" not in answer.ranked
