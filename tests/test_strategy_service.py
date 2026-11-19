"""B3/B6/B9/B10: signed snapshots, suspension overlay, control arm, fallback, S6 always available."""
import pytest

from governedgraph.common.contracts import SuspensionEvent
from governedgraph.strategy_service.catalogue import Catalogue
from governedgraph.strategy_service.service import SnapshotRejected
from governedgraph.strategy_service.snapshot import LocalHmacSigner, RankingSnapshot, sign_snapshot
from strategy_doubles import local_strategy_service

KEY = "first_contact|transition_impact_question|voice|standard"
SIGNER = LocalHmacSigner("test")
CATALOGUE = Catalogue.load()
VER = {item.strategy_id: item.version for item in CATALOGUE.all()}


def svc(tmp_path, share=0.0):
    return local_strategy_service(tmp_path, signer=SIGNER, control_share=share)


def req(jid="J-1"):
    return {"journey_id": jid, "journey_state": "first_contact", "intent": "transition_impact_question", "channel": "voice"}


def snap(order, signer=SIGNER, cat=CATALOGUE.version):
    return sign_snapshot(RankingSnapshot("snap-t", cat, "1.0", "audit-t", "tier0", {KEY: [[s, VER[s]] for s in order]}), signer)


def test_fallback_to_expert_priors_without_snapshot(tmp_path):
    r = svc(tmp_path).get_ranked(req())
    assert r.source == "expert_priors_fallback"
    assert [x.strategy_id for x in r.ranked] == ["S1", "S5", "S2", "S4", "S3"]
    assert r.always_available == ["S6"]


def test_signed_snapshot_is_served(tmp_path):
    s = svc(tmp_path)
    s.load_snapshot(snap(["S2", "S5", "S1", "S4", "S3"]))
    r = s.get_ranked(req())
    assert r.signature_valid and r.ranked[0].strategy_id == "S2"


def test_tampered_or_foreign_snapshot_rejected(tmp_path):
    s = svc(tmp_path)
    bad = snap(["S2", "S5"])
    bad.rankings[KEY] = [["S3", VER["S3"]]]
    with pytest.raises(SnapshotRejected):
        s.load_snapshot(bad)
    with pytest.raises(SnapshotRejected):
        s.load_snapshot(snap(["S2"], signer=LocalHmacSigner("attacker")))


def test_snapshot_referencing_inactive_version_rejected(tmp_path):
    s = svc(tmp_path)
    x = RankingSnapshot("snap-x", CATALOGUE.version, "1.0", "a", "tier0", {KEY: [["S2", "9.9"]]})
    with pytest.raises(SnapshotRejected, match="non-active"):
        s.load_snapshot(sign_snapshot(x, SIGNER))


def test_suspension_overrides_rank_for_all_members(tmp_path):
    s = svc(tmp_path)
    s.load_snapshot(snap(["S2", "S5", "S1", "S4", "S3"]))
    s.suspend(SuspensionEvent("S2", VER["S2"], "first_contact", "audit-1", 26.0, "1.0", "GC-1"))
    for jid in ("J-1", "J-2", "J-3"):
        r = s.get_ranked(req(jid))
        assert "S2" not in [x.strategy_id for x in r.ranked] and r.suspended == ["S2"]


def test_only_board_can_reinstate(tmp_path):
    s = svc(tmp_path)
    s.suspend(SuspensionEvent("S2", VER["S2"], "first_contact", "a", 26.0, "1.0", "GC-1"))
    with pytest.raises(PermissionError):
        s.reinstate("S2", "first_contact", governance_decision_ref="agent-says-ok")
    s.reinstate("S2", "first_contact", governance_decision_ref="GB-2026-099")
    assert s.suspended_for("first_contact") == []


def test_control_arm_gets_static_priors(tmp_path):
    s = svc(tmp_path, share=1.0)
    s.load_snapshot(snap(["S2", "S5", "S1", "S4", "S3"]))
    r = s.get_ranked(req())
    assert r.arm == "control" and r.source == "expert_priors"
    assert r.ranked[0].strategy_id == "S1"


def test_kill_switch_and_freeze(tmp_path):
    s = svc(tmp_path)
    s.load_snapshot(snap(["S2", "S5", "S1", "S4", "S3"]))
    s.revert_to_static()
    assert s.get_ranked(req()).source == "expert_priors_fallback"
    with pytest.raises(SnapshotRejected, match="frozen"):
        s.load_snapshot(snap(["S5"]))


def test_demographic_payload_rejected_at_service(tmp_path):
    from governedgraph.common.contracts import ContractError

    with pytest.raises(ContractError):
        svc(tmp_path).get_ranked({**req(), "age_band": "75+"})
