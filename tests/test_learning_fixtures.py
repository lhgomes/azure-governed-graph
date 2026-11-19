"""B4/B7: dev inputs are explicit, watermarked and isolated from the signer."""
import json
import sys
from pathlib import Path
from shutil import copytree
from types import SimpleNamespace

import pytest

from governedgraph.common.config import DATA_DIR
from governedgraph.dev.publish_learning_fixtures import fixture_payloads, main, publish

ROOT = Path(__file__).resolve().parents[1]


def test_b7_publisher_routes_only_cohorts_to_segregated_container():
    payloads = fixture_payloads(DATA_DIR)
    assert set(payloads) == {("learning-aggregates", "outcomes.csv"),
                             ("learning-aggregates", "uplift.json"),
                             ("cohort-zone", "cohort_aggregates.csv")}
    assert b"dimension,cohort" not in payloads["learning-aggregates", "outcomes.csv"]
    assert b"dimension,cohort" in payloads["cohort-zone", "cohort_aggregates.csv"]
    assert json.loads(payloads["learning-aggregates", "uplift.json"])["watermark"] == "ILLUSTRATIVE - synthetic data"


def test_b7_publisher_rejects_cohort_columns_in_aggregate_fixture(tmp_path):
    copytree(DATA_DIR, tmp_path / "data")
    root = tmp_path / "data"
    path = root / "synthetic" / "outcome_aggregates_illustrative.csv"
    path.write_text(path.read_text().replace("strategy_version,", "cohort,"))
    with pytest.raises(ValueError, match="cohort columns"):
        fixture_payloads(root)


@pytest.mark.parametrize("environment,opt_in", [("production", "true"), ("gg-dev", "false"), ("", "true")])
def test_b4_fixture_publisher_requires_dev_environment_and_explicit_opt_in(monkeypatch, environment, opt_in):
    monkeypatch.setenv("AZURE_ENV_NAME", environment)
    monkeypatch.setenv("DEPLOY_DEV_FIXTURES", opt_in)
    with pytest.raises(RuntimeError, match="opt-in"):
        main()


def test_b7_no_tracking_or_compute_role_exists_and_p2_access_stays_container_scoped():
    modules = {path: path.read_text() for path in (ROOT / "infra").rglob("*.bicep")}
    assert not (ROOT / "infra/modules/ml-tracking-rbac.bicep").exists()
    for path, source in modules.items():
        for forbidden in ("Microsoft.MachineLearningServices", "workspaces/jobs", "/computes/", "/submit/action"):
            assert forbidden not in source, f"{path.name} grants or declares {forbidden}"
    access = modules[ROOT / "infra/modules/p2-access.bicep"]
    assert "scope: artifacts" in access and "scope: aggregates" in access and "scope: registry" in access
    assert "dataActions" not in access and "roleDefinitions@" not in access


def test_b4_fixture_publisher_defaults_disabled_and_has_no_runtime_identity():
    source = (ROOT / "infra/modules/dev-learning-fixtures.bicep").read_text()
    assert "param deployDevFixtures bool = false" in source
    assert "deployDevFixtures && environmentName == 'gg-dev'" in source
    assert "triggerType: 'Manual'" in source
    assert "scope: aggregates" in source
    assert "scope: cohorts" in source
    assert "pipelinePrincipalId" not in source
    assert "fairnessPrincipalId" not in source
    assert "scope: records" not in source
    assert "scope: ranking" not in source


def test_b4_fixture_upload_is_labelled_create_only_and_conflict_fails(monkeypatch):
    class AlreadyExists(Exception):
        pass

    monkeypatch.setitem(sys.modules, "azure.core.exceptions", SimpleNamespace(ResourceExistsError=AlreadyExists))

    class Blob:
        def __init__(self):
            self.data = None
            self.metadata = {}

        def upload_blob(self, data, *, overwrite, metadata):
            assert overwrite is False
            if self.data is not None:
                raise AlreadyExists()
            self.data, self.metadata = data, metadata

        def download_blob(self):
            return SimpleNamespace(readall=lambda: self.data)

        def get_blob_properties(self):
            return SimpleNamespace(metadata=self.metadata)

    blob = Blob()
    clients = {"learning-aggregates": SimpleNamespace(get_blob_client=lambda _: blob)}
    payloads = {("learning-aggregates", "outcomes.csv"): b"fixture"}
    publish(clients, payloads)
    assert blob.metadata["watermark"] == "ILLUSTRATIVE - synthetic data"
    publish(clients, payloads)
    with pytest.raises(ValueError, match="differs"):
        publish(clients, {("learning-aggregates", "outcomes.csv"): b"changed"})
    assert blob.data == b"fixture"
