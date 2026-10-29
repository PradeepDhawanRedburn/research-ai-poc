"""CLI: python -m rag {ingest,ask,eval,inspect} --source {docx,markdown}

  ingest   load reports, describe pictures, embed, store in the local Chroma collection of that solution
  ask      answer a question (or start an interactive prompt) from one or both solutions
  eval     run golden/questions.yaml through the solutions and write reports/rag_eval.md
  inspect  write the chunks a solution would index to .rag_cache/chunks_<source>.jsonl, no embedding calls
           (pictures are still described unless --vision none)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from .config import SOURCES, settings


def _load(source: str, inputs: list[str] | None, vision: str):
    from .chunking import to_documents

    if source == "docx":
        from .loaders.docx_source import discover, load_docx

        for path in discover(inputs, settings.docx_dir):
            info, blocks = load_docx(path, vision=vision)
            yield info, to_documents(info, blocks, source)
    else:
        from .loaders.markdown_source import discover, load_markdown

        for path in discover(inputs, settings.markdown_dir):
            info, blocks = load_markdown(path, vision=vision)
            yield info, to_documents(info, blocks, source)


def cmd_ingest(args) -> int:
    from .store import replace_document, vector_store

    for source in args.source:
        vs = vector_store(source)
        if args.reset:
            vs.reset_collection()
        for info, docs in _load(source, args.inputs, args.vision):
            replace_document(vs, info.doc_id, docs)
            kinds = {k: sum(d.metadata["kind"] == k for d in docs) for k in ("text", "table", "figure", "chart")}
            print(f"[{source}] {info.doc_id}: {len(docs)} chunks {kinds}  {info.header()}")
    return 0


def cmd_inspect(args) -> int:
    for source in args.source:
        path = settings.cache_dir / f"chunks_{source}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        with open(path, "w", encoding="utf-8") as fh:
            for _, docs in _load(source, args.inputs, args.vision):
                for d in docs:
                    fh.write(json.dumps({"text": d.page_content, **d.metadata}, ensure_ascii=False) + "\n")
                    n += 1
        print(f"[{source}] {n} chunks -> {path}")
    return 0


def _print_answer(source: str, a, show_sources: bool) -> None:
    print(f"\n=== {source} ===\n{a.answer}")
    if show_sources:
        print("\nSources:")
        for n, d in enumerate(a.sources, 1):
            m = d.metadata
            label = m.get("caption") or m.get("section") or ""
            print(f"  [{n}] {m.get('doc_id')} {m.get('kind')}: {label[:90]}")
        if a.images:
            print(f"  ({len(a.images)} image(s) shown to the model)")


def cmd_ask(args) -> int:
    from .pipeline import answer

    def one(q: str) -> None:
        for source in args.source:
            a = answer(q, source, k=args.k, doc_id=args.doc, hybrid=not args.no_hybrid,
                       use_images=not args.no_images)
            _print_answer(source, a, not args.quiet)

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

    path = run(args.source, ids=set(args.ids.split(",")) if args.ids else None,
               types=set(args.types.split(",")) if args.types else None, limit=args.limit)
    print(f"report: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m rag", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def source_arg(p, default: str) -> None:
        p.add_argument("--source", default=default,
                       type=lambda s: [x for x in s.split(",") if x in SOURCES] or ap.error(f"bad --source {s}"),
                       help="docx, markdown, or docx,markdown")

    for name, fn in (("ingest", cmd_ingest), ("inspect", cmd_inspect)):
        p = sub.add_parser(name)
        source_arg(p, "docx,markdown")
        p.add_argument("inputs", nargs="*", help="files or folders (default: data/raw for docx, out/A+ for markdown)")
        p.add_argument("--vision", choices=("auto", "all", "none"), default="auto",
                       help="markdown: which pictures to describe (auto = those without digitised values); "
                            "docx: 'none' skips descriptions, otherwise all pictures are described")
        if name == "ingest":
            p.add_argument("--reset", action="store_true", help="empty the collection first")
        p.set_defaults(fn=fn)

    p = sub.add_parser("ask")
    source_arg(p, "docx,markdown")
    p.add_argument("question", nargs="*")
    p.add_argument("--k", type=int, default=None, help=f"chunks to retrieve (default {settings.top_k})")
    p.add_argument("--doc", help="restrict to one report, e.g. RA01220")
    p.add_argument("--no-hybrid", action="store_true", help="vector search only, no BM25 fusion")
    p.add_argument("--no-images", action="store_true", help="text-only answer, do not attach pictures")
    p.add_argument("--quiet", action="store_true", help="do not list sources")
    p.set_defaults(fn=cmd_ask)

    p = sub.add_parser("eval")
    source_arg(p, "docx,markdown")
    p.add_argument("--ids", help="comma-separated question ids")
    p.add_argument("--types", help="comma-separated question types")
    p.add_argument("--limit", type=int)
    p.set_defaults(fn=cmd_eval)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "chromadb", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
