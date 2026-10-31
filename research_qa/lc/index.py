"""One Chroma collection per arm and a hybrid retriever over it (vector search + BM25, reciprocal rank fusion).

Ported from the Phase 0 `rag/` package. BM25 matters because many questions hinge on exact tokens (Fig 27, CCEP,
2030) that embeddings blur. Chroma persists under settings.chroma_dir; it is derived data, rebuilt from the store.
"""
from __future__ import annotations

import re
from functools import lru_cache

from ..config import ARMS, settings
from ..schema import Chunk
from ..storage import CHUNKS, Store

TOKEN_RE = re.compile(r"[a-z0-9]+(?:[./%-][a-z0-9]+)*")
BATCH = 100
FETCH = 30      # candidates from each ranker before fusion
RRF_K = 60

META_FIELDS = ("doc_id", "source_file", "kind", "section", "caption", "figure", "title", "date")


def collection_name(arm: str) -> str:
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}")
    return f"research-{arm}"


def toks(s: str) -> list[str]:
    return TOKEN_RE.findall(s.lower())


def _client():
    import chromadb

    settings.chroma_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(settings.chroma_dir),
                                     settings=chromadb.config.Settings(anonymized_telemetry=False))


def vector_store(arm: str):
    from langchain_chroma import Chroma

    from .models import embeddings

    return Chroma(collection_name=collection_name(arm), embedding_function=embeddings(), client=_client(),
                  collection_metadata={"hnsw:space": "cosine"})


def to_metadata(c: Chunk) -> dict:
    m = {k: str(getattr(c, k, "") or "") for k in META_FIELDS}
    m["assets"] = ";".join(c.assets)
    m["has_facts"] = bool(c.has_facts)
    m["chunk"] = int(c.chunk)
    return m


def from_document(doc) -> Chunk:
    m = doc.metadata or {}
    return Chunk(id=str(doc.id), doc_id=str(m.get("doc_id", "")), chunk=int(m.get("chunk", 0) or 0),
                 kind=str(m.get("kind", "")), text=doc.page_content, title=str(m.get("title", "")),
                 date=str(m.get("date", "")), section=str(m.get("section", "")), caption=str(m.get("caption", "")),
                 figure=str(m.get("figure", "")), source_file=str(m.get("source_file", "")),
                 assets=[a for a in str(m.get("assets", "")).split(";") if a], has_facts=bool(m.get("has_facts")))


def chunks_for(arm: str, doc_id: str, store: Store) -> list[Chunk]:
    """What goes into the collection: Markdown+ chunks from the store, or the stock conversion for the Word arm."""
    if ARMS[arm].has_tools:
        key = f"{CHUNKS}/{doc_id}.jsonl"
        return [Chunk.from_dict(d) for d in store.jsonl(key)] if store.exists(key) else []
    from .word_source import word_chunks

    return word_chunks(doc_id, store)


def build(arm: str, store: Store, doc_ids: list[str] | None = None, reset: bool = False) -> int:
    """(Re)load the arm's collection from the store. Returns the number of chunks in the collection afterwards."""
    from langchain_core.documents import Document

    if reset:
        clear(arm)
    vs = vector_store(arm)
    for d in doc_ids or store.doc_ids():
        chunks = chunks_for(arm, d, store)
        old = vs.get(where={"doc_id": d}, include=[])["ids"]
        if old:
            vs.delete(ids=old)
        for i in range(0, len(chunks), BATCH):
            part = chunks[i:i + BATCH]
            vs.add_documents([Document(page_content=c.text, metadata=to_metadata(c)) for c in part],
                             ids=[c.id for c in part])
        print(f"[{arm}] {d}: {len(chunks)} chunks -> {collection_name(arm)}")
    Retriever.reset(arm)
    return stats(arm)["chunks"]


def clear(arm: str) -> None:
    try:
        _client().delete_collection(collection_name(arm))
    except Exception:  # noqa: BLE001 - not there
        pass
    Retriever.reset(arm)


def stats(arm: str) -> dict:
    """Chunk count and chunks per source file in the collection; zeros when it does not exist."""
    try:
        got = _client().get_collection(collection_name(arm)).get(include=["metadatas"])
    except Exception:  # noqa: BLE001 - missing collection or no chromadb
        return {"collection": collection_name(arm), "chunks": 0, "files": {}}
    files: dict[str, int] = {}
    for m in got["metadatas"] or []:
        f = str((m or {}).get("source_file", ""))
        files[f] = files.get(f, 0) + 1
    return {"collection": collection_name(arm), "chunks": len(got["ids"]), "files": files}


def rrf(rankings: list[list[str]], k: int = RRF_K) -> list[str]:
    """Reciprocal rank fusion of id lists, best first."""
    score: dict[str, float] = {}
    for ranked in rankings:
        for pos, i in enumerate(ranked):
            score[i] = score.get(i, 0.0) + 1.0 / (k + pos)
    return sorted(score, key=lambda i: -score[i])


class Retriever:
    """All chunks of one arm in memory (for BM25) plus the Chroma collection (for vectors)."""

    _cache: dict[str, "Retriever"] = {}

    def __init__(self, arm: str):
        from rank_bm25 import BM25Okapi

        self.arm = arm
        self.vs = vector_store(arm)
        got = self.vs.get(include=["documents", "metadatas"])
        from langchain_core.documents import Document

        self.docs = [from_document(Document(page_content=t, metadata=m or {}, id=i))
                     for i, t, m in zip(got["ids"], got["documents"], got["metadatas"])]
        if not self.docs:
            raise RuntimeError(f"collection {collection_name(arm)} is empty; run: python -m research_qa index "
                               f"--arm {arm}")
        self.by_id = {c.id: c for c in self.docs}
        self.bm25 = BM25Okapi([toks(c.text) for c in self.docs])

    @classmethod
    def get(cls, arm: str) -> "Retriever":
        if arm not in cls._cache:
            cls._cache[arm] = cls(arm)
        return cls._cache[arm]

    @classmethod
    def reset(cls, arm: str | None = None) -> None:
        if arm is None:
            cls._cache.clear()
        else:
            cls._cache.pop(arm, None)

    def retrieve(self, query: str, k: int | None = None, doc_id: str | None = None) -> list[Chunk]:
        k = k or settings.max_docs
        flt = {"doc_id": doc_id} if doc_id else None
        dense = [str(d.id) for d in self.vs.similarity_search(query, k=FETCH, filter=flt)]
        scores = self.bm25.get_scores(toks(query))
        order = sorted(range(len(self.docs)), key=lambda i: -scores[i])
        sparse = [self.docs[i].id for i in order if not doc_id or self.docs[i].doc_id == doc_id][:FETCH]
        return [self.by_id[i] for i in rrf([dense, sparse]) if i in self.by_id][:k]


@lru_cache(maxsize=None)
def retriever(arm: str) -> Retriever:
    return Retriever.get(arm)
