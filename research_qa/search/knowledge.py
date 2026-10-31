"""Knowledge sources and knowledge bases for both arms (docs/DESIGN.md §3.4 and §3.5).

- Markdown+: a search-index knowledge source over the index that index.py fills from the store.
- Word: an Azure Blob knowledge source over `raw/` in the store container. Azure AI Search cracks the .docx,
  chunks, embeds and verbalises pictures itself, with no code of ours. That is the baseline.

Both get a knowledge base with the same settings (extractive output, minimal retrieval reasoning, the same number
of passages per retrieval), so the only difference between the arms is the input.

Preview surface: the Blob knowledge source and the knowledge base are preview features of azure-search-documents
12.1.0b2 and API version 2026-08-01-preview. Everything that depends on their exact SDK shape is in
`_blob_knowledge_source()`, `_knowledge_base()` and `_status_*()`; each falls back to the REST API, so a mismatch
with the installed SDK is a one-place fix.
"""
from __future__ import annotations

from ..config import ARMS, settings
from .azure import index_client, search_rest
from .index import SEMANTIC_CONFIG, SOURCE_DATA_FIELDS, index_name

WORD = "word"
MARKDOWN = "markdown-plus"
RAW_PREFIX = "raw/"


def _storage_connection() -> str:
    """How the search service reaches the container. A connection string with a key when given, else the
    managed-identity form (ResourceId=...), which needs the storage account's ARM id (STORAGE_RESOURCE_ID) and the
    search service's identity holding Storage Blob Data Reader on it."""
    if settings.storage_connection_string:
        return settings.storage_connection_string
    rid = settings.storage_resource_id
    if not rid:
        raise RuntimeError("the Word arm needs STORAGE_CONNECTION_STRING or STORAGE_RESOURCE_ID "
                           "(/subscriptions/.../storageAccounts/<account>) so Azure AI Search can read raw/")
    return f"ResourceId={rid};"


def _aoai(deployment: str, model: str) -> dict:
    return {"resourceUri": settings.search_openai_endpoint, "deploymentId": deployment, "modelName": model}


# ----------------------------------------------------------------------------- knowledge sources

def _index_knowledge_source() -> None:
    from azure.search.documents.indexes.models import (
        SearchIndexFieldReference, SearchIndexKnowledgeSource, SearchIndexKnowledgeSourceParameters,
    )

    n = settings.names(MARKDOWN)
    index_client().create_or_update_knowledge_source(knowledge_source=SearchIndexKnowledgeSource(
        name=n.knowledge_source,
        description="Research reports as Markdown+: text, tables, chart values, matrix states, picture descriptions",
        search_index_parameters=SearchIndexKnowledgeSourceParameters(
            search_index_name=index_name(),
            semantic_configuration_name=SEMANTIC_CONFIG,
            source_data_fields=[SearchIndexFieldReference(name=f) for f in SOURCE_DATA_FIELDS],
        ),
    ))


def _blob_knowledge_source() -> None:
    """Word arm. SDK path relies on azure-search-documents 12.1.0b2 classes `AzureBlobKnowledgeSource`,
    `AzureBlobKnowledgeSourceParameters`, `KnowledgeSourceIngestionParameters`, `KnowledgeSourceAzureOpenAIVectorizer`,
    `KnowledgeBaseAzureOpenAIModel` and `AzureOpenAIVectorizerParameters`. If any is missing or takes other arguments,
    the REST body below is sent instead (api-version 2026-08-01-preview). In both paths the embedding and chat models
    sit under ingestionParameters, as in the service's documented example."""
    n = settings.names(WORD)
    description = ("Research reports as Word files, indexed by Azure AI Search's own pipeline: document cracking, "
                   "chunking, embeddings and image verbalisation")
    try:
        from azure.search.documents.indexes.models import (
            AzureBlobKnowledgeSource, AzureBlobKnowledgeSourceParameters, AzureOpenAIVectorizerParameters,
            KnowledgeBaseAzureOpenAIModel, KnowledgeSourceAzureOpenAIVectorizer, KnowledgeSourceIngestionParameters,
        )

        params = AzureOpenAIVectorizerParameters(resource_url=settings.search_openai_endpoint,
                                                 deployment_name=settings.embedding_deployment,
                                                 model_name=settings.embedding_model)
        chat = AzureOpenAIVectorizerParameters(resource_url=settings.search_openai_endpoint,
                                               deployment_name=settings.answer_model,
                                               model_name=settings.answer_model)
        index_client().create_or_update_knowledge_source(knowledge_source=AzureBlobKnowledgeSource(
            name=n.knowledge_source, description=description,
            azure_blob_parameters=AzureBlobKnowledgeSourceParameters(
                connection_string=_storage_connection(),
                container_name=settings.storage_container,
                folder_path=RAW_PREFIX,
                ingestion_parameters=KnowledgeSourceIngestionParameters(
                    embedding_model=KnowledgeSourceAzureOpenAIVectorizer(azure_open_ai_parameters=params),
                    chat_completion_model=KnowledgeBaseAzureOpenAIModel(azure_open_ai_parameters=chat),
                ),
            ),
        ))
        return
    except (ImportError, TypeError, AttributeError) as e:
        print(f"[{WORD}] SDK knowledge-source classes did not fit ({type(e).__name__}: {e}); using the REST API")
    body = {
        "name": n.knowledge_source,
        "kind": "azureBlob",
        "description": description,
        "azureBlobParameters": {
            "connectionString": _storage_connection(),
            "containerName": settings.storage_container,
            "folderPath": RAW_PREFIX,
            "ingestionParameters": {
                "embeddingModel": {
                    "kind": "azureOpenAI",
                    "azureOpenAIParameters": _aoai(settings.embedding_deployment, settings.embedding_model),
                },
                "chatCompletionModel": {
                    "kind": "azureOpenAI",
                    "azureOpenAIParameters": _aoai(settings.answer_model, settings.answer_model),
                },
            },
        },
    }
    search_rest("PUT", f"knowledgesources/{n.knowledge_source}", body)


def knowledge_source_json(arm: str) -> dict:
    """The knowledge source as the service holds it (REST), e.g. to read the resources a Blob source created."""
    try:
        return search_rest("GET", f"knowledgesources/{settings.names(arm).knowledge_source}")
    except RuntimeError:
        return {}


def word_index_name() -> str:
    """The index the Word arm's Blob knowledge source created for itself."""
    ks = knowledge_source_json(WORD)
    created = (ks.get("azureBlobParameters") or {}).get("createdResources") or {}
    return created.get("index") or (f"{settings.names(WORD).knowledge_source}-index" if ks else "")


# ----------------------------------------------------------------------------- knowledge bases

def _knowledge_base(arm: str, max_docs: int) -> None:
    """Relies on `KnowledgeBase`, `KnowledgeSourceReference`, `KnowledgeBaseRetrieveDefaults` and
    `KnowledgeRetrievalMinimalReasoningEffort` (12.1.0b2); falls back to the REST body."""
    n = settings.names(arm)
    description = f"Research reports, {ARMS[arm].label}"
    tags = {"arm": arm, "chunkchars": str(settings.chunk_chars) if arm == MARKDOWN else "service"}
    try:
        from azure.search.documents.indexes.models import (
            KnowledgeBase, KnowledgeBaseRetrieveDefaults, KnowledgeSourceReference,
        )
        from azure.search.documents.knowledgebases.models import KnowledgeRetrievalMinimalReasoningEffort

        index_client().create_or_update_knowledge_base(knowledge_base=KnowledgeBase(
            name=n.knowledge_base, description=description,
            knowledge_sources=[KnowledgeSourceReference(name=n.knowledge_source)],
            output_mode="extractiveData",  # verbatim passages: the agent reasons over the source, not a pre-made answer
            retrieval_reasoning_effort=KnowledgeRetrievalMinimalReasoningEffort(),  # no LLM query planning in search
            retrieve_defaults=KnowledgeBaseRetrieveDefaults(max_output_documents=max_docs),
            tags=tags,
        ))
        return
    except (ImportError, TypeError, AttributeError) as e:
        print(f"[{arm}] SDK knowledge-base classes did not fit ({type(e).__name__}: {e}); using the REST API")
    search_rest("PUT", f"knowledgebases/{n.knowledge_base}", {
        "name": n.knowledge_base, "description": description,
        "knowledgeSources": [{"name": n.knowledge_source}],
        "outputMode": "extractiveData",
        "retrievalReasoningEffort": {"kind": "minimal"},
        "retrieveDefaults": {"maxOutputDocuments": max_docs},
        "tags": tags,
    })


def create_knowledge(arm: str, max_docs: int | None = None) -> None:
    """Knowledge source and knowledge base of one arm. Word: Azure starts ingesting raw/ on creation; watch it with
    blob_ingestion_status()."""
    settings.require("search_endpoint", "openai_endpoint")
    (_blob_knowledge_source if arm == WORD else _index_knowledge_source)()
    n = settings.names(arm)
    _knowledge_base(arm, max_docs or settings.max_docs)
    print(f"[{arm}] knowledge source '{n.knowledge_source}' and knowledge base '{n.knowledge_base}' ready "
          f"(max {max_docs or settings.max_docs} passages per retrieval)\n  MCP endpoint: {n.mcp_endpoint}")


def kb_settings(arm: str) -> dict:
    """max_docs and tags as stored on the knowledge base; {} when it does not exist."""
    try:
        kb = search_rest("GET", f"knowledgebases/{settings.names(arm).knowledge_base}")
    except RuntimeError:
        return {}
    return {"max_docs": (kb.get("retrieveDefaults") or {}).get("maxOutputDocuments"),
            "tags": kb.get("tags") or {}, "output_mode": kb.get("outputMode"),
            "sources": [s.get("name") for s in kb.get("knowledgeSources", [])]}


def set_max_docs(arm: str, n: int) -> None:
    _knowledge_base(arm, n)


def blob_ingestion_status(arm: str = WORD) -> dict:
    """Where the Word arm's ingestion stands: the indexer the Blob knowledge source created (status, last run,
    items processed and failed). Tries the knowledge source status endpoint first, then the created indexer."""
    n = settings.names(arm)
    try:
        st = search_rest("GET", f"knowledgesources/{n.knowledge_source}/status")
        if st:
            return {"source": "knowledge source", **st}
    except RuntimeError:
        pass
    ks = knowledge_source_json(arm)
    created = (ks.get("azureBlobParameters") or {}).get("createdResources") or {}
    indexer = created.get("indexer")
    if not indexer:
        return {"status": "unknown", "detail": "knowledge source not found or no indexer recorded yet"}
    try:
        from azure.search.documents.indexes import SearchIndexerClient

        from .azure import credential

        s = SearchIndexerClient(endpoint=settings.search_endpoint, credential=credential()).get_indexer_status(indexer)
        last = s.last_result
        return {"source": f"indexer {indexer}", "status": str(s.status),
                "last_run": {"status": str(getattr(last, "status", "")), "items": getattr(last, "item_count", None),
                             "failed": getattr(last, "failed_item_count", None),
                             "end": str(getattr(last, "end_time", "")),
                             "error": getattr(last, "error_message", None)} if last else None,
                "index": created.get("index")}
    except Exception as e:  # noqa: BLE001
        return {"source": f"indexer {indexer}", "status": "unknown", "detail": f"{type(e).__name__}: {str(e)[:200]}"}


def delete_knowledge(arm: str) -> None:
    """Knowledge base, then knowledge source (the service refuses the other order)."""
    n, client = settings.names(arm), index_client()
    for what, fn, name in (("knowledge base", client.delete_knowledge_base, n.knowledge_base),
                           ("knowledge source", client.delete_knowledge_source, n.knowledge_source)):
        try:
            fn(name)
            print(f"[{arm}] deleted {what} '{name}'")
        except Exception as e:  # noqa: BLE001 - already gone
            print(f"[{arm}] {what} '{name}': {type(e).__name__}: {str(e)[:120]}")


def delete_all(arm: str) -> None:
    """Everything the search service holds for one arm: base, source, and for Markdown+ the index. The Word arm's
    index, indexer, skillset and data source are deleted by the service with its knowledge source. The agent and
    its connection are deleted by agent.foundry.delete_agent."""
    delete_knowledge(arm)
    if arm == MARKDOWN:
        try:
            index_client().delete_index(index_name())
            print(f"[{arm}] deleted index '{index_name()}'")
        except Exception as e:  # noqa: BLE001
            print(f"[{arm}] index '{index_name()}': {type(e).__name__}: {str(e)[:120]}")