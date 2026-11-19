import json
import sys
from dataclasses import asdict, replace
from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from counsellor_doubles import new_session
from governedgraph.common.contracts import ContractError
from governedgraph.search import approved_content, ingest
from governedgraph.search import run_publisher_job as remote
from governedgraph.search.corpus import ContentDocument, approve_version, load_corpus


def document(**changes):
    row = {
        "id": "C-TEST-v1.0", "content_id": "C-TEST", "version": "1.0", "status": "approved",
        "title": "Pensioen overgang", "language": "nl", "journey_states": ["*"],
        "approval_ref": "CA-SYNTHETIC-TEST-1", "body": "U kunt altijd een medewerker spreken.",
        "watermark": "ILLUSTRATIVE - synthetic data",
    }
    return ContentDocument.from_dict({**row, **changes})


@pytest.fixture
def azure_modules(monkeypatch):
    class HttpResponseError(Exception):
        def __init__(self, status_code=500):
            self.status_code = status_code

    class ResourceExistsError(HttpResponseError):
        pass

    class ResourceNotFoundError(HttpResponseError):
        pass

    modules = {}
    for name in ("azure", "azure.core", "azure.core.exceptions", "azure.search",
                 "azure.search.documents", "azure.search.documents.models",
                 "azure.search.documents.indexes", "azure.search.documents.indexes.models"):
        modules[name] = ModuleType(name)
        monkeypatch.setitem(sys.modules, name, modules[name])
    exceptions = modules["azure.core.exceptions"]
    exceptions.HttpResponseError = HttpResponseError
    exceptions.ResourceExistsError = ResourceExistsError
    exceptions.ResourceNotFoundError = ResourceNotFoundError
    modules["azure.search.documents.models"].VectorizableTextQuery = lambda **kw: SimpleNamespace(**kw)
    for name in ("SearchIndexer", "IndexingParameters", "IndexingParametersConfiguration", "FieldMapping"):
        setattr(modules["azure.search.documents.indexes.models"], name, lambda **kw: SimpleNamespace(**kw))
    return exceptions


@pytest.mark.parametrize("approval", [None, "", " "])
def test_b3_ingest_rejects_missing_or_empty_approval_before_azure(monkeypatch, tmp_path, approval):
    row = asdict(document())
    if approval is None:
        row.pop("approval_ref")
    else:
        row["approval_ref"] = approval
    path = tmp_path / "content"
    path.mkdir()
    (path / "approved_content.jsonl").write_text(json.dumps(row))
    monkeypatch.setattr(ingest, "DATA_DIR", tmp_path)
    publisher = Mock()
    monkeypatch.setattr(ingest, "AzureContentPublisher", publisher)
    with pytest.raises(ContractError):
        ingest.main()
    publisher.assert_not_called()


def test_b7_content_ingest_has_closed_schema():
    with pytest.raises(ContractError, match="demographic"):
        ContentDocument.from_dict({**asdict(document()), "age_band": "75+"})


def test_b3_approval_retires_only_previous_same_content_language():
    old = document()
    other_language = document(id="C-TEST-en-v1.0", language="en")
    unrelated = document(id="C-OTHER-v1.0", content_id="C-OTHER")
    candidate = document(id="C-TEST-v1.1", version="1.1", approval_ref="CA-SYNTHETIC-TEST-2")
    result = approve_version([old, other_language, unrelated], candidate)
    assert [d.status for d in result] == ["retired", "approved", "approved", "approved"]
    assert approve_version(result, candidate) == result
    with pytest.raises(ContractError, match="immutable"):
        approve_version(result, old)
    with pytest.raises(ContractError, match="immutable"):
        approve_version(result, replace(candidate, body="Changed without a version bump"))
    with pytest.raises(ContractError, match="immutable"):
        approve_version(result, replace(candidate, id="C-ALIAS-v1.1"))


def test_b3_corpus_rejects_duplicate_approved_versions_and_index_key_collisions():
    old = document()
    for other in (replace(old, id="C-TEST-v1.1", version="1.1"), replace(old, id="C-TEST-v1_0", status="retired")):
        with pytest.raises(ContractError):
            load_corpus("\n".join(json.dumps(asdict(d)) for d in [old, other]))


def test_b3_indexed_corpus_round_trip_keeps_source_version_ids():
    doc = document()
    assert load_corpus(json.dumps(doc.index_document()), indexed=True) == [doc]


def test_b3_retrieval_never_returns_retired_versions(azure_modules):
    rows = [document(status="retired"), document(id="C-TEST-v1.1", version="1.1")]

    class Index:
        """Index double that honours the mandatory status filter, as Azure AI Search does."""

        def search(self, **kwargs):
            assert kwargs["filter"].startswith("status eq 'approved'")
            return [row.index_document() for row in rows if row.status == "approved"]

    search = object.__new__(approved_content.AzureContentSearch)
    search.client = Index()
    hits = search.search("pensioen overgang", "first_contact", language="nl")
    assert [h.version for h in hits] == ["1.1"]


def test_b3_azure_always_uses_approved_filter_hybrid_semantic_and_prefilter(azure_modules):
    client = Mock()
    client.search.return_value = [document().index_document()]
    search = object.__new__(approved_content.AzureContentSearch)
    search.client = client
    hits = search.search("pensioen", "state' or status eq 'retired", language="nl")
    call = client.search.call_args.kwargs
    assert call["filter"].startswith("status eq 'approved' and language eq 'nl'")
    assert "state'' or status eq ''retired" in call["filter"]
    assert call["query_type"] == "semantic"
    assert call["semantic_configuration_name"] == "default"
    assert call["vector_filter_mode"] == "preFilter"
    assert call["search_text"] == "pensioen"
    vector = call["vector_queries"][0]
    assert (vector.text, vector.fields, vector.k_nearest_neighbors) == ("pensioen", "body_vector", 50)
    assert hits[0].id == document().id
    for changes in ({"status": "retired"}, {"approval_ref": ""}, {"language": "en"}, {"source_id": None}):
        client.search.return_value = [{**document().index_document(), **changes}]
        with pytest.raises(ValueError, match="outside"):
            search.search("pensioen", "first_contact", language="nl")


@pytest.mark.parametrize("mode", ["standard", "english", "plain_language", "slower_pace", "large_text"])
@pytest.mark.parametrize("language", ["en", "nl"])
def test_b3_tool_uses_established_response_language_independently_of_accessibility(mode, language,
                                                                                    local_counsellor):
    counsellor = new_session("MBR-7Q2K", accessibility_mode=mode)
    counsellor.journey.response_language = language
    counsellor.tools.search = Mock()
    counsellor.tools.search.search.return_value = []
    counsellor.tools.search_approved_content("pensioen")
    assert counsellor.tools.search.search.call_args.kwargs["language"] == language


def test_b3_retirement_failure_prevents_replacement_publication(azure_modules):
    publisher = object.__new__(ingest.AzureContentPublisher)
    old = document()
    publisher.blob = Mock()
    publisher.blob.acquire_lease.return_value.__enter__ = Mock(return_value=object())
    publisher.blob.acquire_lease.return_value.__exit__ = Mock(return_value=False)
    publisher.blob.download_blob.return_value.content_as_text.return_value = json.dumps(old.index_document())
    publisher.indexers = Mock()
    publisher.indexers.get_indexer_status.return_value.last_result = None
    publisher.indexer_name = "synthetic-indexer"
    publisher.configure = Mock()
    publisher.run_indexer = Mock()
    publisher.search = Mock()
    publisher.search.search.return_value = [{"id": old.search_id}]
    publisher.search.merge_documents.return_value = [SimpleNamespace(key=old.search_id, succeeded=False)]
    candidate = document(id="C-TEST-v1.1", version="1.1")
    with pytest.raises(RuntimeError, match="no replacement"):
        publisher.publish([old], candidate)
    publisher.run_indexer.assert_not_called()
    assert len(publisher.blob.upload_blob.call_args_list) == 2
    assert publisher.blob.upload_blob.call_args_list[0].kwargs == {"overwrite": False}
    persisted = load_corpus(publisher.blob.upload_blob.call_args.args[0], indexed=True)
    assert [(d.id, d.status) for d in persisted] == [(old.id, "retired"), (candidate.id, "approved")]


def test_b3_retirement_becomes_visible_before_returning():
    search = Mock()
    search.search.side_effect = [[{"id": document().search_id}], []]
    search.merge_documents.return_value = [SimpleNamespace(key=document().search_id, succeeded=True)]
    ingest.retire_previous(search, [document(id="C-TEST-v1.1", version="1.1")])
    search.merge_documents.assert_called_once_with([{"id": document().search_id, "status": "retired"}])


def test_b3_retirement_timeout_fails_closed():
    search = Mock()
    search.search.return_value = [{"id": document().search_id}]
    search.merge_documents.return_value = [SimpleNamespace(key=document().search_id, succeeded=True)]
    with pytest.raises(TimeoutError, match="replacement not published"):
        ingest.retire_previous(search, [document(id="C-TEST-v1.1", version="1.1")], timeout=0)


def test_b3_publication_retires_before_publishing_and_keeps_authoritative_feed(azure_modules):
    publisher = object.__new__(ingest.AzureContentPublisher)
    old = document()
    candidate = document(id="C-TEST-v1.1", version="1.1")
    publisher.blob = Mock()
    publisher.blob.acquire_lease.return_value.__enter__ = Mock(return_value=object())
    publisher.blob.acquire_lease.return_value.__exit__ = Mock(return_value=False)
    publisher.blob.download_blob.return_value.content_as_text.return_value = json.dumps(old.index_document())
    publisher.blob.upload_blob.side_effect = [azure_modules.ResourceExistsError(), None]
    publisher.indexers = Mock()
    publisher.indexers.get_indexer_status.return_value.last_result = None
    publisher.indexer_name = "synthetic-indexer"
    publisher.configure = Mock()
    publisher.run_indexer = Mock()
    publisher.search = Mock()
    publisher.search.search.side_effect = [[{"id": old.search_id}], [], [candidate.index_document()],
                                           [{"id": candidate.search_id}]]
    publisher.search.merge_documents.return_value = [SimpleNamespace(key=old.search_id, succeeded=True)]
    operations = Mock()
    operations.attach_mock(publisher.search.merge_documents, "retire")
    operations.attach_mock(publisher.blob.upload_blob, "upload")
    operations.attach_mock(publisher.run_indexer, "index")
    publisher.publish([old], candidate)
    assert [call[0] for call in operations.mock_calls] == ["upload", "upload", "retire", "index"]
    source = load_corpus(publisher.blob.upload_blob.call_args.args[0], indexed=True)
    assert [(d.id, d.status) for d in source] == [(old.id, "retired"), (candidate.id, "approved")]
    assert "lease" in publisher.blob.upload_blob.call_args.kwargs
    vector_filter = publisher.search.search.call_args.kwargs["filter"]
    assert "content_id eq 'C-TEST' and version eq '1.1' and language eq 'nl'" in vector_filter
    assert "search.in(id" not in vector_filter


def test_b3_indexer_noop_cannot_masquerade_as_a_vectorized_corpus(azure_modules):
    publisher = object.__new__(ingest.AzureContentPublisher)
    publisher.search = Mock()
    legacy = {**document().index_document(), "source_id": None}
    publisher.search.search.return_value = [legacy]
    with pytest.raises(RuntimeError, match="provenance or integrated vectors"):
        publisher.verify_index([document()], timeout=0)
    publisher.search.search.side_effect = [[document().index_document()], []]
    with pytest.raises(RuntimeError, match="provenance or integrated vectors"):
        publisher.verify_index([document()], timeout=0)

def test_b3_tools_replace_previous_passages_instead_of_reusing_retired_content(local_counsellor):
    tools = new_session("MBR-7Q2K").tools
    tools.search = Mock()
    old = approved_content.ContentHit("C-TEST-v1.0", "C-TEST", "1.0", "Old", "Old passage", "nl")
    new = approved_content.ContentHit("C-TEST-v1.1", "C-TEST", "1.1", "New", "New passage", "nl")
    tools.search.search.side_effect = [[old], [new]]
    tools.search_approved_content("overgang")
    tools.search_approved_content("overgang")
    assert tools.trace.content_versions == [new.id]
    assert tools.trace.content_texts == [new.body]


def test_b3_retrieval_uses_current_question_and_a_bounded_broader_evidence_set(local_counsellor):
    tools = new_session("MBR-7Q2K").tools
    tools.search = Mock()
    tools.search.search.return_value = []
    question = "How do my built-up rights move into the new scheme?"
    tools.search_approved_content(question)
    tools.search.search.assert_called_once_with(
        question, tools.journey.stage, language=tools.journey.response_language, top=5,
    )


@pytest.mark.parametrize("status,failed,warnings", [("transientFailure", 0, []), ("success", 1, []),
                                                  ("success", 0, ["synthetic warning"])])
def test_b3_indexer_failure_or_warning_is_not_success(azure_modules, status, failed, warnings):
    publisher = object.__new__(ingest.AzureContentPublisher)
    publisher.indexer_name = "synthetic-indexer"
    publisher.indexers = Mock()
    publisher.indexers.get_indexer_status.side_effect = lambda _: SimpleNamespace(last_result=SimpleNamespace(
        start_time=datetime.now(timezone.utc), status=status, failed_item_count=failed, errors=[], warnings=warnings))
    with pytest.raises(RuntimeError, match="indexer failed"):
        publisher.run_indexer(timeout=1)


def test_b3_search_identity_does_not_inherit_runtime_content_write_access():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "infra/modules/rbac.bicep").read_text()
    grant = source.split("resource searchContentRead ", 1)[1].split("\nresource ", 1)[0]
    assert "scope: contentContainer" in grant and "roles.blobDataReader" in grant
    data = (root / "infra/modules/data.bicep").read_text()
    storage = data.split("resource contentStorage ", 1)[1].split("\nresource ", 1)[0]
    assert "allowSharedKeyAccess: false" in storage
    assert "allowBlobPublicAccess: false" in storage
    assert "identity: { type: 'SystemAssigned' }" in data


@pytest.fixture
def remote_settings(monkeypatch):
    for name, value in {
        "AZURE_RESOURCE_GROUP": "synthetic-group", "AZURE_SUBSCRIPTION_ID": "synthetic-subscription",
        "CONTENT_PUBLISH_JOB_NAME": "synthetic-publisher",
        "CONTENT_SEARCH_SHARED_LINK_ID": "/synthetic-link",
    }.items():
        monkeypatch.setenv(name, value)


def test_b3_private_publisher_requires_approved_link_and_success(monkeypatch, remote_settings):
    cli = Mock(return_value=json.dumps({"properties": {"status": "Pending", "provisioningState": "Succeeded"}}))
    monkeypatch.setattr(remote, "azure_cli", cli)
    with pytest.raises(RuntimeError, match="owner approval"):
        remote.run_publisher_job()
    assert cli.call_count == 1
    cli.side_effect = [json.dumps({"properties": {"status": "Approved", "provisioningState": "Succeeded"}}),
                       "synthetic-execution", "Succeeded"]
    assert remote.run_publisher_job() == "synthetic-execution"


def test_b3_remote_approval_preserves_full_private_job_template(monkeypatch, remote_settings):
    from pathlib import Path

    template = {"containers": [{"name": "publisher", "image": "synthetic-image",
                "command": ["python", "-m", "governedgraph.search.ingest"],
                "env": [{"name": "AZURE_CLIENT_ID", "value": "p3-not-runtime"}],
                "resources": {"cpu": 0.5, "memory": "1Gi"}}]}
    captured = []

    def cli(*args):
        if args[0] == "rest":
            return json.dumps({"properties": {"status": "Approved", "provisioningState": "Succeeded"}})
        if args[2] == "show":
            return json.dumps(template)
        if args[2] == "start":
            path = Path(args[args.index("--yaml") + 1])
            captured.append(json.loads(path.read_text()))
            return "synthetic-execution"
        return "Succeeded"

    monkeypatch.setattr(remote, "azure_cli", cli)
    assert remote.run_publisher_job(document()) == "synthetic-execution"
    container = captured[0]["containers"][0]
    assert container["image"] == "synthetic-image"
    assert container["resources"] == template["containers"][0]["resources"]
    assert container["env"][0] == template["containers"][0]["env"][0]
    assert container["command"] == ["python", "-m", "governedgraph.search.reindex_on_approval"]
    assert container["args"] == ["--from-env"]
    assert [ContentDocument.from_dict(item) for item in json.loads(container["env"][1]["value"])] == [document()]


def test_b3_batch_approval_publishes_several_versions_in_one_reindex():
    from governedgraph.search.reindex_on_approval import parse_candidates

    first, second = document(), document(id="C-TEST2-v1.0", content_id="C-TEST2")
    assert parse_candidates([asdict(first), asdict(second)]) == [first, second]
    assert parse_candidates(asdict(first)) == [first]


def test_b3_remote_failed_job_is_not_success(monkeypatch, remote_settings):
    cli = Mock(side_effect=[
        json.dumps({"properties": {"status": "Approved", "provisioningState": "Succeeded"}}),
        "synthetic-execution", "Failed"])
    monkeypatch.setattr(remote, "azure_cli", cli)
    with pytest.raises(RuntimeError, match="Failed"):
        remote.run_publisher_job()


def _corpus():
    from governedgraph.common.config import DATA_DIR

    return load_corpus((DATA_DIR / "content" / "approved_content.jsonl").read_text())


def test_b3_every_approved_passage_is_reachable_in_every_journey_state():
    # A state-scoped passage silently disappeared for returning members and left a question with no grounded source.
    states = ("first_contact", "second_contact", "letter_received", "values_reviewed", "objection_raised")
    for doc in (doc for doc in _corpus() if doc.status == "approved"):
        assert all("*" in doc.journey_states or state in doc.journey_states for state in states), doc.id


def test_b3_dutch_content_has_parity_with_english_and_every_passage_lists_its_questions():
    approved = [doc for doc in _corpus() if doc.status == "approved"]
    english = {int(doc.content_id[2:]) for doc in approved if doc.language == "en"}
    dutch = {int(doc.content_id[2:]) - 100 for doc in approved if doc.language == "nl"}
    assert english == dutch
    assert all(doc.answers.strip() and "?" in doc.answers for doc in approved)


def test_b3_superseded_versions_are_retired_stubs_never_retrievable():
    docs = _corpus()
    assert all(doc.body == "SUPERSEDED VERSION - must never be retrieved." for doc in docs if doc.status == "retired")


def test_b3_later_years_question_retrieves_the_yearly_adjustment_passage_at_second_contact(local_counsellor):
    from counsellor_doubles import LocalContentSearch

    hits = LocalContentSearch().search("I am still worried about what could happen in later years", "second_contact")
    assert "C-003-v1.2" in {hit.id for hit in hits}


def test_b3_search_index_ranks_and_searches_the_questions_field():
    docs = {field.name: field for field in ingest.build_index().fields}
    assert docs["answers"].searchable
    semantic = ingest.build_index().semantic_search.configurations[0].prioritized_fields.content_fields
    assert {field.field_name for field in semantic} == {"body", "answers"}
