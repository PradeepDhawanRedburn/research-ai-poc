"""One Chroma collection per arm and a hybrid retriever over it (vector search + BM25, reciprocal rank fusion).

Ported from the Phase 0 `rag/` package. BM25 matters because many questions hinge on exact tokens (Fig 27, CCEP,
2030) that embeddings blur. Chroma persists under settings.chroma_dir; it is derived data, rebuilt from the store.
"""
from __future__ import annotations

import re
import threading
from functools import lru_cache

from .. import acl as access
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


_client_lock = threading.Lock()
_client_cache = None


def _client():
    """One client per process: chromadb's client construction is not thread-safe, and the app asks arms in parallel threads."""
    global _client_cache
    with _client_lock:
        if _client_cache is None:
            import chromadb

            settings.chroma_dir.mkdir(parents=True, exist_ok=True)
            _client_cache = chromadb.PersistentClient(
                path=str(settings.chroma_dir), settings=chromadb.config.Settings(anonymized_telemetry=False))
        return _client_cache


def vector_store(arm: str):
    from langchain_chroma import Chroma

    from .models import embeddings

    return Chroma(collection_name=collection_name(arm), embedding_function=embeddings(), client=_client(),
                  collection_metadata={"hnsw:space": "cosine"})


def to_metadata(c: Chunk) -> dict:
    m = {k: str(getattr(c, k, "") or "") for k in META_FIELDS}
    m["assets"] = ";".join(c.assets)
    m["acl"] = ";".join(c.acl)
    m["has_facts"] = bool(c.has_facts)
    m["chunk"] = int(c.chunk)
    return m


def from_document(doc) -> Chunk:
    m = doc.metadata or {}
    return Chunk(id=str(doc.id), doc_id=str(m.get("doc_id", "")), chunk=int(m.get("chunk", 0) or 0),
                 kind=str(m.get("kind", "")), text=doc.page_content, title=str(m.get("title", "")),
                 date=str(m.get("date", "")), section=str(m.get("section", "")), caption=str(m.get("caption", "")),
                 figure=str(m.get("figure", "")), source_file=str(m.get("source_file", "")),
                 assets=[a for a in str(m.get("assets", "")).split(";") if a], has_facts=bool(m.get("has_facts")),
                 acl=[a for a in str(m.get("acl", "")).split(";") if a])


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
    acl_map = access.load(store)
    for d in doc_ids or store.doc_ids():
        chunks = chunks_for(arm, d, store)
        for c in chunks:
            c.acl = access.for_doc(acl_map, d)
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


def delete_doc(arm: str, doc_id: str) -> int:
    """Remove one report's chunks from the arm's collection; returns how many. Used by reconcile."""
    try:
        vs = vector_store(arm)
        ids = vs.get(where={"doc_id": doc_id}, include=[])["ids"]
    except Exception:  # noqa: BLE001 - no collection yet: nothing to delete
        return 0
    if ids:
        vs.delete(ids=ids)
    Retriever.reset(arm)
    return len(ids)


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
    unlisted = 0
    for m in got["metadatas"] or []:
        f = str((m or {}).get("source_file", ""))
        files[f] = files.get(f, 0) + 1
        unlisted += not (m or {}).get("acl")
    return {"collection": collection_name(arm), "chunks": len(got["ids"]), "files": files,
            "chunks_without_acl": unlisted}


def rrf(rankings: list[list[str]], k: int = RRF_K) -> list[str]:
    """Reciprocal rank fusion of id lists, best first."""
    score: dict[str, float] = {}
    for ranked in rankings:
        for pos, i in enumerate(ranked):
            score[i] = score.get(i, 0.0) + 1.0 / (k + pos)
    return sorted(score, key=lambda i: -score[i])


FILTER_FIELDS = ("doc_id", "kind", "figure", "source_file", "date")   # metadata both legs can filter on
FIG_RE = re.compile(r"\b(?:fig(?:ure)?|chart|exhibit)\.?\s*(\d+[a-z]?)\b", re.I)


def where_clause(filters: dict | None) -> dict | None:
    """Chroma `where` for exact-match filters; None when there are none."""
    items = [{k: v} for k, v in (filters or {}).items() if k in FILTER_FIELDS and v not in (None, "")]
    return None if not items else (items[0] if len(items) == 1 else {"$and": items})


def matches(c: Chunk, filters: dict | None) -> bool:
    return all(str(getattr(c, k, "")) == str(v) for k, v in (filters or {}).items()
               if k in FILTER_FIELDS and v not in (None, ""))


def figure_in(query: str) -> str:
    """'27' for 'Fig 27 of the April Money Map', '' when the query names no figure."""
    m = FIG_RE.search(query)
    return m.group(1).lower() if m else ""


class Retriever:
    """All chunks of one arm in memory (for BM25) plus the Chroma collection (for vectors).

    retrieve() runs both legs in parallel, fuses them by reciprocal rank, applies the same metadata filters to both,
    puts the chunks of a figure the question names first (settings.retrieval_figure_boost), and hands the fused
    candidates to a reranker when one is configured (settings.reranker). `docs`, `vs` and `reranker` can be given
    for tests."""

    _cache: dict[str, "Retriever"] = {}

    def __init__(self, arm: str, docs: list[Chunk] | None = None, vs=None, reranker=None):
        from rank_bm25 import BM25Okapi

        self.arm = arm
        self.vs = vs if vs is not None else vector_store(arm)
        if docs is None:
            got = self.vs.get(include=["documents", "metadatas"])
            from langchain_core.documents import Document

            docs = [from_document(Document(page_content=t, metadata=m or {}, id=i))
                    for i, t, m in zip(got["ids"], got["documents"], got["metadatas"])]
        self.docs = docs
        if not self.docs:
            raise RuntimeError(f"collection {collection_name(arm)} is empty; run: python -m research_qa index "
                               f"--arm {arm}")
        self.by_id = {c.id: c for c in self.docs}
        self.bm25 = BM25Okapi([toks(c.text) for c in self.docs])
        self._reranker = reranker

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

    @property
    def reranker(self):
        if self._reranker is None and settings.reranker != "none":
            from .rerank import get_reranker

            self._reranker = get_reranker()
        return self._reranker

    # --- legs (both apply the metadata filters and the caller's access rights)
    def _visible(self, c: Chunk, flt: dict | None, principals) -> bool:
        return matches(c, flt) and access.allowed(c.acl, principals)

    def _dense(self, query: str, flt: dict | None, principals=None) -> list[str]:
        # Chroma filters the metadata; access is checked here on the over-fetched candidates (a list-valued
        # field cannot be matched exactly in a Chroma where clause)
        hits = self.vs.similarity_search(query, k=FETCH * 4, filter=where_clause(flt))
        return [str(d.id) for d in hits if str(d.id) in self.by_id
                and access.allowed(self.by_id[str(d.id)].acl, principals)][:FETCH]

    def _sparse(self, query: str, flt: dict | None, principals=None) -> list[str]:
        scores = self.bm25.get_scores(toks(query))
        order = sorted(range(len(self.docs)), key=lambda i: -scores[i])
        return [self.docs[i].id for i in order if scores[i] > 0 and self._visible(self.docs[i], flt, principals)][:FETCH]

    def fused(self, query: str, flt: dict | None, parallel: bool = True, principals=None) -> list[str]:
        if parallel:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=2) as pool:
                dense = pool.submit(self._dense, query, flt, principals)
                sparse = pool.submit(self._sparse, query, flt, principals)
                dense, sparse = dense.result(), sparse.result()
        else:
            dense, sparse = self._dense(query, flt, principals), self._sparse(query, flt, principals)
        return [i for i in rrf([dense, sparse]) if i in self.by_id]

    def retrieve(self, query: str, k: int | None = None, doc_id: str | None = None, filters: dict | None = None,
                 info: dict | None = None, principals=None) -> list[Chunk]:
        """Top-k chunks the caller may see. `filters` are exact matches on FILTER_FIELDS (doc_id is the common
        one); `principals` is the caller's identity (default: PRINCIPALS from .env; anonymous sees public only).
        `info`, when a dict is passed, receives what shaped the result: figure_boost ids, reranked, candidates."""
        k = k or settings.max_docs
        principals = access.caller_principals(principals)
        flt = {**(filters or {}), **({"doc_id": doc_id} if doc_id else {})}
        ids = self.fused(query, flt, principals=principals)
        boosted: list[str] = []
        fig = figure_in(query) if settings.retrieval_figure_boost else ""
        if fig:
            boosted = [c.id for c in self.docs if figure_in(c.figure or "") == fig and self._visible(c, flt, principals)][:3]
            ids = boosted + [i for i in ids if i not in boosted]
        reranked = False
        rr = self.reranker
        if rr is not None and ids:
            pool_ids = ids[:settings.rerank_candidates]
            try:
                order = rr.rerank(query, [(i, self.by_id[i].text) for i in pool_ids], top_n=k)
                ids = [i for i in order if i in self.by_id] + [i for i in ids if i not in order]
                reranked = True
            except Exception as e:  # noqa: BLE001 - a reranker outage degrades to the fused order
                if info is not None:
                    info["rerank_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        if info is not None:
            info.update({"figure_boost": boosted, "reranked": reranked, "candidates": len(ids), "filters": flt,
                         "principals": list(principals)})
        return [self.by_id[i] for i in ids[:k]]


@lru_cache(maxsize=None)
def retriever(arm: str) -> Retriever:
    return Retriever.get(arm)
