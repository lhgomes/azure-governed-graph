"""B3/B6/B8: initialization is conditional, verified and never human recovery."""
import json

import pytest

from governedgraph.common.config import DATA_DIR
from governedgraph.common.io import load_json
from governedgraph.strategy_service import bootstrap
from governedgraph.strategy_service.catalogue import Catalogue
from governedgraph.strategy_service.service import StrategyService
from governedgraph.strategy_service.snapshot import LocalHmacSigner
from governedgraph.strategy_service.state import LocalStateStore, StateUnavailable

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
