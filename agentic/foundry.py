"""Microsoft Foundry side of the pipeline: project connection, agent with its tools, chat.

Each solution (docx, markdown) has its own connection and agent, which point at that solution's knowledge base,
so an answer can always be traced back to one index.

The agent has two tools:
  knowledge_base_retrieve  (MCP, runs in Azure AI Search)  plans queries, hybrid search, semantic rerank
  inspect_figure           (function, runs here)           a vision model looks at the picture behind a figure chunk
`Chat.ask` runs the agent loop and records every step so the UI can show what happened.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

import requests
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import FunctionTool, MCPTool, PromptAgentDefinition, Reasoning
from azure.identity import get_bearer_token_provider

from . import vision
from .config import Names, settings
from .search_setup import credential

INSTRUCTIONS = """
You answer questions about financial research reports. You have two tools: the knowledge base (knowledge_base_retrieve) and inspect_figure. You must never answer from your own knowledge under any circumstances.
1. Always call the knowledge base first. It holds the reports as chunks: text, tables, chart data (values read from charts may be approximate; say so) and figure descriptions. Quote numbers exactly as written, with units and dates. If the question names a report (by title, month, company or id such as RA02752), answer from that report.
2. Some chunks are figures (kind figure or chart) and have a picture. If the question depends on what a picture shows (counting objects, colours, labels, layout, values read off a chart) and the chunk text does not give the answer exactly, call inspect_figure with that chunk's `id` (the id inside the retrieved content, like RA02752-00145). Use only ids from the retrieved results. Ask the picture question in the user's own words, naming the figure; do not narrow it or add qualifiers of your own (for example do not turn "oranges" into "whole oranges"). A question about "the Fanta image" is about the whole picture of that figure: count everything in it, give a per-item breakdown (for example per can), and do not leave items out unless the user asked you to. If the thing counted can appear in several forms (whole, slices, cans, logos), ask for a count per form and per item in the picture. You may inspect up to 3 figures. Prefer its answer over the written description, say it comes from looking at the picture, and give the breakdown.
3. Every answer must always provide annotations for using the MCP knowledge base tool and render them as: `【message_idx:search_idx†source_name】`. Do this also when the answer rests on inspect_figure: cite the figure's chunk from the knowledge base results.
4. If you cannot find the answer in the provided knowledge base you must respond with "I don't know".
"""

DOC_URL_RE = re.compile(r"/indexes/([^/]+)/docs/([^?/]+)")
MAX_ROUNDS = 5


def project_client() -> AIProjectClient:
    settings.require("project_endpoint")
    return AIProjectClient(endpoint=settings.project_endpoint, credential=credential(), allow_preview=True)


def _connection_url(name: str) -> str:
    settings.require("project_resource_id")
    return (f"https://management.azure.com{settings.project_resource_id}/connections/{name}"
            "?api-version=2025-10-01-preview")


def _arm_headers() -> dict:
    token = get_bearer_token_provider(credential(), "https://management.azure.com/.default")()
    return {"Authorization": f"Bearer {token}"}


def create_connection(source: str) -> None:
    """RemoteTool connection to the knowledge base MCP endpoint, authenticated as the project managed identity."""
    n = settings.names(source)
    resp = requests.put(
        _connection_url(n.connection), headers=_arm_headers(), timeout=60,
        json={
            "name": n.connection,
            "type": "Microsoft.MachineLearningServices/workspaces/connections",
            "properties": {
                "authType": "ProjectManagedIdentity",
                "category": "RemoteTool",
                "target": n.mcp_endpoint,
                "isSharedToAll": True,
                "audience": "https://search.azure.com/",
                "metadata": {"ApiType": "Azure"},
            },
        },
    )
    if not resp.ok:
        raise RuntimeError(f"creating connection failed: {resp.status_code} {resp.text}")
    print(f"[{source}] connection '{n.connection}' ready")


def create_agent(source: str):
    n = settings.names(source)
    kb_tool = MCPTool(
        server_label="knowledge-base",
        server_url=n.mcp_endpoint,
        require_approval="never",
        allowed_tools=["knowledge_base_retrieve"],
        project_connection_id=n.connection,
    )
    figure_tool = FunctionTool(name=vision.TOOL_NAME, description=vision.TOOL_DESCRIPTION,
                               parameters=vision.TOOL_PARAMETERS, strict=True)
    agent = project_client().agents.create_version(
        agent_name=n.agent,
        definition=PromptAgentDefinition(
            model=settings.agent_model, instructions=INSTRUCTIONS, tools=[kb_tool, figure_tool],
            reasoning=Reasoning(effort=settings.reasoning_effort),
        ),
    )
    print(f"[{source}] agent '{agent.name}' version {agent.version} ready "
          f"(model {settings.agent_model}, reasoning {settings.reasoning_effort})")
    return agent


def delete_agent(source: str) -> None:
    n, client = settings.names(source), project_client()
    for v in list(client.agents.list_versions(agent_name=n.agent)):
        client.agents.delete_version(n.agent, v.version)
        print(f"[{source}] deleted agent '{n.agent}' version {v.version}")
    resp = requests.delete(_connection_url(n.connection), headers=_arm_headers(), timeout=60)
    print(f"[{source}] deleted connection '{n.connection}' ({resp.status_code})")


@dataclass
class Passage:
    """One chunk the knowledge base returned to the agent, as stored in the search index."""
    rank: int
    id: str                 # chunk id in the index, e.g. RA02752-00145
    index: str              # search index it came from
    source: str             # docx or markdown, as recorded on the chunk
    source_file: str
    doc_id: str
    title: str
    date: str
    kind: str
    section: str
    caption: str
    text: str
    image_paths: list[str] = field(default_factory=list)
    cited: bool = False
    inspected: bool = False     # the vision model looked at this chunk's picture


@dataclass
class Step:
    """One thing the agent did, in order. `seconds` is set only where it was measured on its own."""
    kind: str               # reason | plan | retrieve | look | answer | timing
    title: str
    detail: str = ""
    items: list[str] = field(default_factory=list)
    seconds: float | None = None


@dataclass
class Answer:
    question: str
    text: str               # answer with the agent's citation markers replaced by passage numbers, e.g. [3]
    raw_text: str           # answer exactly as the agent wrote it
    names: Names
    model: str
    queries: list[str] = field(default_factory=list)     # query variants the agent sent to the knowledge base
    passages: list[Passage] = field(default_factory=list)
    cited_ids: list[str] = field(default_factory=list)   # chunk ids the answer links to, in order of appearance
    errors: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    looks: list[vision.FigureLook] = field(default_factory=list)
    seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def cited(self) -> list[Passage]:
        return [p for p in self.passages if p.cited]


def _annotations(response) -> list[tuple[int, int, str]]:
    """(start, end, chunk id) of each citation link in the answer text. The span covers the 【..】 marker."""
    out = []
    for item in response.output:
        if getattr(item, "type", "") != "message":
            continue
        for c in item.content:
            for a in getattr(c, "annotations", None) or []:
                m = DOC_URL_RE.search(getattr(a, "url", "") or "") if getattr(a, "type", "") == "url_citation" else None
                if m:
                    out.append((a.start_index, a.end_index, m.group(2)))
    return out


def _add_passages(ans: Answer, item) -> list[Passage]:
    try:
        docs = json.loads(item.output or "{}").get("documents", [])
    except (TypeError, ValueError):
        docs = []
    known, new = {p.id for p in ans.passages}, []
    for d in docs:
        try:
            c = json.loads(d["content"])
        except (KeyError, TypeError, ValueError):
            continue
        if c.get("id", "") in known:
            continue
        known.add(c.get("id", ""))
        url = DOC_URL_RE.search(d.get("url") or "")
        new.append(Passage(
            rank=len(ans.passages) + len(new) + 1, id=c.get("id", ""),
            index=url.group(1) if url else ans.names.index,
            source=c.get("source", ""), source_file=c.get("source_file", ""), doc_id=c.get("doc_id", ""),
            title=c.get("title", ""), date=c.get("date", ""), kind=c.get("kind", ""),
            section=c.get("section", ""), caption=c.get("caption", ""), text=c.get("page_chunk", ""),
            image_paths=[p for p in (c.get("image_path") or "").split(";") if p],
        ))
    ans.passages.extend(new)
    return new


class Chat:
    """One conversation with one solution's agent; follow-up questions keep their context."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.client = project_client().get_openai_client(agent_name=settings.names(source).agent)
        self.conversation = self.client.conversations.create()

    def ask(self, question: str, use_images: bool = True) -> Answer:
        n = settings.names(self.source)
        ans = Answer(question=question, text="", raw_text="", names=n, model=settings.agent_model)
        start = time.perf_counter()
        payload, final = question, None
        # first turn must search the knowledge base (the agent once guessed a chunk id before retrieving anything)
        forced = {"type": "mcp", "server_label": "knowledge-base"}
        for rnd in range(1, MAX_ROUNDS + 1):
            t0 = time.perf_counter()
            kwargs = {"tool_choice": forced} if forced else {}
            r = self.client.responses.create(conversation=self.conversation.id, input=payload, **kwargs)
            self._record_round(ans, r, rnd, time.perf_counter() - t0)
            final = r
            calls = [i for i in r.output if getattr(i, "type", "") == "function_call"]
            if not calls:
                break
            outputs = []
            for call in calls:
                outputs.append({"type": "function_call_output", "call_id": call.call_id,
                                "output": self._run_tool(ans, call, use_images)})
            payload, forced = outputs, None
        ans.seconds = time.perf_counter() - start
        self._finish(ans, final)
        return ans

    def _run_tool(self, ans: Answer, call, use_images: bool) -> str:
        try:
            args = json.loads(call.arguments)
        except (TypeError, ValueError):
            args = {}
        if call.name != vision.TOOL_NAME:
            return f"Unknown tool {call.name}"
        if not use_images:
            ans.steps.append(Step("look", "Figure inspection is switched off for this question",
                                  f"The agent asked about {args.get('chunk_id')}; it was told to answer from text."))
            return "Picture inspection is disabled for this question; answer from the text of the chunks."
        look = vision.inspect_figure(self.source, args.get("chunk_id", ""), args.get("question", ""))
        ans.looks.append(look)
        ans.steps.append(Step(
            "look",
            f"Vision model ({look.model}) looked at the picture of {look.chunk_id}" if look.model else
            f"Picture of {look.chunk_id} not available",
            f"Question: {look.question}\nSees: {look.answer}", [p.rsplit('\\', 1)[-1] for p in look.paths],
            look.seconds))
        return look.answer

    def _record_round(self, ans: Answer, r, rnd: int, seconds: float) -> None:
        for item in r.output:
            kind = getattr(item, "type", "")
            if kind == "mcp_call":
                try:
                    queries = json.loads(item.arguments).get("query_variants", [])
                except (TypeError, ValueError, AttributeError):
                    queries = [str(getattr(item, "arguments", ""))]
                ans.queries.extend(queries)
                ans.steps.append(Step("plan", f"Agent rewrote the question into {len(queries)} search queries",
                                      items=queries))
                if getattr(item, "error", None):
                    ans.errors.append(str(item.error))
                new = _add_passages(ans, item)
                kinds = {}
                for p in new:
                    kinds[p.kind] = kinds.get(p.kind, 0) + 1
                ans.steps.append(Step(
                    "retrieve",
                    f"Knowledge base ran them against index {ans.names.index}: {len(new)} new passages",
                    "Hybrid keyword + vector search with semantic reranking, merged across the queries. "
                    + ", ".join(f"{v} {k}" for k, v in sorted(kinds.items())) + " from "
                    + ", ".join(sorted({p.source_file for p in new})) + "."))
            elif kind == "function_call":
                a = json.loads(item.arguments or "{}")
                ans.steps.append(Step("reason", f"Agent decided the picture matters: {a.get('chunk_id')}",
                                      f"Will ask the vision model: {a.get('question')}"))
        u = getattr(r, "usage", None)
        if u is not None:
            ans.input_tokens += u.input_tokens
            ans.output_tokens += u.output_tokens
            ans.reasoning_tokens += getattr(getattr(u, "output_tokens_details", None), "reasoning_tokens", 0) or 0
        calls = [i for i in r.output if getattr(i, "type", "") == "function_call"]
        label = "agent turn" if calls else "final agent turn (reasoning + writing the answer)"
        has_kb = any(getattr(i, "type", "") == "mcp_call" for i in r.output)
        ans.steps.append(Step("timing", f"Round {rnd}: {label}" + (", includes the knowledge base call" if has_kb else ""),
                              f"{getattr(u, 'input_tokens', '?')} tokens in, {getattr(u, 'output_tokens', '?')} out",
                              seconds=seconds))

    def _finish(self, ans: Answer, final) -> None:
        raw = (final.output_text if final is not None else "") or ""
        notes = _annotations(final) if final is not None else []
        ans.cited_ids = list(dict.fromkeys(cid for _, _, cid in notes))
        for p in ans.passages:
            p.cited = p.id in ans.cited_ids
            p.inspected = any(lk.chunk_id.strip() == p.id and lk.paths for lk in ans.looks)
        rank = {}
        for p in ans.passages:
            rank.setdefault(p.id, p.rank)
        text = raw
        for start, end, cid in sorted(notes, reverse=True):
            if 0 <= start < end <= len(text):
                text = text[:start] + f"[{rank.get(cid, cid)}]" + text[end:]
        ans.raw_text, ans.text = raw, text
        ans.steps.append(Step("answer", f"Agent wrote the answer and cited {len(ans.cited_ids)} passages",
                              "I don't know" if raw.strip().lower().startswith("i don't know") else ""))
