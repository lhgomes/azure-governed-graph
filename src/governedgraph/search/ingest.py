"""P3 approved-content feed -> managed-identity indexer -> integrated embeddings.

ILLUSTRATIVE - synthetic data. Run ``python -m governedgraph.search.ingest`` to seed the
feed once and reindex it. Later runs preserve the authoritative approved feed.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from governedgraph.common import domain
from governedgraph.common.config import DATA_DIR, settings
from governedgraph.search.corpus import ContentDocument, approve_version, load_corpus

log = logging.getLogger("governedgraph.search.ingest")
VECTOR_DIMENSIONS = 3072


def build_index():
    from azure.search.documents.indexes.models import (
        AzureOpenAIVectorizer, AzureOpenAIVectorizerParameters, HnswAlgorithmConfiguration,
        SearchableField, SearchField, SearchFieldDataType, SearchIndex, SemanticConfiguration,
        SemanticField, SemanticPrioritizedFields, SemanticSearch, SimpleField, VectorSearch,
        VectorSearchProfile,
    )

    fields = [
        SimpleField(name="id", type="Edm.String", key=True),
        *[SimpleField(name=name, type="Edm.String", filterable=True)
          for name in ("content_id", "version", "status", "language")],
        SearchField(name="journey_states", type="Collection(Edm.String)", filterable=True),
        *[SimpleField(name=name, type="Edm.String") for name in ("approval_ref", "source_id", "watermark")],
        SearchableField(name="title", type="Edm.String"),
        SearchableField(name="body", type="Edm.String"),
        SearchField(name="body_vector", type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                    searchable=True, hidden=True, vector_search_dimensions=VECTOR_DIMENSIONS,
                    vector_search_profile_name="approved-content-vector"),
    ]
    return SearchIndex(
        name=settings.search_index, fields=fields,
        vector_search=VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name="approved-content-hnsw")],
            profiles=[VectorSearchProfile(name="approved-content-vector",
                                          algorithm_configuration_name="approved-content-hnsw",
                                          vectorizer_name="embedding")],
            vectorizers=[AzureOpenAIVectorizer(
                vectorizer_name="embedding",
                parameters=AzureOpenAIVectorizerParameters(
                    resource_url=settings.embedding_endpoint, deployment_name=settings.embedding_deployment,
                    model_name=settings.embedding_model))]),
        semantic_search=SemanticSearch(configurations=[SemanticConfiguration(
            name="default", prioritized_fields=SemanticPrioritizedFields(
                title_field=SemanticField(field_name="title"),
                content_fields=[SemanticField(field_name="body")]))]),
    )


def build_skillset():
    from azure.search.documents.indexes.models import (
        AzureOpenAIEmbeddingSkill, InputFieldMappingEntry, OutputFieldMappingEntry, SearchIndexerSkillset,
    )

    return SearchIndexerSkillset(name=f"{settings.search_index}-embedding", skills=[
        AzureOpenAIEmbeddingSkill(
            name="approved-content-embedding", context="/document",
            resource_url=settings.embedding_endpoint, deployment_name=settings.embedding_deployment,
            model_name=settings.embedding_model, dimensions=VECTOR_DIMENSIONS,
            inputs=[InputFieldMappingEntry(name="text", source="/document/body")],
            outputs=[OutputFieldMappingEntry(name="embedding", target_name="body_vector")]),
    ])


def require_success(results) -> None:
    failed = [result.key for result in results if not result.succeeded]
    if failed:
        log.error("content_index_update_failed", extra={"document_ids": failed})
        raise RuntimeError("content index update failed; no replacement published")


def retire_previous(search_client, docs: list[ContentDocument], *, timeout: float = 120) -> None:
    approved = {doc.search_id for doc in docs if doc.status == "approved"}
    stale = {
        row["id"] for row in search_client.search(
            search_text="*", filter="status eq 'approved'", select=["id"])
        if row["id"] not in approved
    }
    if not stale:
        return
    require_success(search_client.merge_documents([{"id": key, "status": "retired"} for key in sorted(stale)]))
    deadline = time.monotonic() + timeout
    while True:
        visible = {row["id"] for row in search_client.search(
            search_text="*", filter="status eq 'approved'", select=["id"])}
        if not (stale & visible):
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("retired content remains visible; replacement not published")
        time.sleep(2)


class AzureContentPublisher:
    def __init__(self):
        from azure.identity import DefaultAzureCredential
        from azure.search.documents import SearchClient
        from azure.search.documents.indexes import SearchIndexClient, SearchIndexerClient
        from azure.storage.blob import BlobClient

        required = (settings.search_endpoint, settings.content_storage_url,
                    settings.content_storage_resource_id, settings.embedding_endpoint)
        if not all(required) or settings.embedding_model != "text-embedding-3-large":
            raise ValueError("Search, content storage and the verified embedding model configuration are required")
        credential = DefaultAzureCredential()
        self.search = SearchClient(settings.search_endpoint, settings.search_index, credential)
        self.indexes = SearchIndexClient(settings.search_endpoint, credential)
        self.indexers = SearchIndexerClient(settings.search_endpoint, credential)
        self.blob = BlobClient(settings.content_storage_url, settings.content_container, "corpus.jsonl",
                               credential=credential)
        self.indexer_name = f"{settings.search_index}-indexer"

    def configure(self) -> None:
        from azure.search.documents.indexes.models import (
            SearchIndexerDataContainer, SearchIndexerDataSourceConnection,
        )

        self.indexes.create_or_update_index(build_index())
        self.indexers.create_or_update_skillset(build_skillset())
        self.indexers.create_or_update_data_source_connection(SearchIndexerDataSourceConnection(
            name=f"{settings.search_index}-source", type="azureblob",
            connection_string=f"ResourceId={settings.content_storage_resource_id};",
            container=SearchIndexerDataContainer(name=settings.content_container)))

    def run_indexer(self, *, timeout: float = 900) -> None:
        from azure.core.exceptions import HttpResponseError
        from azure.search.documents.indexes.models import (
            FieldMapping, IndexingParameters, IndexingParametersConfiguration, SearchIndexer,
        )

        started = datetime.now(timezone.utc)
        self.indexers.create_or_update_indexer(SearchIndexer(
            name=self.indexer_name, data_source_name=f"{settings.search_index}-source",
            target_index_name=settings.search_index, skillset_name=f"{settings.search_index}-embedding",
            parameters=IndexingParameters(max_failed_items=0, max_failed_items_per_batch=0,
                configuration=IndexingParametersConfiguration(parsing_mode="jsonLines",
                                                              execution_environment="private")),
            field_mappings=[FieldMapping(source_field_name="id", target_field_name="id")],
            output_field_mappings=[FieldMapping(source_field_name="/document/body_vector",
                                                target_field_name="body_vector")]))
        automatic = self.indexers.get_indexer_status(self.indexer_name).last_result
        if not automatic or not automatic.start_time or automatic.start_time < started:
            try:
                self.indexers.run_indexer(self.indexer_name)
            except HttpResponseError as error:
                if error.status_code != 409:
                    raise
                log.info("content_indexer_already_running")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self.indexers.get_indexer_status(self.indexer_name).last_result
            if result and result.start_time and result.start_time >= started and result.status != "inProgress":
                if result.status != "success" or result.failed_item_count or result.errors or result.warnings:
                    log.error("approved_content_indexer_failed")
                    raise RuntimeError("approved content indexer failed or warned; inspect indexer diagnostics")
                return
            time.sleep(2)
        raise TimeoutError("approved content indexer did not finish; do not start another publication yet")

    def publish(self, seed: list[ContentDocument], candidate: ContentDocument | None = None) -> None:
        from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError

        payload = "\n".join(json.dumps(doc.index_document()) for doc in seed)
        try:
            self.blob.upload_blob(payload, overwrite=False)
        except ResourceExistsError:
            log.info("using_existing_approved_content_feed")
        with self.blob.acquire_lease(lease_duration=-1) as lease:
            docs = load_corpus(self.blob.download_blob(lease=lease).content_as_text(), indexed=True)
            if candidate is not None:
                docs = approve_version(docs, candidate)
            try:
                result = self.indexers.get_indexer_status(self.indexer_name).last_result
            except ResourceNotFoundError:
                result = None
            if result and result.status == "inProgress":
                raise RuntimeError("indexer still running; retry publication after completion")
            self.configure()
            self.blob.upload_blob("\n".join(json.dumps(doc.index_document()) for doc in docs),
                                  overwrite=True, lease=lease)
            retire_previous(self.search, docs)
            self.run_indexer()
            expected = self.verify_index(docs)
            log.info("approved_content_indexed", extra={"document_count": len(expected)})
            print(f"indexed {len(expected)} approved documents into {settings.search_index}", flush=True)

    def verify_index(self, docs: list[ContentDocument], *, timeout: float = 120) -> set[str]:
        from azure.search.documents.models import VectorizableTextQuery

        expected = {doc.search_id: (doc.id, doc.version, doc.approval_ref, doc.language)
                    for doc in docs if doc.status == "approved"}
        documents = {doc.search_id: doc for doc in docs if doc.status == "approved"}
        deadline = time.monotonic() + timeout
        while True:
            actual = {row["id"]: (row["source_id"], row["version"], row["approval_ref"], row["language"])
                      for row in self.search.search(search_text="*", filter="status eq 'approved'",
                          select=["id", "source_id", "version", "approval_ref", "language"])}
            vectors: set[str] = set()
            if actual == expected:
                ids = sorted(expected)
                for offset in range(0, len(ids), 100):
                    batch = ids[offset:offset + 100]
                    conditions = []
                    for key in batch:
                        terms = []
                        for field in ("content_id", "version", "language"):
                            value = getattr(documents[key], field).replace("'", "''")
                            terms.append(f"{field} eq '{value}'")
                        conditions.append("(" + " and ".join(terms) + ")")
                    results = self.search.search(
                        search_text=None, select=["id"], top=len(batch), vector_filter_mode="preFilter",
                        filter="status eq 'approved' and (" + " or ".join(conditions) + ")",
                        vector_queries=[VectorizableTextQuery(text=domain.vocabulary()["search_probe_query"], fields="body_vector",
                            k_nearest_neighbors=len(batch), exhaustive=True)])
                    vectors.update(row["id"] for row in results)
                if vectors == set(expected):
                    return vectors
            if time.monotonic() >= deadline:
                raise RuntimeError("approved index provenance or integrated vectors differ from the governed feed")
            time.sleep(2)


def main() -> None:
    docs = load_corpus((DATA_DIR / "content" / "approved_content.jsonl").read_text())
    AzureContentPublisher().publish(docs)


if __name__ == "__main__":
    main()
