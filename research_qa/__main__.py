"""Command line: python -m research_qa <command>

  upload <files...>        copy local .docx files into raw/ in the store
  ingest                   Word -> Markdown+, assets, facts, chunks, manifest (from raw/ in the store)
  index                    build the Markdown+ search index and push the chunks
  knowledge                knowledge sources and bases (Blob for Word, index for Markdown+)
  agents                   Foundry connections and agents, one per arm
  up                       knowledge + agents for every arm
  ask "<question>"         answer with one arm and mode, print passages, steps and the usage line
  eval                     run the golden questions through arms x modes, write a scored run to eval/<run>/
  retention                evidence retention and retrieval of Markdown+ without a model
  status                   what is in the store and in Azure
  down                     delete agents, knowledge bases, sources and the Markdown+ index (never the store)
  app                      print the command that starts the Streamlit app

Azure parts are imported only by the commands that need them, so `status` and `retention` work with the local store.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import ARMS, DEFAULT_ARMS, DEFAULT_MODES, MODES, canon_mode, combo_key, settings
from .storage import CHUNKS, FACTS, MANIFEST, MARKDOWN, RAW, open_store



def _arms(value: str | None) -> list[str]:
    arms = [a for a in (value or ",".join(DEFAULT_ARMS)).split(",") if a]
    bad = [a for a in arms if a not in ARMS]
    if bad:
        raise SystemExit(f"unknown arm(s) {bad}; choose from {list(ARMS)}")
    return arms


def _modes(value: str | None) -> list[str]:
    modes = [canon_mode(m) for m in (value or ",".join(DEFAULT_MODES)).split(",") if m]
    bad = [m for m in modes if m not in MODES]
    if bad:
        raise SystemExit(f"unknown mode(s) {bad}; choose from {list(MODES)}")
    return modes


def _azure(what: str, fn, *args, **kwargs):
    """Run an Azure-backed step; a missing package or setting becomes one line, not a traceback."""
    try:
        return fn(*args, **kwargs)
    except ImportError as e:
        raise SystemExit(f"{what}: Python package missing ({e.name}); install the project with its extras") from e
    except RuntimeError as e:
        raise SystemExit(f"{what}: {e}") from e


# ----------------------------------------------------------------------------- commands

def cmd_upload(args) -> int:
    store = open_store()
    for f in args.files:
        p = Path(f)
        if p.suffix.lower() != ".docx":
            print(f"skipped {p.name}: not a .docx")
            continue
        store.upload_file(p, f"{RAW}/{p.name}")
        print(f"uploaded {p.name} -> {RAW}/{p.name}")
    return 0


def cmd_ingest(args) -> int:
    from .ingest.pipeline import ingest

    store = open_store()
    manifests = _azure("ingest", ingest, args.doc or None, store, vision=args.vision, force=args.force)
    for m in manifests:
        print(f"{m.doc_id}: {m.chunks} chunks, {m.figures} figures ({m.charts_native} native charts, "
              f"{m.charts_digitised_ok} digitised, {m.charts_digitised_withheld} withheld), {m.pictures_described}/"
              f"{m.pictures} pictures described, {m.facts_figures} fact figures, {m.seconds:.0f} s"
              + (f", {len(m.warnings)} warnings" if m.warnings else ""))
    if args.publish_container:
        from azure.storage.blob import BlobServiceClient

        if settings.storage_connection_string:
            service = BlobServiceClient.from_connection_string(settings.storage_connection_string)
        else:
            from azure.identity import DefaultAzureCredential

            service = BlobServiceClient(account_url=settings.storage_account_url,
                                        credential=DefaultAzureCredential(process_timeout=60))
        target_client = service.get_container_client(args.publish_container)
        if not target_client.exists():
            raise SystemExit(f"publish target container {args.publish_container!r} does not exist; no container was created")
        from .storage import BlobStore
        from .ingest.publish_md_docs import publish_md_docs

        target = BlobStore(args.publish_container, settings.storage_account_url, settings.storage_connection_string)
        result = _azure("publish-md-docs", publish_md_docs, store, target, args.doc or None,
                        dry_run=args.publish_dry_run)
        action = "would write" if args.publish_dry_run else "wrote"
        print(f"{action} {len(result['written'])} legacy-layout blobs for {len(result['documents'])} reports "
              f"to container {args.publish_container}")
        if result["removed"]:
            verb = "would remove" if args.publish_dry_run else "removed"
            print(f"{verb} {len(result['removed'])} stale report blobs")
    return 0


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


LC = settings.retrieval_backend == "langchain"
LC_SKIP = "not used with RETRIEVAL_BACKEND=langchain; run `index` (Chroma collections are built from the store)"


def cmd_index(args) -> int:
    store = open_store()
    if LC:
        from .lc.index import build

        for arm in _arms(getattr(args, "arm", None)):
            n = _azure("index", build, arm, store, args.doc or None, reset=args.reset)
            print(f"[{arm}] {n} chunks in Chroma collection research-{arm} ({settings.chroma_dir})")
        return 0
    from .search.index import create_index, push

    _azure("index", create_index, reset=args.reset)
    n = _azure("index", push, store, args.doc or None)
    print(f"{n} chunks in index {settings.names('markdown-plus').index}")
    return 0


def cmd_knowledge(args) -> int:
    if LC:
        print(LC_SKIP)
        return 0
    from .search.knowledge import create_knowledge

    for arm in _arms(args.arm):
        _azure("knowledge", create_knowledge, arm, max_docs=args.max_docs)
    return 0


def cmd_agents(args) -> int:
    if LC:
        print(LC_SKIP)
        return 0
    from .agent.foundry import create_agent, create_connection

    for arm in _arms(args.arm):
        _azure("agents", create_connection, arm)
        _azure("agents", create_agent, arm)
    return 0


def cmd_up(args) -> int:
    if LC:
        print(LC_SKIP)
        return 0
    args.max_docs = getattr(args, "max_docs", None)
    cmd_knowledge(args)
    cmd_agents(args)
    return 0


def cmd_ask(args) -> int:
    from .agent.ask import ask

    a = _azure("ask", ask, args.question, args.arm, args.mode, use_tools=not args.no_tools)
    print(a.text or "(empty answer)")
    print()
    print(f"{a.arm}/{a.mode} · {a.model} · {a.total_s:.1f} s · {a.total_tokens:,} tokens "
          f"({a.input_tokens:,} in, {a.output_tokens:,} out, {a.reasoning_tokens:,} reasoning, {a.vision_tokens:,} vision)"
          f" · {len(a.passages)} passages, {len(a.cited)} cited, {len(a.tool_calls)} tool calls")
    for e in a.errors:
        print(f"error: {e}")
    if a.steps:
        print("\nSteps")
        for i, s in enumerate(a.steps, 1):
            secs = f" ({s.seconds:.1f} s)" if s.seconds is not None else ""
            print(f" {i}. {s.title}{secs}")
            for it in s.items:
                print(f"    - {it}")
    if a.passages:
        print("\nPassages")
        for p in a.passages:
            tag = " cited" if p.cited else ""
            print(f" [{p.rank}]{tag} {p.doc_id} {p.kind} {p.heading[:60]} ({p.source_file}, {p.id})")
    return 0


def cmd_eval(args) -> int:
    from .evaluate.run import run

    store = open_store()
    key = _azure("eval", run, _arms(args.arm), _modes(args.mode), ids=args.ids.split(",") if args.ids else None,
                 types=args.types.split(",") if args.types else None, limit=args.limit, resume=args.resume,
                 run_name=args.run, store=store, use_tools=not args.no_tools, redo=args.redo,
                 distractor_docs=args.distractor_docs.split(",") if args.distractor_docs else None)
    print(f"\nrun written to {key}/ (rows.jsonl, summary.json, report.md) in {store.describe()}")
    print(store.text(f"{key}/report.md").split("## By question type")[0])
    return 0


def cmd_reconcile(args) -> int:
    from .ingest.reconcile import orphans, reconcile

    store = open_store()
    gone = orphans(store)
    if not gone:
        print("nothing to reconcile: every derived document still has its raw/ file")
        return 0
    res = _azure("reconcile", reconcile, store, dry_run=args.dry_run)
    for d, r in res.items():
        verb = "would remove" if args.dry_run else "removed"
        print(f"[{d}] {verb} {len(r['keys'])} store objects" + (" and the manifest entry" if r["manifest"] else "")
              + ("" if args.dry_run else f"; index: {r['index']}"))
    return 0


def cmd_report(args) -> int:
    from .evaluate.baseline import report

    store = open_store()
    md, _ = _azure("report", report, args.run, store, args.k)
    print(md)
    print(f"written to eval/{args.run}/baseline-k{args.k}.md in {store.describe()}")
    return 0


def cmd_chart_report(args) -> int:
    from .evaluate.chart_quality import report

    print(report(open_store()))
    return 0


def cmd_export_markdown(args) -> int:
    from .export_markdown import export_markdown

    result = _azure("export-documents", export_markdown, open_store(), Path(args.out), dry_run=args.dry_run)
    action = "would write" if args.dry_run else "wrote"
    print(f"{action} {len(result['written'])} artifacts for {len(result['doc_ids'])} reports to {result['output']}")
    if result["removed"]:
        verb = "would remove" if args.dry_run else "removed"
        print(f"{verb} {len(result['removed'])} stale exporter-owned files")
        for path in result["removed"][:8]:
            print(f"  {path}")
        if len(result["removed"]) > 8:
            print(f"  ... and {len(result['removed']) - 8} more")
    return 0


def cmd_retention(args) -> int:
    from .evaluate.retention import check, report_markdown

    store = open_store()
    res = check(store, k=args.k)
    md = report_markdown(res)
    store.put("eval/retention.md", md, "text/markdown")
    print(md)
    return 0


def cmd_calibration_sample(args) -> int:
    from .evaluate.calibration import export_sample

    output = Path(args.out or f"eval-{args.run}-calibration.csv")
    count = _azure("calibration-sample", export_sample, open_store(), args.run, output, args.n)
    print(f"wrote {count} answered rows to {output}")
    return 0


def cmd_calibration_report(args) -> int:
    from .evaluate.calibration import agreement, agreement_markdown

    print(agreement_markdown(agreement(Path(args.csv))))
    return 0


def cmd_word_sync(args) -> int:
    if LC:
        print(LC_SKIP)
        return 0
    import json as _json

    from .search.knowledge import word_sync

    out = _azure("word-sync", word_sync, args.wait)
    print(_json.dumps(out, indent=1, default=str))
    return 0


def cmd_status(args) -> int:
    store = open_store()
    print(f"Store: {store.describe()}")
    docs = store.doc_ids()
    print(f"  raw Word files: {len(docs)}" + (f" ({', '.join(docs)})" if docs else ""))
    manifest = store.json(MANIFEST, {}) if store.exists(MANIFEST) else {}
    for d in docs:
        m = manifest.get(d)
        parts = [f"markdown {'yes' if store.exists(f'{MARKDOWN}/{d}.md') else 'no'}",
                 f"chunks {'yes' if store.exists(f'{CHUNKS}/{d}.jsonl') else 'no'}",
                 f"facts {'yes' if store.exists(f'{FACTS}/{d}.json') else 'no'}"]
        if m:
            parts.append(f"{m.get('chunks', 0)} chunks, {m.get('figures', 0)} figures, "
                         f"{m.get('pictures_described', 0)}/{m.get('pictures', 0)} pictures described")
        print(f"  {d}: " + ", ".join(parts))
    runs = sorted({k.split('/')[1] for k in store.keys('eval/') if k.count('/') >= 2}, reverse=True)
    print(f"  evaluation runs: {len(runs)}" + (f" (latest {runs[0]})" if runs else ""))

    if LC:
        print(f"\nRetrieval engine: langchain (Chroma at {settings.chroma_dir}, embeddings {settings.embedding_deployment}, "
              f"answers {settings.answer_model})")
        try:
            from .lc.index import stats as lc_stats
        except ImportError as e:
            print(f"  langchain packages not installed ({e.name}); install the project with --extra langchain")
            return 0
        from .acl import ACL_KEY

        has_acl = open_store().exists(ACL_KEY)
        for arm in ARMS:
            try:
                s = lc_stats(arm)
                print(f"  {arm}: {s}")
                if has_acl and s.get("chunks_without_acl"):
                    print(f"  WARNING {arm}: {s['chunks_without_acl']} chunks were indexed before acl.json existed and "
                          f"count as public; run `index --reset` to apply the access list")
            except Exception as e:  # noqa: BLE001
                print(f"  {arm}: not available ({type(e).__name__}: {str(e)[:120]})")
        return 0

    print("\nAzure")
    try:
        from .search.index import stats
        from .search.knowledge import blob_ingestion_status, kb_settings
    except ImportError as e:
        print(f"  search packages not installed ({e.name}); skipping")
        return 0
    if not settings.search_endpoint:
        print("  AZURE_SEARCH_ENDPOINT not set; skipping")
        return 0
    for arm in ARMS:
        n = settings.names(arm)
        try:
            kb = kb_settings(arm)
            print(f"  {arm}: knowledge base {n.knowledge_base}: {kb}")
        except Exception as e:  # noqa: BLE001
            print(f"  {arm}: knowledge base {n.knowledge_base} not available ({type(e).__name__}: {str(e)[:120]})")
        try:
            if ARMS[arm].source == "index":
                print(f"    index {n.index}: {stats(arm)}")
            else:
                print(f"    Blob ingestion: {blob_ingestion_status(arm)}")
        except Exception as e:  # noqa: BLE001
            print(f"    not available ({type(e).__name__}: {str(e)[:120]})")
    try:
        from .agent.foundry import list_agents

        print(f"  agents: {list_agents()}")
    except Exception as e:  # noqa: BLE001
        print(f"  agents: not available ({type(e).__name__}: {str(e)[:120]})")
    return 0


def cmd_down(args) -> int:
    if LC:
        from .lc.index import clear

        for arm in _arms(args.arm):
            _azure("down", clear, arm)
            print(f"[{arm}] Chroma collection research-{arm} removed")
        print("Chroma collections removed; the store is untouched.")
        return 0
    from .agent.foundry import delete_agent
    from .search.knowledge import delete_knowledge

    for arm in _arms(args.arm):
        for what, fn in (("agent", delete_agent), ("knowledge", delete_knowledge)):
            try:
                _azure("down", fn, arm)
            except SystemExit as e:
                print(f"{arm} {what}: {e}")
    if "markdown-plus" in _arms(args.arm):
        from .search.index import delete_index

        try:
            _azure("down", delete_index)
        except SystemExit as e:
            print(f"index: {e}")
    print("Azure objects removed; the store is untouched.")
    return 0


def cmd_app(args) -> int:
    print("streamlit run research_qa/app/Overview.py")
    return 0


# ----------------------------------------------------------------------------- parser

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research_qa", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true", help="log what the steps do")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("upload", help="copy local .docx files into raw/")
    p.add_argument("files", nargs="+")
    p.set_defaults(fn=cmd_upload)

    p = sub.add_parser("ingest", help="Word -> Markdown+, assets, facts, chunks, manifest")
    p.add_argument("--doc", nargs="*", help="report ids; default: every raw Word file")
    p.add_argument("--vision", choices=["auto", "all", "none"], default="auto",
                   help="describe pictures: auto = those without digitised values, all, none")
    p.add_argument("--force", action="store_true", help="re-run reports already in the manifest")
    p.add_argument("--publish-container", help="also publish legacy <doc>.md and <doc>/canonical.json/assets to this existing Blob container")
    p.add_argument("--publish-dry-run", action="store_true", help="preview legacy-container writes/deletions without changing it")
    p.set_defaults(fn=cmd_ingest)

    p = sub.add_parser("index", help="build the Markdown+ index and push the chunks (langchain engine: both arms\' Chroma collections)")
    p.add_argument("--reset", action="store_true", help="remove every document first")
    p.add_argument("--doc", nargs="*")
    p.add_argument("--arm", help="langchain engine only: comma-separated arms; default all")
    p.set_defaults(fn=cmd_index)

    for name, fn, help_ in (("knowledge", cmd_knowledge, "knowledge sources and bases"),
                            ("agents", cmd_agents, "Foundry connections and agents"),
                            ("up", cmd_up, "knowledge + agents")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--arm", help=f"comma-separated, from {list(ARMS)}; default all")
        if name in ("knowledge", "up"):
            p.add_argument("--max-docs", type=int, help="passages per retrieval (default MAX_DOCS)")
        p.set_defaults(fn=fn)

    p = sub.add_parser("ask", help="answer one question")
    p.add_argument("question")
    p.add_argument("--arm", default="markdown-plus", choices=list(ARMS))
    p.add_argument("--mode", default="agentic", type=canon_mode, choices=list(MODES))
    p.add_argument("--no-tools", action="store_true", help="no facts lookup, no picture inspection")
    p.set_defaults(fn=cmd_ask)

    p = sub.add_parser("eval", help="score the golden questions")
    p.add_argument("--arm", help=f"comma-separated, from {list(ARMS)}; default all")
    p.add_argument("--mode", help=f"comma-separated, from {list(MODES)}; default agentic")
    p.add_argument("--ids", help="comma-separated question ids")
    p.add_argument("--types", help="comma-separated question types")
    p.add_argument("--limit", type=int)
    p.add_argument("--resume", action="store_true", help="continue the run named by --run")
    p.add_argument("--redo", action="store_true",
                   help="in the run named by --run, drop and rerun the rows of the selected arms and modes, keep the rest")
    p.add_argument("--run", help="run name (default: UTC timestamp)")
    p.add_argument("--no-tools", action="store_true")
    p.add_argument("--distractor-docs", help="comma-separated indexed report ids included as retrieval noise, no questions")
    p.set_defaults(fn=cmd_eval)

    p = sub.add_parser("reconcile", help="remove everything left behind by documents deleted from raw/ (store and engine)")
    p.add_argument("--dry-run", action="store_true", help="list what would be removed, remove nothing")
    p.set_defaults(fn=cmd_reconcile)
    p = sub.add_parser("report", help="recall@k and p95 latency by question type from a run's rows, no model")
    p.add_argument("--run", required=True, help="run name under eval/")
    p.add_argument("--k", type=int, default=10)
    p.set_defaults(fn=cmd_report)
    sub.add_parser("chart-report", help="native-versus-digitised chart error distribution, no model").set_defaults(
        fn=cmd_chart_report)
    p = sub.add_parser("export-documents", help="export current report artifacts and media to a local folder")
    p.add_argument("--out", default="md-docs", help="output folder (default: ./md-docs)")
    p.add_argument("--dry-run", action="store_true", help="show writes and stale exporter-owned files, make no changes")
    p.set_defaults(fn=cmd_export_markdown)

    p = sub.add_parser("export-markdown", help="alias for export-documents")
    p.add_argument("--out", default="md-docs", help="output folder (default: ./md-docs)")
    p.add_argument("--dry-run", action="store_true", help="show writes and stale exporter-owned files, make no changes")
    p.set_defaults(fn=cmd_export_markdown)
    p = sub.add_parser("retention", help="evidence retention of Markdown+, no model")
    p.add_argument("--k", type=int, default=5)
    p.set_defaults(fn=cmd_retention)

    p = sub.add_parser("calibration-sample", help="export a stratified human judge-calibration CSV")
    p.add_argument("--run", required=True, help="evaluation run under eval/")
    p.add_argument("--n", type=_positive_int, default=50, help="answered rows to sample (default 50)")
    p.add_argument("--out", help="CSV output path (default eval-<run>-calibration.csv)")
    p.set_defaults(fn=cmd_calibration_sample)

    p = sub.add_parser("calibration-report", help="report judge agreement and Cohen's kappa from a graded CSV")
    p.add_argument("--csv", required=True, help="graded CSV with judge_verdict and human_verdict columns")
    p.set_defaults(fn=cmd_calibration_report)

    sub.add_parser("status", help="what is in the store and in Azure").set_defaults(fn=cmd_status)

    p = sub.add_parser("word-sync", help="make Azure ingest raw/ for the Word arm now and show the per-document result")
    p.add_argument("--wait", type=int, default=600, help="seconds to wait for the run to finish (default 600)")
    p.set_defaults(fn=cmd_word_sync)

    p = sub.add_parser("down", help="delete the Azure objects (not the store)")
    p.add_argument("--arm", help="comma-separated; default all")
    p.set_defaults(fn=cmd_down)

    sub.add_parser("app", help="print the Streamlit command").set_defaults(fn=cmd_app)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "azure", "openai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        print("interrupted")
        return 130
    except RuntimeError as e:  # a missing setting, reported as one line
        print(f"{args.cmd}: {e}", file=sys.stderr)
        return 2
    except ImportError as e:
        print(f"{args.cmd}: Python package missing ({e.name}); install the project with its extras", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
