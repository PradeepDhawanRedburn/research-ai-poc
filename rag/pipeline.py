"""Retrieval and answering.

Retrieval is hybrid: Chroma vector search (Azure embeddings) and BM25 over the same chunks, fused with
reciprocal rank fusion. BM25 matters here because many questions hinge on exact tokens (Fig 27, CCEP, 2030).

Answering is multimodal: the retrieved chunks go in as text, and the pictures behind the top figure chunks go in
as images, so the chat model can read a chart itself rather than rely only on the description made at ingestion.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from rank_bm25 import BM25Okapi

from .azure_models import chat_model
from .config import settings
from .images import data_url
from .store import all_documents, vector_store

TOKEN_RE = re.compile(r"[a-z0-9]+(?:[./%-][a-z0-9]+)*")

ANSWER_SYSTEM = """You answer questions about financial research reports using only the numbered sources given.

- Use only facts in the sources (text, tables, chart data, image descriptions, and the attached images). \
Do not use outside knowledge.
- Cite every fact with its source number, e.g. [3]. Quote numbers exactly as written, with units and dates.
- Chart values marked approximate or read from an image: say they are approximate.
- If the sources do not contain the answer, say "The documents do not say." and stop; do not guess.
- If the question names a report (by title, month or company), answer from that report.
Be concise: a direct answer first, then a short supporting explanation."""


def _tokens(s: str) -> list[str]:
    return TOKEN_RE.findall(s.lower())


@dataclass
class Answer:
    question: str
    answer: str
    sources: list[Document]
    images: list[str]
    timings: dict[str, float] = field(default_factory=dict)  # retrieval_s, generation_s, total_s
    usage: dict[str, int] = field(default_factory=dict)      # input_tokens, output_tokens, total_tokens


class Retriever:
    def __init__(self, source: str):
        self.source = source
        self.vs = vector_store(source)
        self.docs = all_documents(self.vs)
        if not self.docs:
            raise RuntimeError(f"collection {settings.collection(source)} is empty; run: python -m rag ingest "
                               f"--source {source}")
        self.bm25 = BM25Okapi([_tokens(d.page_content) for d in self.docs])
        self.by_id = {d.id: d for d in self.docs}

    def retrieve(self, query: str, k: int | None = None, doc_id: str | None = None, fetch: int = 30,
                 hybrid: bool = True) -> list[Document]:
        k = k or settings.top_k
        flt = {"doc_id": doc_id} if doc_id else None
        dense = self.vs.similarity_search(query, k=fetch, filter=flt)
        ranked: dict[str, float] = {}
        for rank, d in enumerate(dense):
            ranked[d.id] = ranked.get(d.id, 0) + 1 / (60 + rank)
        if hybrid:
            scores = self.bm25.get_scores(_tokens(query))
            order = sorted(range(len(self.docs)), key=lambda i: -scores[i])
            order = [i for i in order if not doc_id or self.docs[i].metadata.get("doc_id") == doc_id][:fetch]
            for rank, i in enumerate(order):
                did = self.docs[i].id
                ranked[did] = ranked.get(did, 0) + 1 / (60 + rank)
        dense_by_id = {d.id: d for d in dense}
        best = sorted(ranked, key=lambda i: -ranked[i])[:k]
        return [dense_by_id.get(i) or self.by_id[i] for i in best]


@lru_cache(maxsize=None)
def retriever(source: str) -> Retriever:
    return Retriever(source)


def _context(docs: list[Document]) -> tuple[list[dict], list[str]]:
    parts: list[dict] = []
    images: list[str] = []
    for n, d in enumerate(docs, 1):
        m = d.metadata
        parts.append({"type": "text", "text": f"[{n}] ({m.get('doc_id')}, {m.get('kind')})\n{d.page_content}"})
    # attach the pictures of the best-ranked figure chunks
    for n, d in enumerate(docs, 1):
        for p in filter(None, (d.metadata.get("image_path") or "").split(";")):
            if len(images) >= settings.max_answer_images:
                break
            if Path(p).exists() and p not in images:
                images.append(p)
                parts.append({"type": "text", "text": f"Image belonging to source [{n}]:"})
                parts.append({"type": "image_url", "image_url": {"url": data_url(Path(p)), "detail": "high"}})
    return parts, images


def answer(question: str, source: str, k: int | None = None, doc_id: str | None = None,
           hybrid: bool = True, use_images: bool = True) -> Answer:
    t0 = time.perf_counter()
    docs = retriever(source).retrieve(question, k=k, doc_id=doc_id, hybrid=hybrid)
    t1 = time.perf_counter()
    parts, images = _context(docs)
    if not use_images:
        parts = [p for p in parts if p["type"] == "text" and not p["text"].startswith("Image belonging")]
        images = []
    content = [{"type": "text", "text": "Sources:"}, *parts, {"type": "text", "text": f"Question: {question}"}]
    t2 = time.perf_counter()
    msg = chat_model().invoke([SystemMessage(ANSWER_SYSTEM), HumanMessage(content=content)])
    t3 = time.perf_counter()
    u = msg.usage_metadata or {}
    return Answer(question, str(msg.content).strip(), docs, images,
                  timings={"retrieval_s": t1 - t0, "generation_s": t3 - t2, "total_s": t3 - t0},
                  usage={k: int(u.get(k, 0)) for k in ("input_tokens", "output_tokens", "total_tokens")})
