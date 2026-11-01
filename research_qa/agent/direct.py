"""Single-pass mode: one knowledge-base retrieval, one chat completion. No planning, no tools, no agent.

Same knowledge base as the agentic mode of the same arm, so the two modes differ only in what sits on top of
retrieval. The retrieve call goes through the REST API (POST knowledgebases/{name}/retrieve), which is the stable
part of the preview surface; the SDK's KnowledgeBaseRetrievalClient would do the same.
"""
from __future__ import annotations

import json
import time

from ..config import ARMS, settings
from .fields import chunk_file, chunk_text
from ..schema import Answer, Passage, Step

ANSWER_SYSTEM = """You answer questions about financial research reports using only the numbered passages given.
- Use only facts in the passages. Do not use outside knowledge.
- Cite every fact with its passage number, e.g. [3]. Quote numbers exactly as written, with units and dates.
- Chart values marked approximate: say they are approximate.
- If the passages do not contain the answer, say "I don't know." and stop; do not guess.
- If the question names a report (by title, month, company or id), answer from that report.
Be concise: a direct answer first, then a short supporting explanation."""


def retrieve_body(arm: str, question: str, doc_id: str | None = None, principals=None) -> dict:
    """The `intents` request body. On the Markdown+ index (filterable fields) the filterAddOn carries the caller's
    access rights (acl.json, see research_qa/acl.py) and, when given, the doc_id. The Word arm's Blob-built index
    has neither field, so no filter is sent for it: access control there is Azure's own, not ours."""
    from .. import acl as access

    params = {
        "knowledgeSourceName": settings.names(arm).knowledge_source,
        "kind": "azureBlob" if arm == "word" else "searchIndex",
        "includeReferences": True,
        "includeReferenceSourceData": True,
    }
    if arm != "word":
        clauses = [access.odata_filter(access.caller_principals(principals))]
        if doc_id:
            clauses.append("doc_id eq '%s'" % doc_id.replace("'", "''"))
        params["filterAddOn"] = " and ".join(clauses)
    return {
        "intents": [{"type": "semantic", "search": question}],
        "retrievalReasoningEffort": {"kind": "minimal"},
        "includeActivity": True,
        "knowledgeSourceParams": [params],
    }


def retrieve(arm: str, question: str, max_docs: int | None = None, doc_id: str | None = None,
             principals=None) -> dict:
    from ..search.azure import search_rest

    # the passage limit lives on the knowledge base (retrieveDefaults), the same for both modes; `max_docs` is
    # accepted for symmetry with the agent path and applied there through knowledge.set_max_docs
    path = f"knowledgebases/{settings.names(arm).knowledge_base}/retrieve"
    # Our knowledge bases run with minimal retrieval reasoning (no query planning in the service), and that mode
    # takes the query as `intents`, not as chat `messages` (the service says so with a 400). Messages are kept as the
    # fallback for a base configured with reasoning.
    # Shape from the REST reference (Knowledge Retrieval - Retrieve, 2025-11-01-preview and later): `intents` with
    # semantic queries, the effort stated on the request, and source data asked for on the references so the
    # passages carry their fields (otherwise only ids and the extractive text come back).
    intents = retrieve_body(arm, question, doc_id, principals)
    try:
        return search_rest("POST", path, intents)
    except RuntimeError as first:
        if "400" not in str(first):
            raise
        try:
            return search_rest("POST", path,
                               {"messages": [{"role": "user", "content": [{"type": "text", "text": question}]}]})
        except RuntimeError as second:
            raise RuntimeError(f"intents body rejected: {first} | messages body rejected: {second}") from second


def passages_from_retrieve(resp: dict, arm: str) -> tuple[list[Passage], list[str]]:
    """Passages from a retrieve response: the `references` (with sourceData) when present, else the extractive
    JSON in the response text. Also returns the query variants the service ran, from `activity`."""
    out: list[Passage] = []
    seen: set[str] = set()

    def add(c: dict, fallback_id: str):
        pid = str(c.get("id") or c.get("chunk_id") or fallback_id)
        if pid in seen:
            return
        seen.add(pid)
        text = chunk_text(c)
        source_file = chunk_file(c)
        out.append(Passage(rank=len(out) + 1, id=pid,
                           doc_id=str(c.get("doc_id") or (source_file.rsplit(".", 1)[0] if source_file else "")),
                           source_file=source_file, kind=str(c.get("kind") or ("chunk" if arm == "word" else "")),
                           heading=str(c.get("caption") or c.get("figure") or c.get("section") or ""), text=text,
                           assets=[a for a in str(c.get("assets") or "").split(";") if a],
                           title=str(c.get("title") or ""), date=str(c.get("date") or "")))

    for ref in resp.get("references", []) or []:
        data = ref.get("sourceData") or {}
        add(data, str(ref.get("docKey") or ref.get("id") or ""))
    if not out:
        for msg in resp.get("response", []) or []:
            for part in msg.get("content", []) or []:
                try:
                    docs = json.loads(part.get("text") or "[]")
                except (TypeError, ValueError):
                    continue
                for d in docs if isinstance(docs, list) else []:
                    c = d if isinstance(d, dict) else {}
                    inner = c.get("content")
                    if isinstance(inner, str):
                        try:
                            inner = json.loads(inner)
                        except ValueError:
                            inner = {"page_chunk": inner}
                    add(inner if isinstance(inner, dict) else c, str(c.get("ref_id", len(out) + 1)))
    queries: list[str] = []
    for act in resp.get("activity", []) or []:
        q = act.get("query") or {}
        if isinstance(q, dict) and q.get("search"):
            queries.append(str(q["search"]))
        elif isinstance(act.get("searchQuery"), str):
            queries.append(act["searchQuery"])
    return out, queries


def stream_text(chunks, on_token=None, t_start: float | None = None) -> tuple[str, object, float]:
    """Accumulate an OpenAI chat-completion stream: (text, usage object or None, first_token_s). The usage arrives
    on the final chunk when stream_options.include_usage is set."""
    t_start = t_start if t_start is not None else time.perf_counter()
    text, first, usage = "", 0.0, None
    for ch in chunks:
        u = getattr(ch, "usage", None)
        if u is not None:
            usage = u
        choices = getattr(ch, "choices", None) or []
        delta = getattr(choices[0], "delta", None) if choices else None
        c = getattr(delta, "content", None) if delta is not None else None
        if c:
            if not text:
                first = time.perf_counter() - t_start
            text += c
            if on_token is not None:
                on_token(text)
    return text.strip(), usage, first


def ask_direct(question: str, arm: str, max_docs: int | None = None, principals=None, on_token=None) -> Answer:
    from ..search.azure import openai_client

    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}")
    n = settings.names(arm)
    ans = Answer(arm=arm, mode="single-pass", question=question, text="", model=settings.answer_model,
                 store=n.knowledge_base)
    t0 = time.perf_counter()
    try:
        resp = retrieve(arm, question, max_docs, principals=principals)
    except Exception as e:  # noqa: BLE001 - the answer records the failure
        ans.errors.append(f"retrieve failed: {type(e).__name__}: {str(e)[:300]}")
        resp = {}
    ans.passages, ans.queries = passages_from_retrieve(resp, arm)
    ans.retrieval_s = time.perf_counter() - t0
    ans.steps.append(Step("retrieve", f"Knowledge base {n.knowledge_base}: {len(ans.passages)} passages",
                          "One retrieval, hybrid search with semantic reranking.", items=list(ans.queries),
                          seconds=ans.retrieval_s))

    sources = "\n\n".join(f"[{p.rank}] ({p.doc_id or p.source_file}{', ' + p.kind if p.kind else ''})\n{p.text}"
                          for p in ans.passages) or "(no passages)"
    reasoning = settings.answer_model.startswith(("gpt-5", "o"))
    kw = {"reasoning_effort": settings.reasoning_effort} if reasoning else {"temperature": 0}
    prompt = f"Passages:\n{sources}\n\nQuestion: {question}"
    system, content = ANSWER_SYSTEM, prompt
    if settings.direct_images and ARMS[arm].has_tools and ans.passages:
        from ..storage import open_store
        from . import tools

        blocks, shown = tools.picture_blocks(open_store(), ans.passages, settings.direct_images_max)
        if shown:
            for p, _k in shown:
                p.inspected = True
            system = ANSWER_SYSTEM + "\n" + tools.PICTURES_NOTE
            content = [{"type": "text", "text": prompt}] + blocks
            ans.steps.append(Step("look", f"Original pictures of {len(shown)} figure passage(s) sent with the question",
                                  "DIRECT_IMAGES is on: the answer model sees the pictures, not only their descriptions.",
                                  items=[f"[{p.rank}] {p.heading or p.kind}" for p, _ in shown],
                                  assets=[k for _, k in shown]))
    t1 = time.perf_counter()
    messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
    if settings.stream_direct:
        stream = openai_client().chat.completions.create(
            model=settings.answer_model, max_completion_tokens=1500, messages=messages, stream=True,
            stream_options={"include_usage": True}, **kw)
        text, u, ans.first_token_s = stream_text(stream, on_token, t_start=t0)
    else:
        r = openai_client().chat.completions.create(
            model=settings.answer_model, max_completion_tokens=1500, messages=messages, **kw)
        u, text = getattr(r, "usage", None), (r.choices[0].message.content or "").strip()
    ans.generation_s = time.perf_counter() - t1
    ans.input_tokens = int(getattr(u, "prompt_tokens", 0) or 0)
    ans.output_tokens = int(getattr(u, "completion_tokens", 0) or 0)
    ans.reasoning_tokens = int(getattr(getattr(u, "completion_tokens_details", None), "reasoning_tokens", 0) or 0)
    ans.text = ans.raw_text = text
    ans.total_s = time.perf_counter() - t0
    mark_cited(ans)
    ans.steps.append(Step("answer", f"{settings.answer_model} wrote the answer and cited {len(ans.cited)} passages",
                          seconds=ans.generation_s))
    return ans


def mark_cited(ans: Answer) -> None:
    import re

    nums = {int(x) for x in re.findall(r"\[(\d+)\]", ans.text)}
    for p in ans.passages:
        p.cited = p.rank in nums
