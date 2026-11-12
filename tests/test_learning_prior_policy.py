"""B5 regression contracts for the Azure-hosted validation job.

These test the learning policy with deterministic data but are executed only
inside the provisioned Azure test runner.
"""
from governedgraph.learning.ranking_pipeline import StrategyEvidence, compute_candidates
from governedgraph.learning.pipeline_steps import previous_rankings
from governedgraph.strategy_service.catalogue import Catalogue
from governedgraph.common.config import DATA_DIR
from governedgraph.common.io import load_json


def test_no_mature_evidence_keeps_the_previous_approved_order():
    catalogue = Catalogue.load()
    priors = load_json(DATA_DIR / "catalogue" / "expert_priors_v1.json")
    thresholds = load_json(DATA_DIR / "fairness" / "thresholds_v1.json")
    request_key = next(iter(priors["rankings"]))
    available = [sid for sid in priors["rankings"][request_key]
                 if sid in {s.strategy_id for s in catalogue.rankable_for(
                     __import__("governedgraph.common.contracts", fromlist=["RequestKey"]).RequestKey(
                         *request_key.split("|")))}]
    assert len(available) > 1
    previous = list(reversed(available))
    candidates = compute_candidates(catalogue, {request_key: previous}, {},
                                    thresholds, expert_priors=priors)
    assert candidates[0].candidate == previous
    assert candidates[0].shift == 0


def test_expert_prior_coverage_is_required():
    catalogue = Catalogue.load()
    priors = load_json(DATA_DIR / "catalogue" / "expert_priors_v1.json")
    thresholds = load_json(DATA_DIR / "fairness" / "thresholds_v1.json")
    request_key = next(iter(priors["rankings"]))
    broken = {**priors, "rankings": {**priors["rankings"], request_key: []}}
    try:
        compute_candidates(catalogue, {request_key: [priors["rankings"][request_key][0]]}, {},
                           thresholds, expert_priors=broken)
    except ValueError as error:
        assert "expert priors" in str(error)
    else:
        raise AssertionError("missing expert prior coverage was accepted")
