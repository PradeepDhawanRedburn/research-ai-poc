"""A pluggable reranker over the fused candidates (settings.reranker).

`none` (default) keeps the fused order. `cohere` calls a Cohere Rerank model deployed as a Foundry serverless
endpoint, with the request shape of Cohere's v2 rerank API (`model`, `query`, `documents`, `top_n`; results carry
`index` and `relevance_score`). The endpoint, key and model id come from RERANK_ENDPOINT, RERANK_API_KEY and
RERANK_MODEL. This client was written against the published API shape and has not been exercised against a live
deployment here; the retriever degrades to the fused order and records the error if a call fails.
"""
from __future__ import annotations

from typing import Protocol

from ..config import settings


class Reranker(Protocol):
    def rerank(self, query: str, docs: list[tuple[str, str]], top_n: int) -> list[str]:
        """Ids of `docs` (id, text) best first, at most top_n."""


class CohereReranker:
    def __init__(self, endpoint: str, api_key: str, model: str, timeout: float = 30.0):
        if not endpoint or not api_key:
            raise RuntimeError("RERANKER=cohere needs RERANK_ENDPOINT and RERANK_API_KEY (and RERANK_MODEL)")
        self.url = endpoint.rstrip("/") + ("" if endpoint.rstrip("/").endswith("/v2/rerank") else "/v2/rerank")
        self.api_key, self.model, self.timeout = api_key, model, timeout

    def rerank(self, query: str, docs: list[tuple[str, str]], top_n: int) -> list[str]:
        import requests

        body = {"query": query, "documents": [t for _, t in docs], "top_n": min(top_n, len(docs))}
        if self.model:
            body["model"] = self.model
        resp = requests.post(self.url, json=body, timeout=self.timeout,
                             headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        if not resp.ok:
            raise RuntimeError(f"rerank {resp.status_code}: {resp.text[:300]}")
        results = sorted(resp.json().get("results", []), key=lambda r: -float(r.get("relevance_score", 0)))
        return [docs[int(r["index"])][0] for r in results if 0 <= int(r.get("index", -1)) < len(docs)]


def get_reranker() -> Reranker | None:
    if settings.reranker == "none":
        return None
    if settings.reranker == "cohere":
        return CohereReranker(settings.rerank_endpoint, settings.rerank_api_key, settings.rerank_model)
    raise RuntimeError(f"unknown RERANKER {settings.reranker!r}; choose none or cohere")
