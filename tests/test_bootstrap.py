"""B3/B6/B8: initialization is conditional, verified and never human recovery."""
import copy
import json
from dataclasses import asdict, replace
from itertools import product
from uuid import uuid4

import pytest

from governedgraph.common.config import DATA_DIR
from governedgraph.common.io import load_json
from governedgraph.strategy_service import bootstrap
from governedgraph.strategy_service.catalogue import Catalogue
from governedgraph.strategy_service.service import SnapshotRejected, StrategyService
from governedgraph.strategy_service.snapshot import LocalHmacSigner, sign_snapshot
from governedgraph.strategy_service.source_upgrade import load_upgrade, sign_upgrade
from governedgraph.strategy_service.state import LocalStateStore, StateConflict, StateUnavailable

SIGNER = LocalHmacSigner("bootstrap-test")


def snapshot():
    return bootstrap.expert_snapshot(Catalogue.load(), load_json(DATA_DIR / "catalogue/expert_priors_v1.json"),
                                     load_json(DATA_DIR / "fairness/thresholds_v1.json"), SIGNER)


def test_b3_initial_snapshot_is_approved_signed_expert_order_only(tmp_path):
    store = LocalStateStore(tmp_path / "state.json")
    result = bootstrap.initialize(store, snapshot(), SIGNER)
    assert result["initialized"] is True
    service = StrategyService(store=store, signer=SIGNER)
    assert service.current.tier == "expert_priors"
    priors = load_json(DATA_DIR / "catalogue/expert_priors_v1.json")
    assert {key: [pair[0] for pair in order] for key, order in service.current.rankings.items()} == priors["rankings"]


@pytest.mark.parametrize("control_share", [0.0, 1.0])
def test_b3_b10_signed_priors_cover_every_supported_explanation_context(tmp_path, control_share):
    from governedgraph.common.contracts import ACCESSIBILITY_MODES, CHANNELS, INTENT_DECISIONS, RequestKey

    catalogue = Catalogue.load()
    priors = load_json(DATA_DIR / "catalogue/expert_priors_v1.json")
    stages = {stage for style in catalogue.all() if style.ranked_for_resolution
              for stage in style.eligible_journey_states}
    intents = {intent for route, intent, _ in INTENT_DECISIONS if route == "explanation"}
    keys = [RequestKey(*parts) for parts in product(sorted(stages), sorted(intents),
                                                   sorted(CHANNELS), sorted(ACCESSIBILITY_MODES))]
    keys = [key for key in keys if catalogue.rankable_for(key)]
    missing = {key.id for key in keys} - priors["rankings"].keys()
    if missing:
        pytest.fail(f"{len(missing)} supported contexts lack explicit approved priors: {sorted(missing)[:3]}")
    store = LocalStateStore(tmp_path / "state.json")
    bootstrap.initialize(store, snapshot(), SIGNER)
    service = StrategyService(store=store, signer=SIGNER, control_share=control_share)
    for key in keys:
        response = service.get_ranked({"journey_id": "J-synthetic-coverage", **asdict(key)})
        expected = priors["rankings"][key.id]
        assert expected and len(expected) == len(set(expected))
        assert set(expected) <= {style.strategy_id for style in catalogue.rankable_for(key)}
        assert response.source == ("expert_priors" if control_share else "snapshot")
        assert response.signature_valid == (control_share == 0)
        assert [style.strategy_id for style in response.ranked] == expected
        assert response.always_available == ["S6"] and "S6" not in expected


@pytest.mark.parametrize("contact_type,channel", tuple(product(("first_contact", "second_contact"), ("voice", "chat"))))
def test_b3_b6_general_follow_up_uses_signed_ranking_and_keeps_protective_suspensions(
        tmp_path, contact_type, channel):
    from governedgraph.common.contracts import SuspensionEvent

    store = LocalStateStore(tmp_path / "state.json")
    bootstrap.initialize(store, snapshot(), SIGNER)
    service = StrategyService(store=store, signer=SIGNER, control_share=0)
    service.suspend(SuspensionEvent("S1", service.catalogue.get("S1").version, "*",
                                   "synthetic-coverage-audit", 1.0, "1.0", "synthetic-case"))
    request = {"journey_id": "J-synthetic-follow-up", "journey_state": contact_type, "channel": channel,
               "accessibility_mode": "standard"}
    first = service.get_ranked({**request, "intent": "transition_impact_question"})
    follow_up = service.get_ranked({**request, "intent": "general_question"})
    assert first.source == follow_up.source == "snapshot"
    assert first.snapshot_id == follow_up.snapshot_id
    assert follow_up.signature_valid and follow_up.ranked
    assert "S1" in follow_up.suspended
    assert "S1" not in {style.strategy_id for style in follow_up.ranked}
    assert follow_up.always_available == ["S6"]


def test_b6_b8_redeploy_preserves_every_learned_suspension_and_frozen_state_byte(tmp_path):
    store = LocalStateStore(tmp_path / "state.json")
    bootstrap.initialize(store, snapshot(), SIGNER)
    service = StrategyService(store=store, signer=SIGNER)
    from governedgraph.common.contracts import SuspensionEvent
    service.suspend(SuspensionEvent("S2", service.catalogue.get("S2").version, "first_contact",
                                  "audit-bootstrap-test", 1.0, "1.0", "synthetic-case"))
    service.freeze()
    before = store.path.read_bytes()
    assert bootstrap.initialize(store, snapshot(), SIGNER)["initialized"] is False
    assert store.path.read_bytes() == before
    assert StrategyService(store=store, signer=SIGNER).read_state()["frozen"] is True


def test_b3_b8_corrupt_existing_state_is_never_treated_as_fresh(tmp_path):
    store = LocalStateStore(tmp_path / "state.json")
    bootstrap.initialize(store, snapshot(), SIGNER)
    value = json.loads(store.path.read_text())
    value["current"]["signature"] = "invalid"
    store.path.write_text(json.dumps(value))
    before = store.path.read_bytes()
    with pytest.raises(StateUnavailable):
        bootstrap.initialize(store, snapshot(), SIGNER)
    assert store.path.read_bytes() == before


def test_b3_initial_snapshot_signature_is_a_hard_gate(tmp_path):
    value = snapshot()
    value.signature = "invalid"
    store = LocalStateStore(tmp_path / "state.json")
    from governedgraph.strategy_service.service import SnapshotRejected
    with pytest.raises(SnapshotRejected):
        bootstrap.initialize(store, value, SIGNER)
    assert not store.path.exists()


@pytest.mark.parametrize("role", ["sign", "initialize"])
def test_normal_bootstrap_roles_do_not_load_or_sign_upgrade_metadata(tmp_path, monkeypatch, capsys, role):
    import sys

    monkeypatch.setattr(bootstrap, "load_upgrade", lambda: pytest.fail("ordinary role loaded an upgrade plan"))
    monkeypatch.setattr(bootstrap, "default_signer", lambda **_: SIGNER)
    monkeypatch.setattr(bootstrap, "BlobStateStore", lambda _: LocalStateStore(tmp_path / "state.json"))
    monkeypatch.setenv("GOVERNEDGRAPH_INITIAL_SNAPSHOT", json.dumps(snapshot().to_dict()))
    monkeypatch.delenv("GOVERNEDGRAPH_SOURCE_UPGRADE_APPROVAL", raising=False)
    monkeypatch.delenv("GOVERNEDGRAPH_SOURCE_UPGRADE_SIGNATURE", raising=False)
    monkeypatch.setattr(sys, "argv", ["bootstrap", role])
    bootstrap.main()
    result = json.loads(capsys.readouterr().out.removeprefix("GG_BOOTSTRAP="))
    if role == "sign":
        assert set(result) == {"snapshot"}
    else:
        assert result["initialized"] is True


@pytest.mark.parametrize("role", ["sign-upgrade", "upgrade"])
def test_explicit_upgrade_roles_require_a_reviewed_plan(monkeypatch, role):
    import sys

    monkeypatch.setattr(bootstrap, "load_upgrade", lambda: None)
    monkeypatch.setattr(sys, "argv", ["bootstrap", role])
    with pytest.raises(SnapshotRejected, match="reviewed upgrade plan"):
        bootstrap.main()


def test_fresh_and_unchanged_future_catalogues_ignore_obsolete_upgrade_plans(tmp_path, monkeypatch):
    from governedgraph.strategy_service import service

    data = Catalogue.load().to_dict()
    data["catalogue_version"] = "future-approved-source"
    future = Catalogue(data)
    monkeypatch.setattr(Catalogue, "load", classmethod(lambda cls, path=None: future))
    monkeypatch.setattr(service, "load_upgrade", lambda: pytest.fail("current source depended on obsolete upgrade metadata"))
    baseline = snapshot()
    store = LocalStateStore(tmp_path / "state.json")
    assert bootstrap.initialize(store, baseline, SIGNER)["initialized"] is True
    before = store.path.read_bytes()
    assert bootstrap.initialize(store, baseline, SIGNER)["initialized"] is False
    assert store.path.read_bytes() == before
    runtime = StrategyService(store=store, signer=SIGNER)
    assert runtime.read_state()["current"]["catalogue_version"] == "future-approved-source"


@pytest.fixture
def predecessor_state(tmp_path):
    class ArchiveStore(LocalStateStore):
        def __init__(self):
            super().__init__(tmp_path / "state.json")
            self.archives = []

        def archive_upgrade(self, document):
            self.archives.append(copy.deepcopy(document))
            return "catalogue-upgrades/synthetic.json"

        def write(self, state, etag):
            if self.read()[1] != etag:
                raise StateConflict("synthetic concurrent update")
            return super().write(state, etag)

    plan = load_upgrade()
    assert plan is not None
    old = bootstrap.expert_snapshot(Catalogue(plan.source_catalogue), plan.source_priors,
                                   load_json(DATA_DIR / "fairness/thresholds_v1.json"), SIGNER)
    learned = copy.deepcopy(old)
    learned.snapshot_id, learned.tier, learned.source_audit_id = "old-learned-ranking", "tier0", "synthetic-audit"
    for order in learned.rankings.values():
        order.reverse()
    sign_snapshot(learned, SIGNER)
    from governedgraph.common.contracts import SuspensionEvent
    event = SuspensionEvent("S2", "1.0", "first_contact", "synthetic-audit", 1.0, "1.0", "synthetic-case")
    from governedgraph.strategy_service.overrides import OwnerOverride
    pause = OwnerOverride("GB-" + uuid4().hex, "pause", strategy_id="S3", journey_state="*")
    state = {"current": learned.to_dict(), "last_good": old.to_dict(), "static": old.to_dict(),
             "frozen": False, "suspensions": [asdict(event)],
             "owner_revision": uuid4().hex, "owner_overrides": [pause.to_dict()]}
    store = ArchiveStore()
    store.write(state, None)
    approval = {"tier": "tier2", "approved": True, "decision_ref": plan.decision_ref,
                "evidence_ref": plan.evidence_ref}
    return store, plan, sign_upgrade(plan, approval, SIGNER)


def test_normal_initialization_never_implicitly_upgrades_old_state(predecessor_state, monkeypatch):
    from governedgraph.strategy_service import service

    store, _, _ = predecessor_state
    before = store.path.read_bytes()
    monkeypatch.setattr(service, "load_upgrade", lambda: pytest.fail("ordinary initialization read migration metadata"))
    with pytest.raises(StateUnavailable):
        bootstrap.initialize(store, snapshot(), SIGNER)
    assert store.path.read_bytes() == before and not store.archives


def test_b3_b5_b6_upgrade_archives_old_learning_and_installs_only_approved_expert_priors(predecessor_state):
    store, plan, upgrade = predecessor_state
    before, _ = store.read()
    replica = StrategyService(store=store, signer=SIGNER)
    assert replica.current is not None
    assert replica.catalogue.version == plan.source_catalogue["catalogue_version"]
    assert replica.current.snapshot_id == "old-learned-ranking"
    baseline = snapshot()
    result = bootstrap.initialize(store, baseline, SIGNER, plan=plan, upgrade=upgrade)
    assert result["migrated"] is True and result["previous_state_archive"] == "catalogue-upgrades/synthetic.json"
    after = replica.read_state()
    assert replica.catalogue.version == baseline.catalogue_version
    assert after["current"] == after["static"] == baseline.to_dict() and after["last_good"] is None
    assert after["suspensions"] == before["suspensions"]
    assert after["owner_overrides"] == before["owner_overrides"]
    assert after["owner_revision"] == before["owner_revision"]
    assert store.archives[0]["previous_state"] == before
    assert store.archives[0]["publication"] == asdict(upgrade)
    for share in (0.0, 1.0):
        service = StrategyService(store=store, signer=SIGNER, control_share=share)
        ranked = service.get_ranked({
            "journey_id": "synthetic-upgrade", "journey_state": "first_contact",
            "intent": "transition_impact_question", "channel": "voice",
        })
        assert {"S2", "S3"} <= set(ranked.suspended)
        assert not {"S2", "S3"} & {item.strategy_id for item in ranked.ranked}
        assert ranked.always_available == ["S6"]
    raw = store.path.read_bytes()
    assert bootstrap.initialize(store, baseline, SIGNER, plan=plan, upgrade=upgrade)["initialized"] is False
    assert store.path.read_bytes() == raw and len(store.archives) == 1


@pytest.mark.parametrize("blocked", ["missing_signature", "tampered_signature", "changed_plan", "invalid_baseline",
                                   "corrupt_state", "unknown_version", "frozen", "pending_owner_change", "owner_order"])
def test_b3_b8_upgrade_never_bypasses_approval_or_protective_state(predecessor_state, blocked):
    store, plan, upgrade = predecessor_state
    baseline = snapshot()
    if blocked == "missing_signature":
        upgrade = None
    elif blocked == "tampered_signature":
        upgrade = replace(upgrade, signature="invalid")
    elif blocked == "changed_plan":
        plan = replace(plan, evidence_ref="different-unapproved-evidence")
    elif blocked == "invalid_baseline":
        baseline.signature = "invalid"
    else:
        state, etag = store.read()
        if blocked == "corrupt_state":
            state["current"]["signature"] = "invalid"
        elif blocked == "unknown_version":
            from governedgraph.strategy_service.snapshot import RankingSnapshot
            for field in ("current", "last_good", "static"):
                old = RankingSnapshot.from_dict(state[field])
                old.catalogue_version = "unknown-catalogue"
                state[field] = sign_snapshot(old, SIGNER).to_dict()
        elif blocked == "frozen":
            state["frozen"] = True
        elif blocked == "owner_order":
            from governedgraph.strategy_service.overrides import OwnerOverride
            key = next(iter(plan.source_priors["rankings"]))
            state["owner_overrides"].append(OwnerOverride(
                "GB-" + uuid4().hex, "order", request_key=key,
                order=plan.source_priors["rankings"][key],
            ).to_dict())
        else:
            from governedgraph.strategy_service.catalogue_release import CatalogueDecision, prepare_bundle
            from governedgraph.strategy_service.overrides import state_revision
            decision = CatalogueDecision.from_dict({
                "expected_revision": state_revision(state), "decision_ref": "GB-" + uuid4().hex,
                "reason": "ILLUSTRATIVE - pending synthetic owner change", "confirmed": True,
                "change": {"action": "edit", "strategy_id": "S1", "name": "Synthetic approved style",
                           "pattern_id": "S1", "eligible_journey_states": ["first_contact"],
                           "channels": ["voice"], "accessibility_modes": ["standard"]},
            })
            state["catalogue_pending"] = {
                "decision_ref": decision.decision_ref, "base_version": plan.source_catalogue["catalogue_version"],
                "bundle": prepare_bundle(Catalogue(plan.source_catalogue), plan.source_priors, decision).to_dict(),
            }
        store.write(state, etag)
    before = store.path.read_bytes()
    expected = (StateUnavailable if blocked in {"corrupt_state", "unknown_version"} else
                StateConflict if blocked in {"frozen", "pending_owner_change", "owner_order"} else SnapshotRejected)
    with pytest.raises(expected):
        bootstrap.initialize(store, baseline, SIGNER, plan=plan, upgrade=upgrade)
    assert store.path.read_bytes() == before and not store.archives


@pytest.mark.parametrize("failure", ["archive", "concurrent_freeze"])
def test_b6_b8_upgrade_requires_durable_history_and_cas(predecessor_state, monkeypatch, failure):
    store, plan, upgrade = predecessor_state
    before = store.path.read_bytes()
    if failure == "archive":
        def archive(_):
            raise StateUnavailable("synthetic archive outage")
        monkeypatch.setattr(store, "archive_upgrade", archive)
    else:
        write = store.write
        def concurrent(state, etag):
            previous, version = store.read()
            LocalStateStore.write(store, {**previous, "frozen": True}, version)
            return write(state, etag)
        monkeypatch.setattr(store, "write", concurrent)
    with pytest.raises(StateUnavailable):
        bootstrap.initialize(store, snapshot(), SIGNER, plan=plan, upgrade=upgrade)
    if failure == "archive":
        assert store.path.read_bytes() == before
    else:
        state, _ = store.read()
        assert state["frozen"] is True
        assert state["current"]["catalogue_version"] == plan.source_catalogue["catalogue_version"]


def test_b4_bootstrap_fixtures_preserve_changed_upstream_and_initialize_only_missing(monkeypatch):
    from azure.core.exceptions import ResourceNotFoundError
    from governedgraph.dev.publish_learning_fixtures import initialize

    class Blob:
        def __init__(self, exists):
            self.exists, self.writes = exists, []
        def get_blob_properties(self):
            if not self.exists:
                error = ResourceNotFoundError(message="missing")
                error.error_code = "BlobNotFound"
                raise error
            return {"source": "changed-upstream"}
        def upload_blob(self, data, **options):
            assert options["overwrite"] is False
            self.writes.append(data)
    old, fresh = Blob(True), Blob(False)
    class Container:
        def get_blob_client(self, name):
            return old if name == "old" else fresh
    initialize({"learning-aggregates": Container()}, {("learning-aggregates", "old"): b"initial",
                                                     ("learning-aggregates", "fresh"): b"initial"})
    assert old.writes == [] and fresh.writes == [b"initial"]
