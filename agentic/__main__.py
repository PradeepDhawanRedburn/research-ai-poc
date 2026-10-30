"""Agentic retrieval over the research reports: Azure AI Search knowledge base + Foundry agent through MCP.

Two solutions with the same chunking, index schema and agent. Only the input differs:
  docx      the original Word files (data/raw), read by rag/loaders/docx_source.py
  markdown  the MarkItDown A+ output (out/A+)
Each has its own index, knowledge source, knowledge base, project connection and agent, so every answer can be traced
to one of them.

  python -m agentic check     show the resolved configuration and what is reachable
  python -m agentic index     create the search indexes and load the reports into them
  python -m agentic kb        create the knowledge sources and knowledge bases
  python -m agentic agent     create the project connections and agents
  python -m agentic up        index + kb + agent
  python -m agentic ask       chat with the agents (one question, or interactive)
  python -m agentic eval      run golden/questions.yaml through the agents; reports/agentic_eval.{jsonl,md}
  python -m agentic cleanup   delete agents, connections, knowledge bases, knowledge sources and indexes

All commands take --source docx, markdown or docx,markdown (default: both).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from .config import SOURCES, settings


def cmd_check(args) -> int:
    print(f"{'search endpoint':20} {settings.search_endpoint or '(not set)'}")
    print(f"{'project endpoint':20} {settings.project_endpoint or '(not set)'}")
    print(f"{'project resource':20} {settings.resource_parts() if settings.project_resource_id else '(not set)'}")
    print(f"{'vectorizer endpoint':20} {settings.openai_endpoint or '(not set)'}")
    print(f"{'embedding':20} {settings.embedding_deployment}")
    print(f"{'agent model':20} {settings.agent_model}")
    for s in args.source:
        n = settings.names(s)
        print(f"\n[{s}] index {n.index} | knowledge source {n.knowledge_source} | knowledge base {n.knowledge_base}"
              f" | connection {n.connection} | agent {n.agent}\n  MCP endpoint {n.mcp_endpoint}")
    print("\nsearch service:", end=" ")
    try:
        from .search_setup import index_client

        c = index_client()
        have = set(c.list_index_names())
        print(f"reachable; indexes present: {[settings.names(s).index for s in args.source if settings.names(s).index in have]}")
    except Exception as e:  # noqa: BLE001 - diagnostic output
        print(f"FAILED {type(e).__name__}: {str(e)[:200]}")
    print("foundry project:", end=" ")
    try:
        from .foundry import project_client

        have = {a.name for a in project_client().agents.list()}
        print(f"reachable; agents present: {[settings.names(s).agent for s in args.source if settings.names(s).agent in have]}")
    except Exception as e:  # noqa: BLE001
        print(f"FAILED {type(e).__name__}: {str(e)[:200]}")
    return 0


def cmd_index(args) -> int:
    from .search_setup import create_index, ingest

    for s in args.source:
        create_index(s, reset=args.reset)
        print(f"[{s}] {ingest(s, args.inputs, args.vision)} chunks indexed")
    return 0


def cmd_kb(args) -> int:
    from .search_setup import create_knowledge_base

    for s in args.source:
        create_knowledge_base(s, max_docs=args.max_docs)
    return 0


def cmd_agent(args) -> int:
    from .foundry import create_agent, create_connection

    for s in args.source:
        create_connection(s)
        create_agent(s)
    return 0


def cmd_up(args) -> int:
    cmd_index(args)
    cmd_kb(args)
    return cmd_agent(args)


def print_answer(a, show_evidence: bool = True) -> None:
    n = a.names
    print(f"\n=== {n.source}: index {n.index} | knowledge base {n.knowledge_base} | agent {n.agent} ({a.model}) ===")
    print(a.text)
    print(f"\n[{a.seconds:.1f} s, {a.input_tokens} tokens in / {a.output_tokens} out "
          f"({a.reasoning_tokens} reasoning)]")
    if not show_evidence:
        return
    print("\nSteps:")
    for i, st in enumerate((s for s in a.steps if s.kind != "timing"), 1):
        secs = f"  ({st.seconds:.1f} s)" if st.seconds is not None else ""
        print(f"  {i}. {st.title}{secs}")
        for line in ([st.detail] if st.detail else []) + [f"- {x}" for x in st.items]:
            print(f"       {line}")
    print("Timing:", "; ".join(f"{s.title.split(':')[0]} {s.seconds:.1f} s" for s in a.steps if s.kind == "timing"))
    print(f"Passages retrieved ({len(a.passages)}); * = linked from the answer:")
    for p in a.passages:
        label = (p.caption or p.section)[:80]
        print(f"  {'*' if p.cited else ' '} [{p.rank}] {p.id}  {p.source}:{p.source_file}  {p.kind}  {label}")
    for e in a.errors:
        print(f"  ERROR from knowledge base call: {e}")


def cmd_ask(args) -> int:
    from .foundry import Chat

    chats = {s: Chat(s) for s in args.source}

    def one(q: str) -> None:
        for s, chat in chats.items():
            print_answer(chat.ask(q, use_images=not args.no_images), not args.quiet)

    if args.question:
        one(" ".join(args.question))
        return 0
    print("Ask a question (empty line to quit).")
    while True:
        try:
            q = input("\n> ").strip()
        except EOFError:
            break
        if not q:
            break
        one(q)
    return 0


def cmd_eval(args) -> int:
    from .evaluate import run

    if args.judge:
        object.__setattr__(settings, "judge_deployment", args.judge)
    path = run(args.source, ids=set(args.ids.split(",")) if args.ids else None,
               types=set(args.types.split(",")) if args.types else None, limit=args.limit,
               use_images=not args.no_images, resume=args.resume)
    print(f"report: {path}\nfour-way table: python eval/scores.py reports/rag_eval.jsonl reports/agentic_eval.jsonl"
          " reports/four_way.md")
    return 0


def cmd_cleanup(args) -> int:
    from .foundry import delete_agent
    from .search_setup import delete_search_objects

    for s in args.source:
        delete_agent(s)
        delete_search_objects(s)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agentic", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def source_arg(p) -> None:
        p.add_argument("--source", default=list(SOURCES),
                       type=lambda s: [x for x in s.split(",") if x in SOURCES] or ap.error(f"bad --source {s}"),
                       help="docx, markdown, or docx,markdown (default)")

    def tuning(p, *names: str) -> None:
        if "chunk" in names:
            p.add_argument("--chunk-chars", type=int, help="chunk size at ingestion (default RAG_CHUNK_CHARS or 1800); "
                           "needs --reset to rebuild the index")
        if "docs" in names:
            p.add_argument("--max-docs", type=int, help=f"most passages the knowledge base returns per retrieval "
                           f"(default {settings.max_docs})")
        if "reasoning" in names:
            p.add_argument("--reasoning", choices=("minimal", "low", "medium", "high"),
                           help=f"agent reasoning effort (default {settings.reasoning_effort})")

    for name, fn in (("check", cmd_check), ("cleanup", cmd_cleanup)):
        p = sub.add_parser(name)
        source_arg(p)
        p.set_defaults(fn=fn)
    p = sub.add_parser("kb")
    source_arg(p)
    tuning(p, "docs")
    p.set_defaults(fn=cmd_kb)
    p = sub.add_parser("agent")
    source_arg(p)
    tuning(p, "reasoning")
    p.set_defaults(fn=cmd_agent)
    for name, fn in (("index", cmd_index), ("up", cmd_up)):
        p = sub.add_parser(name)
        source_arg(p)
        p.add_argument("inputs", nargs="*", help="files or folders (default: data/raw for docx, out/A+ for markdown)")
        p.add_argument("--vision", choices=("auto", "all", "none"), default="auto",
                       help="markdown: which pictures to describe (auto = those without digitised values); "
                            "docx: 'none' skips descriptions, otherwise all pictures are described")
        p.add_argument("--reset", action="store_true", help="drop and recreate the indexes first")
        tuning(p, "chunk", *(("docs", "reasoning") if name == "up" else ()))
        p.set_defaults(fn=fn)
    p = sub.add_parser("ask")
    source_arg(p)
    p.add_argument("question", nargs="*")
    p.add_argument("--quiet", action="store_true", help="do not list steps and passages")
    p.add_argument("--no-images", action="store_true", help="do not let the agent look at pictures")
    p.set_defaults(fn=cmd_ask)
    p = sub.add_parser("eval")
    source_arg(p)
    p.add_argument("--ids", help="comma-separated question ids")
    p.add_argument("--types", help="comma-separated question types")
    p.add_argument("--limit", type=int)
    p.add_argument("--no-images", action="store_true", help="do not let the agent look at pictures")
    p.add_argument("--judge", help=f"deployment that grades the answers (default {settings.judge_deployment})")
    p.add_argument("--resume", action="store_true", help="continue reports/agentic_eval.jsonl, skipping done pairs")
    p.set_defaults(fn=cmd_eval)

    args = ap.parse_args(argv)
    if getattr(args, "chunk_chars", None):
        os.environ["RAG_CHUNK_CHARS"] = str(args.chunk_chars)  # read by rag.config when the loaders are imported
    for opt, attr in (("max_docs", "max_docs"), ("reasoning", "reasoning_effort")):
        if getattr(args, opt, None):
            object.__setattr__(settings, attr, getattr(args, opt))  # settings is frozen; this is a one-run override
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # citations use 【】 which the Windows code page lacks
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    for noisy in ("httpx", "openai", "azure"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
