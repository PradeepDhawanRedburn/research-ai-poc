"""Local Chroma store, one collection per solution, so both can be queried side by side."""
from __future__ import annotations

from functools import lru_cache

import chromadb
from langchain_chroma import Chroma
from langchain_core.documents import Document

from .azure_models import embeddings
from .config import settings


def vector_store(source: str) -> Chroma:
    return Chroma(
        collection_name=settings.collection(source),
        embedding_function=embeddings(),
        client=_client(),
        collection_metadata={"hnsw:space": "cosine"},
    )


@lru_cache(maxsize=1)
def _client() -> chromadb.ClientAPI:
    settings.chroma_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(settings.chroma_dir),
                                     settings=chromadb.config.Settings(anonymized_telemetry=False))


def replace_document(vs: Chroma, doc_id: str, docs: list[Document], batch: int = 200) -> None:
    """Idempotent re-ingest: drop the report's old chunks, add the new ones with stable ids."""
    vs.delete(where={"doc_id": doc_id})
    for i in range(0, len(docs), batch):
        part = docs[i:i + batch]
        vs.add_documents(part, ids=[f"{doc_id}:{d.metadata['chunk']:05d}" for d in part])


def all_documents(vs: Chroma) -> list[Document]:
    got = vs.get(include=["documents", "metadatas"])
    return [Document(page_content=t, metadata=m or {}, id=i)
            for i, t, m in zip(got["ids"], got["documents"], got["metadatas"])]
