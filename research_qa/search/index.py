"""The Markdown+ index: chunks from the store, embedded here, searched by the knowledge base.

The Word arm has no code here: Azure AI Search builds and fills its index itself from the Blob knowledge source
(see knowledge.py). The schema mirrors schema.Chunk; `assets` holds store keys (never local paths) and `has_facts`
tells the agent that lookup_facts has exact values for the figure.
"""
from __future__ import annotations

from collections import Counter

from ..config import ARMS, settings
from ..schema import Chunk
from ..storage import CHUNKS, Store
from .azure import embed, index_client, search_client

ARM = "markdown-plus"
VECTOR_FIELD = "page_embedding_text_3_large"
VECTOR_PROFILE = "hnsw_text_3_large"
VECTORIZER = "azure_openai_text_3_large"
SEMANTIC_CONFIG = "semantic_config"
# fields the knowledge source hands to the agent (no vector: it would bloat every citation)
SOURCE_DATA_FIELDS = ("id", "page_chunk", "doc_id", "title", "date", "section", "kind", "caption", "figure",
                      "source_file", "assets", "has_facts")


def index_name(arm: str = ARM) -> str:
    return settings.names(arm).index


def build_index():
    from azure.search.documents.indexes.models import (
        AzureOpenAIVectorizer, AzureOpenAIVectorizerParameters, HnswAlgorithmConfiguration, SearchField, SearchIndex,
        SemanticConfiguration, SemanticField, SemanticPrioritizedFields, SemanticSearch, VectorSearch,
        VectorSearchProfile,
    )

    meta = dict(filterable=True, facetable=True)
    return SearchIndex(
        name=index_name(),
        fields=[
            SearchField(name="id", type="Edm.String", key=True, filterable=True, sortable=True),
            SearchField(name="page_chunk", type="Edm.String", searchable=True),
            SearchField(name=VECTOR_FIELD, type="Collection(Edm.Single)", stored=False,
                        vector_search_dimensions=settings.embedding_dimensions,
                        vector_search_profile_name=VECTOR_PROFILE),
            SearchField(name="doc_id", type="Edm.String", searchable=True, **meta),
            SearchField(name="title", type="Edm.String", searchable=True),
            SearchField(name="date", type="Edm.String", sortable=True, **meta),
            SearchField(name="section", type="Edm.String", searchable=True),
            SearchField(name="kind", type="Edm.String", **meta),
            SearchField(name="caption", type="Edm.String", searchable=True),
            SearchField(name="figure", type="Edm.String", searchable=True, **meta),
            SearchField(name="source_file", type="Edm.String", **meta),
            SearchField(name="assets", type="Edm.String"),            # ';'-joined store keys of the pictures
            SearchField(name="has_facts", type="Edm.Boolean", filterable=True),
        ],
        vector_search=VectorSearch(
            profiles=[VectorSearchProfile(name=VECTOR_PROFILE, algorithm_configuration_name="alg",
                                          vectorizer_name=VECTORIZER)],
            algorithms=[HnswAlgorithmConfiguration(name="alg")],
            vectorizers=[AzureOpenAIVectorizer(
                vectorizer_name=VECTORIZER,
                parameters=AzureOpenAIVectorizerParameters(
                    resource_url=settings.search_openai_endpoint,
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
                    keywords_fields=[SemanticField(field_name="section"), SemanticField(field_name="figure")],
                ),
            )],
        ),
    )


def clear_index(name: str) -> int:
    """Delete every document, keeping the index: a knowledge source references it, so it cannot be deleted, and
    recreating it would mean recreating the knowledge source, base and agent too."""
    search = search_client(name)
    removed = 0
    while True:
        ids = [r["id"] for r in search.search("*", select=["id"], top=1000)]
        if not ids:
            return removed
        search.delete_documents([{"id": i} for i in ids])
        removed += len(ids)


def create_index(reset: bool = False) -> str:
    settings.require("search_endpoint", "openai_endpoint")
    client, name = index_client(), index_name()
    if reset and name in client.list_index_names():
        print(f"[{ARM}] index '{name}': {clear_index(name)} documents removed")
    client.create_or_update_index(build_index())  # applies field or config changes to an existing index in place
    print(f"[{ARM}] index '{name}' ready")
    return name


def to_document(c: Chunk, vector: list[float]) -> dict:
    return {
        "id": c.id, "page_chunk": c.text, VECTOR_FIELD: vector, "doc_id": c.doc_id, "title": c.title,
        "date": c.date or "", "section": c.section or "", "kind": c.kind, "caption": c.caption or "",
        "figure": c.figure or "", "source_file": c.source_file or "", "assets": ";".join(c.assets),
        "has_facts": bool(c.has_facts),
    }


def push(store: Store, doc_ids: list[str] | None = None) -> int:
    """Upload the chunks of each report from chunks/<doc>.jsonl, replacing that report's old chunks."""
    from azure.search.documents import SearchIndexingBufferedSender

    from .azure import credential

    name = index_name()
    search = search_client(name)
    ids = doc_ids or [k.rsplit("/", 1)[-1][:-len(".jsonl")] for k in store.keys(f"{CHUNKS}/", ".jsonl")]
    total = 0
    for doc_id in ids:
        chunks = [Chunk.from_dict(r) for r in store.jsonl(f"{CHUNKS}/{doc_id}.jsonl")]
        old = [r["id"] for r in search.search("*", filter=f"doc_id eq '{doc_id}'", select=["id"], top=1000)]
        if old:
            search.delete_documents([{"id": i} for i in old])
        vectors = embed([c.text for c in chunks])
        docs = [to_document(c, v) for c, v in zip(chunks, vectors)]
        with SearchIndexingBufferedSender(endpoint=settings.search_endpoint, index_name=name,
                                          credential=credential()) as sender:
            sender.upload_documents(documents=docs)
        total += len(docs)
        kinds = Counter(c.kind for c in chunks)
        print(f"[{ARM}] {doc_id}: {len(docs)} chunks ({', '.join(f'{v} {k}' for k, v in sorted(kinds.items()))})")
    return total


def stats(arm: str = ARM) -> dict:
    """Chunk count and chunks per source file, read from the live index. For the Word arm the index is the one
    the Blob knowledge source created; its name is read from the knowledge source (knowledge.py)."""
    if arm == ARM:
        name = index_name()
    else:
        from .knowledge import word_index_name

        name = word_index_name()
    if not name:
        return {"index": "", "chunks": 0, "files": {}}
    search = search_client(name)
    facets = ["source_file,count:50"] if arm == ARM else []
    res = search.search("*", top=0, include_total_count=True, facets=facets or None)
    f = res.get_facets() or {}
    return {"index": name, "chunks": res.get_count(),
            "files": {x["value"]: x["count"] for x in f.get("source_file", [])}}


def get_chunk(chunk_id: str) -> dict:
    """One Markdown+ chunk by id, with `assets` as a list of store keys. {} when unknown."""
    try:
        doc = search_client(index_name()).get_document(
            key=chunk_id, selected_fields=["id", "page_chunk", "kind", "caption", "figure", "doc_id", "assets",
                                            "has_facts"])
    except Exception:  # noqa: BLE001 - unknown chunk id
        return {}
    return {"id": doc.get("id", ""), "text": doc.get("page_chunk", ""), "kind": doc.get("kind", ""),
            "caption": doc.get("caption", ""), "figure": doc.get("figure", ""), "doc_id": doc.get("doc_id", ""),
            "assets": [a for a in (doc.get("assets") or "").split(";") if a], "has_facts": bool(doc.get("has_facts"))}


def arm_label(arm: str) -> str:
    return ARMS[arm].label
