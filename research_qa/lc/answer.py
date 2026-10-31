"""Answering with the LangChain engine, both modes, returning the same schema.Answer as the Azure engine.

- direct: one hybrid retrieval, one chat completion over the numbered passages (no tools, no pictures).
- agent: a tool-calling loop (at most MAX_ROUNDS) with `search_reports` for both arms and, for Markdown+,
  `lookup_facts` and `inspect_figure` (the same functions the Foundry agent calls). The model cites passages as [n].
Everything the question cost is recorded: passages, steps, tool calls, seconds by phase, tokens by kind.
"""
from __future__ import annotations

import json
import re
import time

from ..agent import tools
from ..agent.direct import ANSWER_SYSTEM
from ..agent.foundry import instructions as foundry_instructions
from ..config import ARMS, settings
from ..schema import Answer, Passage, Step
from ..storage import Store, open_store
from .index import collection_name

MAX_ROUNDS = 5
SEARCH_TOOL = "search_reports"
SEARCH_DESCRIPTION = ("Search the research reports. Returns numbered passages: text, tables, chart data and figure "
                      "descriptions. Pass the question or a focused rewrite of it as `query`; pass `doc_id` (for "
                      "example RA01220) to search one report only, else null.")
CITE_RE = re.compile(r"\[(\d+)\]")


def instructions(arm: str) -> str:
    """The Foundry agent's instructions, with the knowledge base and the annotation markers replaced."""
    text = foundry_instructions(arm)
    text = text.replace("Always call the knowledge base (knowledge_base_retrieve) first.",
                        f"Always call {SEARCH_TOOL} first; call it again with a different query or doc_id when the "
                        "first results do not hold the answer.")
    text = text.replace("knowledge base", "search tool")
    text = re.sub(r"with annotations for the search tool tool, rendered as `【message_idx:search_idx†source_name】`",
                  "as [n], the passage number", text)
    text = re.sub(r"with annotations for the search tool, rendered as `【message_idx:search_idx†source_name】`",
                  "as [n], the passage number", text)
    text = text.replace("that passage's `id` (the id inside the retrieved content, like RA02752-00145)",
                        "that passage's number or id (like RA02752-00145)")
    return text


def to_passage(c, rank: int) -> Passage:
    return Passage(rank=rank, id=c.id, doc_id=c.doc_id, source_file=c.source_file, kind=c.kind,
                   heading=c.caption or c.section, text=c.text, assets=list(c.assets), title=c.title, date=c.date)


def numbered(passages: list[Passage]) -> str:
    """Passages as the model sees them. The id is shown because inspect_figure takes it (the Azure knowledge base
    returns the id inside the content; here it must be written out, or the agent never inspects a picture)."""
    return "\n\n".join(f"[{p.rank}] id={p.id} ({p.doc_id or p.source_file}{', ' + p.kind if p.kind else ''}"
                       f"{', ' + p.heading if p.heading else ''}{', has picture' if p.assets else ''})\n{p.text}"
                       for p in passages) or "(no passages)"


def mark_cited(ans: Answer) -> None:
    nums = {int(x) for x in CITE_RE.findall(ans.text)}
    for p in ans.passages:
        p.cited = p.rank in nums


def _retrieve(arm: str, query: str, k: int | None, doc_id: str | None = None):
    from .index import retriever

    return retriever(arm).retrieve(query, k=k, doc_id=doc_id or None)


# ----------------------------------------------------------------------------- direct

def ask_direct(question: str, arm: str, max_docs: int | None = None) -> Answer:
    from langchain_core.messages import HumanMessage, SystemMessage

    from .models import chat_model, usage_of

    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}")
    ans = Answer(arm=arm, mode="direct", question=question, text="", model=settings.answer_model,
                 store=collection_name(arm))
    t0 = time.perf_counter()
    try:
        ans.passages = [to_passage(c, i) for i, c in enumerate(_retrieve(arm, question, max_docs), 1)]
    except Exception as e:  # noqa: BLE001 - the answer records the failure
        ans.errors.append(f"retrieve failed: {type(e).__name__}: {str(e)[:300]}")
    ans.queries = [question]
    ans.retrieval_s = time.perf_counter() - t0
    ans.steps.append(Step("retrieve", f"Collection {ans.store}: {len(ans.passages)} passages",
                          "One hybrid retrieval: vector search and BM25 over the same chunks, fused by reciprocal rank.",
                          items=[question], seconds=ans.retrieval_s))
    t1 = time.perf_counter()
    msg = chat_model().invoke([SystemMessage(ANSWER_SYSTEM),
                               HumanMessage(f"Passages:\n{numbered(ans.passages)}\n\nQuestion: {question}")])
    ans.generation_s = time.perf_counter() - t1
    ans.input_tokens, ans.output_tokens, ans.reasoning_tokens = usage_of(msg)
    ans.text = ans.raw_text = str(msg.content if isinstance(msg.content, str) else
                                  " ".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in msg.content)).strip()
    ans.total_s = time.perf_counter() - t0
    mark_cited(ans)
    ans.steps.append(Step("answer", f"{settings.answer_model} wrote the answer and cited {len(ans.cited)} passages",
                          seconds=ans.generation_s))
    return ans


# ----------------------------------------------------------------------------- agent

def _tool_specs(arm: str) -> list[dict]:
    """OpenAI-style tool definitions; bound with bind_tools, which accepts this form."""
    specs = [{"type": "function", "function": {
        "name": SEARCH_TOOL, "description": SEARCH_DESCRIPTION,
        "parameters": {"type": "object",
                       "properties": {"query": {"type": "string", "description": "search query"},
                                      "doc_id": {"type": ["string", "null"], "description": "report id or null"}},
                       "required": ["query", "doc_id"], "additionalProperties": False}}}]
    if ARMS[arm].has_tools:
        specs.append({"type": "function", "function": {"name": tools.FACTS_TOOL, "description": tools.FACTS_DESCRIPTION,
                                                       "parameters": tools.FACTS_PARAMETERS}})
        specs.append({"type": "function", "function": {"name": tools.FIGURE_TOOL,
                                                       "description": tools.FIGURE_DESCRIPTION,
                                                       "parameters": tools.FIGURE_PARAMETERS}})
    return specs


class Chat:
    """One conversation with one arm; the message history stays on the instance for follow-up questions.
    `model` and `retrieve` can be swapped for tests: model(messages, tool_specs, force_tool) -> AIMessage-like."""

    def __init__(self, arm: str, store: Store | None = None, model=None, retrieve=None):
        if arm not in ARMS:
            raise ValueError(f"unknown arm {arm!r}")
        self.arm = arm
        self.store = store
        self.history: list = []
        self._model = model
        self._retrieve = retrieve or (lambda q, k, d: _retrieve(arm, q, k, d))

    # --- model call, isolated so tests can fake it
    def _call(self, messages: list, specs: list[dict], force: str | None):
        if self._model is not None:
            return self._model(messages, specs, force)
        from .models import chat_model

        bound = chat_model().bind_tools(specs, tool_choice=({"type": "function", "function": {"name": force}}
                                                            if force else None))
        return bound.invoke(messages)

    def ask(self, question: str, use_tools: bool = True, max_docs: int | None = None) -> Answer:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

        store = self.store or (open_store() if ARMS[self.arm].has_tools else None)
        ans = Answer(arm=self.arm, mode="agent", question=question, text="", model=settings.answer_model,
                     store=collection_name(self.arm))
        specs = _tool_specs(self.arm)
        if not use_tools:
            specs = specs[:1]
        messages = [SystemMessage(instructions(self.arm))] + self.history + [HumanMessage(question)]
        start = time.perf_counter()
        final = None
        for rnd in range(1, MAX_ROUNDS + 1):
            t0 = time.perf_counter()
            msg = self._call(messages, specs, SEARCH_TOOL if rnd == 1 else None)
            secs = time.perf_counter() - t0
            from .models import usage_of

            i, o, r = usage_of(msg)
            ans.input_tokens += i
            ans.output_tokens += o
            ans.reasoning_tokens += r
            calls = list(getattr(msg, "tool_calls", None) or [])
            ans.steps.append(Step("timing", f"Round {rnd}: " + ("agent turn" if calls else "final agent turn"),
                                  f"{i} tokens in, {o} out", seconds=secs))
            messages.append(msg if isinstance(msg, AIMessage) else AIMessage(content=str(getattr(msg, "content", "")),
                                                                            tool_calls=calls))
            final = msg
            if not calls:
                break
            for call in calls:
                name, args, call_id = call.get("name"), dict(call.get("args") or {}), call.get("id", "")
                result = self._run_tool(ans, store, name, args, use_tools, max_docs)
                messages.append(ToolMessage(content=result, tool_call_id=call_id, name=name))
        ans.total_s = time.perf_counter() - start
        ans.rounds = sum(1 for s in ans.steps if s.kind == "timing")
        content = getattr(final, "content", "") if final is not None else ""
        if not isinstance(content, str):
            content = " ".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
        ans.text = ans.raw_text = content.strip()
        mark_cited(ans)
        ans.steps.append(Step("answer", f"Agent wrote the answer and cited {len(ans.cited)} passages",
                              "I don't know" if ans.text.lower().startswith("i don't know") else ""))
        ans.steps = [s for s in ans.steps if s.kind != "timing"] + [s for s in ans.steps if s.kind == "timing"]
        ans.generation_s = max(0.0, ans.total_s - ans.retrieval_s - ans.tools_s)
        self.history += [HumanMessage(question), AIMessage(content=ans.text)]
        return ans

    def _run_tool(self, ans: Answer, store: Store | None, name: str, args: dict, use_tools: bool,
                  max_docs: int | None) -> str:
        t0 = time.perf_counter()
        if name == SEARCH_TOOL:
            query, doc_id = str(args.get("query") or ans.question), args.get("doc_id") or None
            try:
                found = self._retrieve(query, max_docs, doc_id)
            except Exception as e:  # noqa: BLE001
                ans.errors.append(f"search failed: {type(e).__name__}: {str(e)[:300]}")
                found = []
            known = {p.id for p in ans.passages}
            new = [to_passage(c, len(ans.passages) + n + 1) for n, c in enumerate(c for c in found if c.id not in known)]
            ans.passages.extend(new)
            secs = time.perf_counter() - t0
            ans.retrieval_s += secs
            ans.queries.append(query + (f" [{doc_id}]" if doc_id else ""))
            kinds: dict[str, int] = {}
            for p in new:
                kinds[p.kind] = kinds.get(p.kind, 0) + 1
            ans.steps.append(Step("retrieve", f"Search over {ans.store}: {len(new)} new passages",
                                  f"Query: {query}" + (f" in {doc_id}" if doc_id else "") + ". "
                                  + ", ".join(f"{v} {k}" for k, v in sorted(kinds.items())) + " from "
                                  + ", ".join(sorted({p.source_file for p in new})) + ".", items=[query], seconds=secs))
            ans.tool_calls.append({"tool": name, "args": args, "result": f"{len(new)} passages", "seconds": secs})
            shown = [p for p in ans.passages if p.id in {q.id for q in found}]
            return numbered(shown) if shown else "No passages found; try another query or doc_id."
        if not use_tools or store is None or not ARMS[self.arm].has_tools:
            ans.steps.append(Step("look" if name == tools.FIGURE_TOOL else "facts",
                                  f"{name} is not available for this question", json.dumps(args)))
            return f"{name} is not available; answer from the text of the passages."
        if name == tools.FACTS_TOOL:
            res = tools.lookup_facts(store, args.get("doc_id"), args.get("figure"), args.get("series"),
                                     args.get("x"), args.get("query"))
            secs = time.perf_counter() - t0
            ans.tools_s += secs
            shown = ", ".join(f"{k}={v}" for k, v in args.items() if v) or "no filter"
            ans.steps.append(Step("facts", f"lookup_facts({shown}): {res['count']} rows",
                                  "\n".join(f"{r['figure']} · {r['series']} · {r['x']}: {r['value']} {r['unit']}".strip()
                                            for r in res["rows"][:8]) + (f"\n{res['note']}" if res.get("note") else ""),
                                  seconds=secs))
            ans.tool_calls.append({"tool": name, "args": args, "result": f"{res['count']} rows", "seconds": secs})
            return json.dumps(res, ensure_ascii=False)
        if name == tools.FIGURE_TOOL:
            ref = str(args.get("chunk_id", "")).strip()
            passage = next((p for p in ans.passages if p.id == ref), None)
            if passage is None and ref.strip("[]").isdigit():
                passage = next((p for p in ans.passages if p.rank == int(ref.strip("[]"))), None)
            assets, text, chunk_id = (passage.assets, passage.text, passage.id) if passage else ([], "", ref)
            look = tools.inspect_figure(store, chunk_id, assets, text, str(args.get("question", "")))
            ans.tools_s += look.seconds
            ans.vision_tokens += look.input_tokens + look.output_tokens
            if passage is not None and look.assets:
                passage.inspected = True
            ans.steps.append(Step("look", f"Vision model ({look.model}) looked at the picture of {chunk_id}"
                                  if look.model else f"Picture of {chunk_id} not available",
                                  f"Question: {look.question}\nSees: {look.answer}", assets=list(look.assets),
                                  seconds=look.seconds))
            ans.tool_calls.append({"tool": name, "args": args, "result": look.answer[:300], "seconds": look.seconds,
                                   "tokens": look.input_tokens + look.output_tokens})
            return look.answer
        return f"Unknown tool {name}"
