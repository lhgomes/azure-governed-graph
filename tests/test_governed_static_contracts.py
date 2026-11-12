"""B3/B10 contract regressions; execute only on the Azure-hosted test runner.

An in-memory test double isolates the domain contract from Azure credentials.
The application itself has no local runtime state selection.
"""
from copy import deepcopy

import pytest

from governedgraph.common.contracts import ContractError, OutcomeSubmission, ResponseReference
from governedgraph.strategy_service.bootstrap import expert_snapshot
from governedgraph.strategy_service.service import SnapshotRejected, StrategyService
from governedgraph.strategy_service.snapshot import LocalHmacSigner, RankingSnapshot, sign_snapshot


class MemoryStateStore:
    def __init__(self, initial=None):
        self.state = deepcopy(initial)
        self.etag = "version-1" if initial is not None else None
        self.version = 1 if initial is not None else 0

    def read(self, etag=None):
        if etag and etag == self.etag:
            return None, self.etag
        return deepcopy(self.state), self.etag

    def write(self, state, etag=None):
        if etag != self.etag:
            raise ValueError("concurrent test state update")
        self.version += 1
        self.state = deepcopy(state)
        self.etag = f"version-{self.version}"
        return self.etag


SIGNER = LocalHmacSigner("azure-ci-contract-test-only")
KEY = "first_contact|transition_impact_question|voice|standard"


def service(store=None):
    return StrategyService(store=store or MemoryStateStore(), signer=SIGNER, control_share=0)


def request():
    return {"journey_id": "J-static-contract", "journey_state": "first_contact",
            "intent": "transition_impact_question", "channel": "voice"}


def test_verified_static_rollback_survives_restart_and_has_explicit_authorization():
    store = MemoryStateStore()
    svc = service(store)
    static = expert_snapshot(svc.catalogue, svc.priors, svc.thresholds, SIGNER)
    learned = sign_snapshot(RankingSnapshot(
        "snap-verified-ci", svc.catalogue.version, svc.thresholds["threshold_version"],
        "audit-ci", "tier0", {KEY: static.rankings[KEY]},
    ), SIGNER)
    svc.load_snapshot(learned, static_snapshot=static)
    assert svc.get_ranked(request()).source == "snapshot"
    svc.revert_to_static()
    for candidate in (svc, service(store)):
        response = candidate.get_ranked(request())
        assert response.source == "expert_priors_fallback"
        assert response.signature_valid is True
        assert response.static_fallback_authorized is True
        assert response.snapshot_id == static.snapshot_id


def test_absent_signed_static_state_cannot_authorize_treatment_fallback():
    svc = service()
    svc.revert_to_static()
    response = svc.get_ranked(request())
    assert response.static_fallback_authorized is False
    assert response.signature_valid is False


def test_tampered_expert_snapshot_fails_publication():
    svc = service()
    static = expert_snapshot(svc.catalogue, svc.priors, svc.thresholds, SIGNER)
    static.rankings[KEY] = [["S6", "1.0"]]
    learned = sign_snapshot(RankingSnapshot(
        "snap-verified-ci", svc.catalogue.version, svc.thresholds["threshold_version"],
        "audit-ci", "tier0", {KEY: [["S1", "1.0"]]},
    ), SIGNER)
    with pytest.raises(SnapshotRejected):
        svc.load_snapshot(learned, static_snapshot=static)


def test_closed_outcome_reference_contracts_reject_untrusted_values():
    reference = ResponseReference("J-known", "a" * 32)
    assert reference.response_id == "a" * 32
    assert OutcomeSubmission("a" * 32, "skip").choice == "skip"
    for invalid in ("maybe", "", "approved"):
        with pytest.raises(ContractError):
            OutcomeSubmission("a" * 32, invalid)
    with pytest.raises(ContractError):
        ResponseReference("https://elsewhere", "a" * 32)
