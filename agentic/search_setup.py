"""Azure AI Search side of the pipeline: index, documents, knowledge source and knowledge base.

Follows the Azure AI Search agentic retrieval tutorial, with the research reports (chunked by `rag/`) in place of
the tutorial's NASA sample. The search service embeds queries itself through the vectorizer in the index, so only
ingestion needs an embedding client.
"""
from __future__ import annotations

from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient, SearchIndexingBufferedSender
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    AzureOpenAIVectorizer, AzureOpenAIVectorizerParameters, HnswAlgorithmConfiguration, KnowledgeBase,
    KnowledgeBaseRetrieveDefaults, KnowledgeSourceReference, SearchField, SearchIndex, SearchIndexFieldReference,
    SearchIndexKnowledgeSource,
    SearchIndexKnowledgeSourceParameters, SemanticConfiguration, SemanticField, SemanticPrioritizedFields,
    SemanticSearch, VectorSearch, VectorSearchProfile,
)
from azure.search.documents.knowledgebases.models import KnowledgeRetrievalMinimalReasoningEffort

from .config import settings

VECTOR_FIELD = "page_embedding_text_3_large"
EMBEDDING_DIMENSIONS = 3072  # text-embedding-3-large
SEMANTIC_CONFIG = "semantic_config"


def credential() -> DefaultAzureCredential:
    # az can be slow to hand out a token; the default 10 s timeout makes the first call fail now and then
    return DefaultAzureCredential(process_timeout=60)


def index_client() -> SearchIndexClient:
    settings.require("search_endpoint")
    return SearchIndexClient(endpoint=settings.search_endpoint, credential=credential())


def build_index(source: str) -> SearchIndex:
    meta = dict(filterable=True, facetable=True)
    return SearchIndex(
        name=settings.names(source).index,
        fields=[
            SearchField(name="id", type="Edm.String", key=True, filterable=True, sortable=True, facetable=True),
            SearchField(name="page_chunk", type="Edm.String", searchable=True),
            SearchField(name=VECTOR_FIELD, type="Collection(Edm.Single)", stored=False,
                        vector_search_dimensions=EMBEDDING_DIMENSIONS, vector_search_profile_name="hnsw_text_3_large"),
            SearchField(name="doc_id", type="Edm.String", searchable=True, **meta),
            SearchField(name="title", type="Edm.String", searchable=True),
            SearchField(name="date", type="Edm.String", sortable=True, **meta),
            SearchField(name="section", type="Edm.String", searchable=True),
            SearchField(name="kind", type="Edm.String", **meta),
            SearchField(name="caption", type="Edm.String", searchable=True),
            # which loader produced the chunk (docx or markdown) and from which file
            SearchField(name="source", type="Edm.String", **meta),
            SearchField(name="source_file", type="Edm.String", **meta),
            # rasterised picture(s) of a figure chunk on this machine, ';'-separated; read by the agent's inspect_figure tool
            SearchField(name="image_path", type="Edm.String"),
        ],
        vector_search=VectorSearch(
            profiles=[VectorSearchProfile(name="hnsw_text_3_large", algorithm_configuration_name="alg",
                                          vectorizer_name="azure_openai_text_3_large")],
            algorithms=[HnswAlgorithmConfiguration(name="alg")],
            vectorizers=[AzureOpenAIVectorizer(
                vectorizer_name="azure_openai_text_3_large",
                parameters=AzureOpenAIVectorizerParameters(
                    resource_url=settings.openai_endpoint,
                    deployment_name=settings.embedding_deployment,
                    model_name=settings.embedding_model,
                ),
            )],
        ),
        semantic_search=SemanticSearch(
            default_configuration_name=SEMANTIC_CONFIG,
            configurations=[SemanticConfiguration(
                name=SEMANTIC_CONFIG,
                prioritized_fields=SemanticPrioritizedFields(
                    title_field=SemanticField(field_name="title"),
                    content_fields=[SemanticField(field_name="page_chunk")],
                    keywords_fields=[SemanticField(field_name="section")],
                ),
            )],
        ),
    )


def create_index(source: str, reset: bool = False) -> None:
    settings.require("openai_endpoint")
    client, name = index_client(), settings.names(source).index
    if reset and name in client.list_index_names():
        client.delete_index(name)
    client.create_or_update_index(build_index(source))
    print(f"[{source}] index '{name}' ready")


def _embedder():
    from openai import AzureOpenAI

    kw = {"azure_endpoint": settings.embedding_endpoint, "api_version": settings.api_version, "max_retries": 6}
    if settings.embedding_api_key:
        kw["api_key"] = settings.embedding_api_key
    else:
        from azure.identity import get_bearer_token_provider

        kw["azure_ad_token_provider"] = get_bearer_token_provider(
            credential(), "https://cognitiveservices.azure.com/.default")
    return AzureOpenAI(**kw)


def _embed(texts: list[str], batch: int = 64) -> list[list[float]]:
    client, out = _embedder(), []
    for i in range(0, len(texts), batch):
        resp = client.embeddings.create(model=settings.embedding_deployment, input=texts[i:i + batch])
        out.extend(d.embedding for d in resp.data)
    return out


def _load(source: str, inputs: list[str] | None, vision: str):
    """Yield (path, DocInfo, chunks) per report, reusing the loaders and chunking of the local RAG."""
    from rag.chunking import to_documents

    if source == "docx":
        from rag.loaders.docx_source import discover, load_docx as load

        paths = discover(inputs, settings.docx_dir)
    else:
        from rag.loaders.markdown_source import discover, load_markdown as load

        paths = discover(inputs, settings.markdown_dir)
    for path in paths:
        info, blocks = load(path, vision=vision)
        yield path, info, to_documents(info, blocks, source)


def ingest(source: str, inputs: list[str] | None = None, vision: str = "auto") -> int:
    index = settings.names(source).index
    search = SearchClient(endpoint=settings.search_endpoint, index_name=index, credential=credential())
    total = 0
    for path, info, chunks in _load(source, inputs, vision):
        old = [r["id"] for r in search.search("*", filter=f"doc_id eq '{info.doc_id}'", select=["id"], top=1000)]
        if old:
            search.delete_documents([{"id": i} for i in old])
        vectors = _embed([c.page_content for c in chunks])
        docs = [{
            "id": f"{info.doc_id}-{c.metadata['chunk']:05d}",
            "page_chunk": c.page_content,
            VECTOR_FIELD: v,
            "doc_id": info.doc_id,
            "title": " - ".join(x for x in (info.title, info.subtitle) if x),
            "date": info.date or "",
            "section": c.metadata["section"],
            "kind": c.metadata["kind"],
            "caption": c.metadata["caption"],
            "source": source,
            "source_file": path.name,
            "image_path": c.metadata.get("image_path") or "",
        } for c, v in zip(chunks, vectors)]
        with SearchIndexingBufferedSender(endpoint=settings.search_endpoint, index_name=index,
                                          credential=credential()) as sender:
            sender.upload_documents(documents=docs)
        total += len(docs)
        print(f"[{source}] {info.doc_id}: {len(docs)} chunks from {path.name}  {info.header()}")
    record_chunk_size(source)
    return total


def create_knowledge_base(source: str, max_docs: int | None = None) -> None:
    n, client = settings.names(source), index_client()
    client.create_or_update_knowledge_source(knowledge_source=SearchIndexKnowledgeSource(
        name=n.knowledge_source,
        description=f"Research reports ({source} source): text, tables, chart data and figure descriptions",
        search_index_parameters=SearchIndexKnowledgeSourceParameters(
            search_index_name=n.index,
            semantic_configuration_name=SEMANTIC_CONFIG,
            # human-readable fields only: embeddings would bloat the citations
            source_data_fields=[SearchIndexFieldReference(name=f) for f in (
                "id", "page_chunk", "doc_id", "title", "date", "section", "kind", "caption", "source",
                "source_file", "image_path")],
        ),
    ))
    client.create_or_update_knowledge_base(knowledge_base=KnowledgeBase(
        name=n.knowledge_base,
        description=f"Research reports, {source} solution",
        knowledge_sources=[KnowledgeSourceReference(name=n.knowledge_source)],
        output_mode="extractiveData",  # verbatim passages: the agent reasons over the source, not a pre-made answer
        retrieval_reasoning_effort=KnowledgeRetrievalMinimalReasoningEffort(),  # no LLM query planning in search
        # the knob for "how many chunks does the agent get": applies to every retrieval through MCP
        retrieve_defaults=KnowledgeBaseRetrieveDefaults(max_output_documents=max_docs or settings.max_docs),
        tags={"chunkchars": str(settings.chunk_chars)},
    ))
    print(f"[{source}] knowledge source '{n.knowledge_source}' and knowledge base '{n.knowledge_base}' ready"
          f" (max {max_docs or settings.max_docs} passages per retrieval)\n  MCP endpoint: {n.mcp_endpoint}")


def record_chunk_size(source: str) -> None:
    """Remember on the knowledge base which chunk size its index was built with (shown in the UI)."""
    client = index_client()
    try:
        kb = client.get_knowledge_base(settings.names(source).knowledge_base)
    except Exception:  # noqa: BLE001 - knowledge base not created yet; create_knowledge_base records it
        return
    kb.tags = {**(kb.tags or {}), "chunkchars": str(settings.chunk_chars)}
    client.create_or_update_knowledge_base(knowledge_base=kb)


def knowledge_base_settings(source: str) -> dict:
    """What the live knowledge base is configured with: passages per retrieval and the chunk size of its index."""
    kb = index_client().get_knowledge_base(settings.names(source).knowledge_base)
    d = kb.retrieve_defaults
    return {"max_docs": getattr(d, "max_output_documents", None), "chunk_chars": (kb.tags or {}).get("chunkchars"),
            "output_mode": str(kb.output_mode), "reasoning": type(kb.retrieval_reasoning_effort).__name__}


def set_max_docs(source: str, max_docs: int) -> None:
    client = index_client()
    kb = client.get_knowledge_base(settings.names(source).knowledge_base)
    kb.retrieve_defaults = KnowledgeBaseRetrieveDefaults(max_output_documents=max_docs)
    client.create_or_update_knowledge_base(knowledge_base=kb)


def figure_chunk(source: str, chunk_id: str) -> dict:
    """Picture file(s) and text of a figure chunk, looked up in that solution's index."""
    search = SearchClient(endpoint=settings.search_endpoint, index_name=settings.names(source).index,
                          credential=credential())
    try:
        doc = search.get_document(key=chunk_id, selected_fields=["image_path", "page_chunk", "kind", "caption"])
    except Exception:  # noqa: BLE001 - unknown chunk id
        return {}
    return {"paths": [p for p in (doc.get("image_path") or "").split(";") if p], "text": doc.get("page_chunk", ""),
            "kind": doc.get("kind", ""), "caption": doc.get("caption", "")}


def index_stats(source: str) -> dict:
    """Chunk count and chunks per source file, read from the live index."""
    n = settings.names(source)
    search = SearchClient(endpoint=settings.search_endpoint, index_name=n.index, credential=credential())
    res = search.search("*", top=0, include_total_count=True, facets=["source_file,count:50", "source,count:5"])
    facets = res.get_facets() or {}
    return {
        "index": n.index,
        "chunks": res.get_count(),
        "files": {f["value"]: f["count"] for f in facets.get("source_file", [])},
        "sources": {f["value"]: f["count"] for f in facets.get("source", [])},
    }


def delete_search_objects(source: str) -> None:
    n, client = settings.names(source), index_client()
    for what, fn, arg in (
        ("knowledge base", client.delete_knowledge_base, n.knowledge_base),
        ("knowledge source", client.delete_knowledge_source, n.knowledge_source),
        ("index", client.delete_index, n.index),
    ):
        fn(arg)
        print(f"[{source}] deleted {what} '{arg}'")

