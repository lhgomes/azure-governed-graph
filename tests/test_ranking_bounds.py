"""B5: bounded continuous ranking of already-approved strategies only."""
from governedgraph.learning.ranking_pipeline import bounded_reorder, compute_candidates, load_outcome_aggregates, load_thresholds, max_shift
from governedgraph.strategy_service.catalogue import Catalogue
from strategy_doubles import pinned_outcomes


def test_b3_b5_second_contact_has_separate_eligible_priors_without_first_contact_outcomes(tmp_path):
    from governedgraph.common.config import DATA_DIR
    from governedgraph.common.contracts import RequestKey
    from governedgraph.common.io import load_json
    from governedgraph.strategy_service.snapshot import expert_rankings

    catalogue = Catalogue.load()
    priors = load_json(DATA_DIR / "catalogue/expert_priors_v1.json")
    rankings = expert_rankings(catalogue, priors)
    evidence = load_outcome_aggregates(pinned_outcomes(tmp_path))
    for channel, mode in (("voice", "standard"), ("chat", "standard"), ("chat", "english")):
        key = RequestKey("second_contact", "transition_impact_question", channel, mode)
        eligible = {style.strategy_id for style in catalogue.rankable_for(key)}
        assert {sid for sid, _ in rankings[key.id]} == eligible == {"S1", "S2", "S3", "S4", "S5"}
        candidates = compute_candidates(catalogue, {key.id: priors["rankings"][key.id]}, evidence, load_thresholds())
        assert len(candidates) == 1 and candidates[0].candidate == candidates[0].previous
        assert catalogue.get("S6").eligible_for(key)
        assert catalogue.get("S6").ranked_for_resolution is False
    assert catalogue.historical("S5", "1.0") is not None
    assert not catalogue.is_active("S5", "1.0")


def test_b3_b5_expanded_coverage_preserves_approved_orders_without_borrowing_outcomes():
    from types import SimpleNamespace

    from governedgraph.common.config import DATA_DIR
    from governedgraph.common.contracts import RequestKey
    from governedgraph.common.io import load_json
    from governedgraph.learning.pipeline_steps import previous_rankings
    from governedgraph.learning.ranking_pipeline import StrategyEvidence
    from governedgraph.strategy_service.source_upgrade import load_upgrade

    catalogue = Catalogue.load()
    priors = load_json(DATA_DIR / "catalogue/expert_priors_v1.json")
    plan = load_upgrade()
    assert plan is not None
    old_key = "first_contact|transition_impact_question|voice|standard"
    new_keys = set(priors["rankings"]) - set(plan.source_priors["rankings"])
    assert "first_contact|general_question|voice|standard" in new_keys
    for key, order in plan.source_priors["rankings"].items():
        assert priors["rankings"][key] == order
    for key in new_keys:
        context = RequestKey(*key.split("|"))
        eligible = {style.strategy_id for style in catalogue.rankable_for(context)}
        approved = (plan.source_priors["rankings"]["second_contact|transition_impact_question|voice|standard"]
                    if context.journey_state == "second_contact" else plan.source_priors["default"])
        assert priors["rankings"][key] == [sid for sid in approved if sid in eligible]
    learned = list(reversed(priors["rankings"][old_key]))
    service = SimpleNamespace(priors=priors, current=SimpleNamespace(
        rankings={old_key: [[sid, catalogue.get(sid).version] for sid in learned]}))
    previous = previous_rankings(service)
    assert previous[old_key] == learned
    evidence = {old_key: {"S2": StrategyEvidence("S2", 900, 850, 50)}}
    candidates = compute_candidates(catalogue, previous, evidence, load_thresholds(), expert_priors=priors)
    assert {candidate.request_key for candidate in candidates} == set(priors["rankings"])
    for candidate in candidates:
        if candidate.request_key in new_keys:
            assert candidate.previous == candidate.candidate == priors["rankings"][candidate.request_key]
            assert candidate.shift == 0


def test_rank_shift_is_capped():
    prev = ["S1", "S5", "S2", "S4", "S3"]
    scores = {"S2": 0.9, "S5": 0.8, "S4": 0.7, "S1": 0.1, "S3": 0.05}
    out = bounded_reorder(prev, scores, max_shift=2)
    assert max_shift(prev, out) <= 2
    assert out[0] == "S2"  # S2 may move up exactly 2 places
    assert out.index("S1") == 2  # S1 wanted to fall to 4th but is capped at 2 places


def test_candidates_never_add_or_remove_strategies(tmp_path):
    cands = compute_candidates(Catalogue.load(), {"first_contact|transition_impact_question|voice|standard": ["S1", "S5", "S2", "S4", "S3"]},
                               load_outcome_aggregates(pinned_outcomes(tmp_path)), load_thresholds())
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
    from strategy_doubles import local_strategy_service, run_cycle

    service = local_strategy_service(tmp_path)
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
    from governedgraph.strategy_service.snapshot import sign_snapshot
    from strategy_doubles import local_strategy_service, run_cycle

    service = local_strategy_service(tmp_path, control_share=0)
    run_cycle(service)
    voice = "first_contact|transition_impact_question|voice|standard"
    chat = "first_contact|transition_impact_question|chat|standard"
    sparse = replace(service.current, snapshot_id="snap-sparse",
                     rankings={voice: service.current.rankings[voice]})
    service.load_snapshot(sign_snapshot(sparse, service.signer))
    evidence = load_outcome_aggregates(pinned_outcomes(tmp_path))
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


def test_b3_b5_english_chat_has_signed_approved_priors_without_borrowing_standard_outcomes(tmp_path, monkeypatch):
    from counsellor_doubles import install, new_session
    from governedgraph.learning.pipeline_steps import previous_rankings
    from strategy_doubles import local_strategy_service, run_cycle

    install(monkeypatch, tmp_path)
    service = local_strategy_service(tmp_path, control_share=0)
    key = "first_contact|transition_impact_question|chat|english"
    approved = service.priors["default"]
    assert previous_rankings(service)[key] == approved
    assert key not in load_outcome_aggregates(pinned_outcomes(tmp_path / "outcomes"))
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
