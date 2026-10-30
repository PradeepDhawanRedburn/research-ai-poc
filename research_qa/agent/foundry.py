"""Foundry side: project connection, one agent per arm, and `Chat` which runs the agent loop and records every
step, passage, tool call and token so the app and the evaluation see exactly what happened.

Both arms get the same model, the same reasoning effort and the same instructions. The Markdown+ agent has two
extra function tools (tools.py) because its data exists; the Word agent has only the knowledge base.
The Responses-parsing helpers (`annotations`, `add_passages`, `record_round`, `finish`) take duck-typed objects, so
they are tested offline with fakes and used unchanged with the real SDK objects.
"""
from __future__ import annotations

import json
import re
import time

from ..config import ARMS, settings
from ..schema import Answer, Passage, Step
from ..storage import Store, open_store
from . import tools

DOC_URL_RE = re.compile(r"/indexes/([^/]+)/docs/([^?/]+)")
MAX_ROUNDS = 6
KB_LABEL = "knowledge-base"

INSTRUCTIONS_COMMON = """You answer questions about financial research reports. You must never answer from your own knowledge under any circumstances.
1. Always call the knowledge base (knowledge_base_retrieve) first. It holds the reports as passages: text, tables, chart data and figure descriptions. Quote numbers exactly as written, with units and dates. If the question names a report (by title, month, company or id such as RA02752), answer from that report. If the question compares two editions or reports, retrieve both and say which says what.
"""
INSTRUCTIONS_TOOLS = """2. Exact values: for any number from a chart (a value in a year or month, a peak, a latest value) or any state from a tick/cross matrix (available / not available, green tick / red cross), call lookup_facts before anything else; it holds every series point and matrix cell read at ingestion. Say when a value is approximate (read from a drawing).
3. Pictures: some passages are figures with a picture. If the question depends on what a picture shows (counting objects, colours, labels, layout, text on packaging or a screen) and neither the passage text nor lookup_facts gives it exactly, call inspect_figure with that passage's `id` (the id inside the retrieved content, like RA02752-00145). Use only ids from the retrieved results. Ask the picture question in the user's own words, naming the figure; do not narrow it or add qualifiers of your own. If the thing counted can appear in several forms (whole, slices, cans, logos), ask for a count per form and per item. You may inspect up to 3 figures. Prefer its answer over the written description, say it comes from looking at the picture, and give the breakdown.
4. Every answer must cite the passages it rests on with annotations for the knowledge base tool, rendered as `【message_idx:search_idx†source_name】`; do this also when the answer rests on lookup_facts or inspect_figure (cite the figure's passage).
5. If the knowledge base and the tools do not contain the answer, respond "I don't know".
"""
INSTRUCTIONS_PLAIN = """2. Every answer must cite the passages it rests on with annotations for the knowledge base tool, rendered as `【message_idx:search_idx†source_name】`.
3. If the knowledge base does not contain the answer, respond "I don't know".
"""
ICONS = {"plan": "🧭", "retrieve": "🔎", "facts": "📊", "look": "👁️", "reason": "🤔", "answer": "✍️"}


def instructions(arm: str) -> str:
    return INSTRUCTIONS_COMMON + (INSTRUCTIONS_TOOLS if ARMS[arm].has_tools else INSTRUCTIONS_PLAIN)


# ----------------------------------------------------------------------------- project, connection, agent

def project_client():
    from azure.ai.projects import AIProjectClient

    from ..search.azure import credential

    settings.require("project_endpoint")
    return AIProjectClient(endpoint=settings.project_endpoint, credential=credential(), allow_preview=True)


def _connection_url(name: str) -> str:
    settings.require("project_resource_id")
    return (f"https://management.azure.com{settings.project_resource_id}/connections/{name}"
            "?api-version=2025-10-01-preview")


def _arm_headers() -> dict:
    from ..search.azure import bearer

    return {"Authorization": f"Bearer {bearer('https://management.azure.com/.default')}"}


def create_connection(arm: str) -> None:
    """RemoteTool connection to the arm's knowledge base MCP endpoint, authenticated as the project identity."""
    import requests

    n = settings.names(arm)
    resp = requests.put(
        _connection_url(n.connection), headers=_arm_headers(), timeout=60,
        json={"name": n.connection,
              "type": "Microsoft.MachineLearningServices/workspaces/connections",
              "properties": {"authType": "ProjectManagedIdentity", "category": "RemoteTool",
                             "target": n.mcp_endpoint, "isSharedToAll": True,
                             "audience": "https://search.azure.com/", "metadata": {"ApiType": "Azure"}}},
    )
    if not resp.ok:
        raise RuntimeError(f"creating connection failed: {resp.status_code} {resp.text}")
    print(f"[{arm}] connection '{n.connection}' ready")


def create_agent(arm: str):
    from azure.ai.projects.models import FunctionTool, MCPTool, PromptAgentDefinition, Reasoning

    n = settings.names(arm)
    agent_tools = [MCPTool(server_label=KB_LABEL, server_url=n.mcp_endpoint, require_approval="never",
                           allowed_tools=["knowledge_base_retrieve"], project_connection_id=n.connection)]
    if ARMS[arm].has_tools:
        agent_tools += [
            FunctionTool(name=tools.FACTS_TOOL, description=tools.FACTS_DESCRIPTION,
                         parameters=tools.FACTS_PARAMETERS, strict=True),
            FunctionTool(name=tools.FIGURE_TOOL, description=tools.FIGURE_DESCRIPTION,
                         parameters=tools.FIGURE_PARAMETERS, strict=True),
        ]
    agent = project_client().agents.create_version(
        agent_name=n.agent,
        definition=PromptAgentDefinition(model=settings.answer_model, instructions=instructions(arm),
                                         tools=agent_tools, reasoning=Reasoning(effort=settings.reasoning_effort)),
    )
    print(f"[{arm}] agent '{agent.name}' version {agent.version} ready (model {settings.answer_model}, "
          f"reasoning {settings.reasoning_effort}, tools {len(agent_tools)})")
    return agent


def delete_agent(arm: str) -> None:
    import requests

    n, client = settings.names(arm), project_client()
    for v in list(client.agents.list_versions(agent_name=n.agent)):
        client.agents.delete_version(n.agent, v.version)
        print(f"[{arm}] deleted agent '{n.agent}' version {v.version}")
    resp = requests.delete(_connection_url(n.connection), headers=_arm_headers(), timeout=60)
    print(f"[{arm}] deleted connection '{n.connection}' ({resp.status_code})")


# ----------------------------------------------------------------------------- parsing the Responses output

def annotations(response) -> list[tuple[int, int, str]]:
    """(start, end, chunk id) of each citation link in the answer text. The span covers the 【..】 marker."""
    out = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", "") != "message":
            continue
        for c in getattr(item, "content", []) or []:
            for a in getattr(c, "annotations", None) or []:
                url = getattr(a, "url", "") or ""
                m = DOC_URL_RE.search(url) if getattr(a, "type", "") == "url_citation" else None
                if m:
                    out.append((a.start_index, a.end_index, m.group(2)))
    return out


def add_passages(ans: Answer, item) -> list[Passage]:
    """Passages from one knowledge_base_retrieve call. Markdown+ passages carry our chunk fields; Word-arm passages
    carry whatever Azure's pipeline stored, read defensively."""
    try:
        docs = json.loads(getattr(item, "output", None) or "{}").get("documents", [])
    except (TypeError, ValueError):
        docs = []
    known, new = {p.id for p in ans.passages}, []
    for d in docs:
        try:
            c = json.loads(d["content"]) if isinstance(d.get("content"), str) else dict(d.get("content") or {})
        except (KeyError, TypeError, ValueError):
            c = {"page_chunk": str(d.get("content", ""))}
        url = DOC_URL_RE.search(d.get("url") or "")
        pid = str(c.get("id") or c.get("chunk_id") or (url.group(2) if url else "") or f"p{len(known) + 1}")
        if pid in known:
            continue
        known.add(pid)
        text = str(c.get("page_chunk") or c.get("content_text") or c.get("chunk") or c.get("content") or "")
        source_file = str(c.get("source_file") or c.get("metadata_storage_name") or c.get("title") or "")
        doc_id = str(c.get("doc_id") or (source_file.rsplit(".", 1)[0] if source_file else ""))
        new.append(Passage(
            rank=len(ans.passages) + len(new) + 1, id=pid, doc_id=doc_id, source_file=source_file,
            kind=str(c.get("kind") or ("chunk" if ans.arm == "word" else "")),
            heading=str(c.get("caption") or c.get("figure") or c.get("section") or ""), text=text,
            assets=[a for a in str(c.get("assets") or "").split(";") if a],
            title=str(c.get("title") or ""), date=str(c.get("date") or ""),
        ))
    ans.passages.extend(new)
    return new


def record_round(ans: Answer, r, rnd: int, seconds: float) -> bool:
    """Steps, queries, passages and tokens of one agent round. Returns True when the round called the knowledge base."""
    has_kb = False
    for item in getattr(r, "output", []) or []:
        kind = getattr(item, "type", "")
        if kind == "mcp_call":
            has_kb = True
            try:
                queries = json.loads(item.arguments).get("query_variants", [])
            except (TypeError, ValueError, AttributeError):
                queries = [str(getattr(item, "arguments", ""))]
            ans.queries.extend(queries)
            ans.steps.append(Step("plan", f"Agent rewrote the question into {len(queries)} search queries",
                                  items=list(queries)))
            if getattr(item, "error", None):
                ans.errors.append(str(item.error))
            new = add_passages(ans, item)
            kinds: dict[str, int] = {}
            for p in new:
                kinds[p.kind or "chunk"] = kinds.get(p.kind or "chunk", 0) + 1
            ans.steps.append(Step(
                "retrieve", f"Knowledge base {ans.store}: {len(new)} new passages",
                "Hybrid keyword + vector search with semantic reranking, merged across the queries. "
                + ", ".join(f"{v} {k}" for k, v in sorted(kinds.items()))
                + (" from " + ", ".join(sorted({p.source_file for p in new if p.source_file})) if new else "") + "."))
        elif kind == "function_call":
            try:
                a = json.loads(getattr(item, "arguments", None) or "{}")
            except (TypeError, ValueError):
                a = {}
            if getattr(item, "name", "") == tools.FIGURE_TOOL:
                ans.steps.append(Step("reason", f"Agent decided the picture matters: {a.get('chunk_id')}",
                                      f"Will ask the vision model: {a.get('question')}"))
            else:
                ans.steps.append(Step("reason", "Agent asked for exact values",
                                      ", ".join(f"{k}={v}" for k, v in a.items() if v)))
    u = getattr(r, "usage", None)
    if u is not None:
        ans.input_tokens += int(getattr(u, "input_tokens", 0) or 0)
        ans.output_tokens += int(getattr(u, "output_tokens", 0) or 0)
        ans.reasoning_tokens += int(getattr(getattr(u, "output_tokens_details", None), "reasoning_tokens", 0) or 0)
    calls = [i for i in (getattr(r, "output", []) or []) if getattr(i, "type", "") == "function_call"]
    label = "agent turn" if calls else "final agent turn (reasoning + writing the answer)"
    ans.steps.append(Step("timing", f"Round {rnd}: {label}" + (", includes the knowledge base call" if has_kb else ""),
                          f"{getattr(u, 'input_tokens', '?')} tokens in, {getattr(u, 'output_tokens', '?')} out",
                          seconds=seconds))
    ans.rounds = rnd
    if has_kb:
        ans.retrieval_s += seconds
    return has_kb


def finish(ans: Answer, final) -> None:
    """Replace the agent's citation markers with passage numbers, mark cited passages, add the answer step."""
    raw = (getattr(final, "output_text", "") if final is not None else "") or ""
    notes = annotations(final) if final is not None else []
    cited_ids = list(dict.fromkeys(cid for _, _, cid in notes))
    rank: dict[str, int] = {}
    for p in ans.passages:
        rank.setdefault(p.id, p.rank)
        p.cited = p.id in cited_ids
    text = raw
    for start, end, cid in sorted(notes, reverse=True):
        if 0 <= start < end <= len(text):
            text = text[:start] + f"[{rank.get(cid, cid)}]" + text[end:]
    ans.raw_text, ans.text = raw, text
    ans.steps.append(Step("answer", f"Agent wrote the answer and cited {len(cited_ids)} passages",
                          "I don't know" if raw.strip().lower().startswith("i don't know") else ""))
    ans.steps = [s for s in ans.steps if s.kind != "timing"] + [s for s in ans.steps if s.kind == "timing"]
    ans.generation_s = max(0.0, ans.total_s - ans.retrieval_s - ans.tools_s)


# ----------------------------------------------------------------------------- tool execution

def run_tool(ans: Answer, store: Store, name: str, args: dict, use_tools: bool) -> str:
    """Execute one function call on our side and record it on the answer. Returns the text handed back."""
    t0 = time.perf_counter()
    if not use_tools:
        ans.steps.append(Step("look" if name == tools.FIGURE_TOOL else "facts",
                              f"{name} is switched off for this question", json.dumps(args)))
        return f"{name} is disabled for this question; answer from the text of the passages."
    if name == tools.FACTS_TOOL:
        res = tools.lookup_facts(store, args.get("doc_id"), args.get("figure"), args.get("series"), args.get("x"),
                                 args.get("query"))
        secs = time.perf_counter() - t0
        ans.tools_s += secs
        shown = ", ".join(f"{k}={v}" for k, v in args.items() if v) or "no filter"
        ans.steps.append(Step("facts", f"lookup_facts({shown}): {res['count']} rows",
                              "\n".join(f"{r['figure']} · {r['series']} · {r['x']}: {r['value']} {r['unit']}".strip()
                                        for r in res["rows"][:8]) + (f"\n{res['note']}" if res["note"] else ""),
                              seconds=secs))
        ans.tool_calls.append({"tool": name, "args": args, "result": f"{res['count']} rows", "seconds": secs})
        return json.dumps(res, ensure_ascii=False)
    if name == tools.FIGURE_TOOL:
        chunk_id = str(args.get("chunk_id", "")).strip()
        passage = next((p for p in ans.passages if p.id == chunk_id), None)
        if passage is None:
            from ..search.index import get_chunk

            c = get_chunk(chunk_id)
            assets, text = c.get("assets", []), c.get("text", "")
        else:
            assets, text = passage.assets, passage.text
        look = tools.inspect_figure(store, chunk_id, assets, text, str(args.get("question", "")))
        ans.tools_s += look.seconds
        ans.vision_tokens += look.input_tokens + look.output_tokens
        if passage is not None and look.assets:
            passage.inspected = True
        ans.steps.append(Step(
            "look",
            f"Vision model ({look.model}) looked at the picture of {chunk_id}" if look.model else
            f"Picture of {chunk_id} not available",
            f"Question: {look.question}\nSees: {look.answer}", assets=list(look.assets), seconds=look.seconds))
        ans.tool_calls.append({"tool": name, "args": args, "result": look.answer[:300], "seconds": look.seconds,
                               "tokens": look.input_tokens + look.output_tokens})
        return look.answer
    return f"Unknown tool {name}"


# ----------------------------------------------------------------------------- chat

class Chat:
    """One conversation with one arm's agent; follow-up questions keep their context."""

    def __init__(self, arm: str, store: Store | None = None) -> None:
        if arm not in ARMS:
            raise ValueError(f"unknown arm {arm!r}")
        self.arm = arm
        self.names = settings.names(arm)
        self.store = store or open_store()
        self.client = project_client().get_openai_client(agent_name=self.names.agent)
        self.conversation = self.client.conversations.create()

    def ask(self, question: str, use_tools: bool = True) -> Answer:
        ans = Answer(arm=self.arm, mode="agent", question=question, text="", model=settings.answer_model,
                     store=self.names.knowledge_base)
        start = time.perf_counter()
        payload, final = question, None
        forced = {"type": "mcp", "server_label": KB_LABEL}  # first turn must search before anything else
        for rnd in range(1, MAX_ROUNDS + 1):
            t0 = time.perf_counter()
            kwargs = {"tool_choice": forced} if forced else {}
            r = self.client.responses.create(conversation=self.conversation.id, input=payload, **kwargs)
            record_round(ans, r, rnd, time.perf_counter() - t0)
            final = r
            calls = [i for i in r.output if getattr(i, "type", "") == "function_call"]
            if not calls:
                break
            outputs = []
            for call in calls:
                try:
                    args = json.loads(call.arguments)
                except (TypeError, ValueError):
                    args = {}
                outputs.append({"type": "function_call_output", "call_id": call.call_id,
                                "output": run_tool(ans, self.store, call.name, args, use_tools)})
            payload, forced = outputs, None
        ans.total_s = time.perf_counter() - start
        finish(ans, final)
        return ans
