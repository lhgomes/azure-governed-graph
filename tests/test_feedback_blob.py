"""ILLUSTRATIVE - synthetic data: immutable versions and pointer races, no network."""
from dataclasses import asdict, replace
import hashlib
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from governedgraph.learning.azure_inputs import BlobLearningInputs
from governedgraph.learning.feedback import FeedbackSnapshot, project_feedback
from governedgraph.learning.feedback_store import BaselinePending, BlobFeedbackStore, snapshot_name


@pytest.fixture
def blobs(monkeypatch):
    class NotFound(Exception):
        pass

    class Exists(Exception):
        pass

    class Modified(Exception):
        pass

    azure, core, errors = (ModuleType(name) for name in ("azure", "azure.core", "azure.core.exceptions"))
    core.MatchConditions = SimpleNamespace(IfNotModified="unchanged")
    errors.ResourceNotFoundError = NotFound
    errors.ResourceExistsError = Exists
    errors.ResourceModifiedError = Modified
    for module in (azure, core, errors):
        monkeypatch.setitem(sys.modules, module.__name__, module)

    class Container:
        def __init__(self):
            self.values = {}
            self.revisions = {}

        def get_blob_client(self, name):
            def download():
                if name not in self.values:
                    raise NotFound(name)
                return SimpleNamespace(readall=lambda: self.values[name],
                                       properties=SimpleNamespace(etag=str(self.revisions[name])))

            def upload(payload, **options):
                if name in self.values and not options.get("overwrite", False):
                    raise Exists(name)
                if "etag" in options and options["etag"] != str(self.revisions.get(name)):
                    raise Modified(name)
                self.values[name] = payload
                self.revisions[name] = self.revisions.get(name, 0) + 1

            return SimpleNamespace(download_blob=download, upload_blob=upload)

    return Container(), Modified


def versioned():
    first = project_feedback([], [])
    payload = first.outcomes + b"first_contact|transition_impact_question|voice|standard,S1,1.0,1,1,0\n"
    second = FeedbackSnapshot(payload, replace(first.manifest, outcome_digest=hashlib.sha256(payload).hexdigest()))
    return first, second


def test_b4_azure_input_remains_pinned_when_live_pointer_advances(blobs):
    container, _ = blobs
    first, second = versioned()
    writer = BlobFeedbackStore(container=container)
    assert writer.current() is None
    writer.save(first)
    inputs = object.__new__(BlobLearningInputs)
    inputs.aggregates = container
    assert inputs.outcome_bytes() == first.outcomes
    writer.current()
    writer.save(second)
    assert inputs.outcome_bytes() == first.outcomes
    fresh = object.__new__(BlobLearningInputs)
    fresh.aggregates = container
    fresh.pin_outcome_digest(first.manifest.outcome_digest)
    assert fresh.outcome_bytes() == first.outcomes
    another = object.__new__(BlobLearningInputs)
    another.aggregates = container
    assert another.outcome_bytes() == second.outcomes


def test_b4_stale_producer_cannot_overwrite_a_newer_pointer(blobs):
    container, modified = blobs
    first, second = versioned()
    initial = BlobFeedbackStore(container=container)
    initial.current()
    initial.save(first)
    stale = BlobFeedbackStore(container=container)
    stale.current()
    initial.current()
    initial.save(second)
    with pytest.raises(modified):
        stale.save(first)
    assert json.loads(container.values["current.json"]) == asdict(second.manifest)


def test_missing_baseline_is_pending_not_an_incident_and_recovers(blobs, caplog, monkeypatch):
    import logging

    from governedgraph.learning import feedback_store
    from governedgraph.orchestrator.service import OrchestratorService

    container, _ = blobs
    store = BlobFeedbackStore(container=container)
    with pytest.raises(BaselinePending):
        store.baseline_bytes()
    container.values["outcomes.csv"], container.revisions["outcomes.csv"] = b"baseline", 1
    assert store.baseline_bytes() == b"baseline"

    service = object.__new__(OrchestratorService)
    service.store, service.worker, service.audit_store, service.feedback_store = SimpleNamespace(ids=lambda: []), None, None, store

    def pending(*_):
        raise BaselinePending("not yet")

    monkeypatch.setattr(feedback_store, "publish_feedback", pending)
    with caplog.at_level(logging.INFO, logger="governedgraph.orchestrator"):
        service.recover()
    assert [(r.levelno, r.message) for r in caplog.records] == [(logging.WARNING, "feedback_baseline_pending")]


def test_b4_existing_content_addressed_snapshot_cannot_be_changed(blobs):
    container, _ = blobs
    first, _ = versioned()
    writer = BlobFeedbackStore(container=container)
    writer.current()
    writer.save(first)
    container.values[snapshot_name(first.manifest.outcome_digest)] = b"corrupted"
    with pytest.raises(ValueError, match="immutable"):
        writer.save(first)
    reader = object.__new__(BlobLearningInputs)
    reader.aggregates = container
    with pytest.raises(ValueError, match="digest"):
        reader.outcome_bytes()
