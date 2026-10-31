"""Run the golden questions through any arm × mode and score them the same way.

Per question and combination: a fresh conversation, the answer, the judge's verdict, whether the golden evidence
strings are present in the passages returned (same matching as retention.py), seconds by phase, tokens by kind,
cost from the price table. Rows are appended to eval/<run>/rows.jsonl in the store as they are produced, so a run
interrupted by a token expiry or a throttle can be continued with resume=True. Then summary.json and report.md.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from ..config import ARMS, MODES, combo_key, settings
from ..schema import Answer
from ..storage import EVAL, Store, open_store
from . import report as report_mod
from .cost import prices
from .golden import load_questions
from .retention import evidence_items, normalise, present

log = logging.getLogger(__name__)

ROW_FIELDS = ["id", "type", "doc", "arm", "mode", "combo", "evidence_retrieved", "evidence_hits", "verdict", "reason",
              "answer", "expected", "retrieved", "cited", "queries", "tools", "images", "retrieval_s", "generation_s",
              "tools_s", "total_s", "rounds", "input_tokens", "output_tokens", "reasoning_tokens", "vision_tokens",
              "total_tokens", "cost", "errors", "model", "judge"]


def run_key(run_name: str) -> str:
    return f"{EVAL}/{run_name}"


def list_runs(store: Store | None = None) -> list[str]:
    """Run names in the store, newest first (names are timestamps unless the user chose one)."""
    store = store or open_store()
    names = sorted({k.split("/")[1] for k in store.keys(f"{EVAL}/") if k.count("/") >= 2}, reverse=True)
    return names


def _done(store: Store, key: str) -> set[tuple[str, str, str]]:
    if not store.exists(key):
        return set()
    out = set()
    for r in store.jsonl(key):
        try:
            out.add((r["id"], r["arm"], r["mode"]))
        except KeyError:
            continue
    return out


def _row(q: dict, arm: str, mode: str, a: Answer, verdict: dict, items: list[list[str]]) -> dict:
    ctx = normalise("\n".join(p.text for p in a.passages))
    hits = present(items, ctx)
    tools = [t.get("tool", "") for t in a.tool_calls]
    return {
        "id": q["id"], "type": q["type"], "doc": q["doc"], "arm": arm, "mode": mode, "combo": combo_key(arm, mode),
        "evidence_retrieved": all(hits) if items else None, "evidence_hits": f"{sum(hits)}/{len(hits)}",
        "verdict": verdict["verdict"], "reason": verdict["reason"],
        "answer": a.text, "expected": q.get("answer", ""),
        "retrieved": [p.id for p in a.passages], "cited": [p.id for p in a.cited],
        "queries": list(a.queries), "tools": tools, "images": len(a.inspected_assets) or sum(t == "inspect_figure" for t in tools),
        "retrieval_s": a.retrieval_s, "generation_s": a.generation_s, "tools_s": a.tools_s, "total_s": a.total_s,
        "rounds": a.rounds,
        "input_tokens": a.input_tokens, "output_tokens": a.output_tokens, "reasoning_tokens": a.reasoning_tokens,
        "vision_tokens": a.vision_tokens, "total_tokens": a.total_tokens,
        "cost": prices().cost_of(a), "errors": list(a.errors), "model": a.model or settings.answer_model,
        "judge": settings.judge_deployment,
    }


def _error_row(q: dict, arm: str, mode: str, err: Exception) -> dict:
    n = len(q.get("evidence", []))
    return {"id": q["id"], "type": q["type"], "doc": q["doc"], "arm": arm, "mode": mode, "combo": combo_key(arm, mode),
            "evidence_retrieved": False if n else None, "evidence_hits": f"0/{n}",
            "verdict": "incorrect", "reason": f"run failed: {type(err).__name__}: {str(err)[:300]}",
            "answer": "", "expected": q.get("answer", ""), "retrieved": [], "cited": [], "queries": [], "tools": [],
            "images": 0, "retrieval_s": 0.0, "generation_s": 0.0, "tools_s": 0.0, "total_s": 0.0, "rounds": 0,
            "input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "vision_tokens": 0, "total_tokens": 0,
            "cost": None, "errors": [str(err)[:300]], "model": settings.answer_model, "judge": settings.judge_deployment}


def run(arms: list[str], modes: list[str], ids=None, types=None, limit: int | None = None, resume: bool = False,
        run_name: str | None = None, store: Store | None = None, ask_fn=None, judge_fn=None, use_tools: bool = True,
        progress=print, redo: bool = False) -> str:
    """Returns the run key (eval/<run>). `ask_fn(question, arm, mode)` and `judge_fn(q, text)` can be swapped for
    tests; by default they are the agent/direct pipeline and the Azure OpenAI judge."""
    for a in arms:
        if a not in ARMS:
            raise ValueError(f"unknown arm {a!r}; choose from {list(ARMS)}")
    for m in modes:
        if m not in MODES:
            raise ValueError(f"unknown mode {m!r}; choose from {list(MODES)}")
    store = store or open_store()
    if ask_fn is None:
        from ..agent.ask import ask

        ask_fn = lambda question, arm, mode: ask(question, arm, mode, use_tools=use_tools)  # noqa: E731
    if judge_fn is None:
        from .judge import default_judge

        judge_fn = default_judge()

    run_name = run_name or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    key = run_key(run_name)
    rows_key = f"{key}/rows.jsonl"
    if redo and store.exists(rows_key):
        # keep the other arms' and modes' rows of this run, rerun the selected combinations from scratch
        kept = [l for l in store.text(rows_key).splitlines()
                if l.strip() and not (json.loads(l).get("arm") in arms and json.loads(l).get("mode") in modes)]
        store.put(rows_key, "".join(k + "\n" for k in kept), "application/x-ndjson")
        resume = True
    done = _done(store, rows_key) if resume else set()
    if not resume and store.exists(rows_key):
        store.delete(rows_key)
    questions = load_questions(ids, types, limit)
    todo = [(q, a, m) for q in questions for a in arms for m in modes if (q["id"], a, m) not in done]
    log.info("%d question/arm/mode triples to run (%d already done)", len(todo), len(done))
    store.put_json(f"{key}/config.json", {
        "run": run_name, "arms": arms, "modes": modes, "questions": len(questions), "resume": resume,
        "answer_model": settings.answer_model, "vision_deployment": settings.vision_deployment,
        "judge": settings.judge_deployment, "max_docs": settings.max_docs, "use_tools": use_tools,
        "retrieval": settings.retrieval_flags(), "principals": list(settings.principals),
        "started": datetime.now(timezone.utc).isoformat(), "currency": prices().currency,
    })

    buffer: list[str] = []
    if resume and store.exists(rows_key):
        buffer = [l for l in store.text(rows_key).splitlines() if l.strip()]
    for n, (q, arm, mode) in enumerate(todo, 1):
        items = evidence_items(q)
        t0 = time.perf_counter()
        try:
            a = ask_fn(q["question"], arm, mode)
            if not a.total_s:
                a.total_s = time.perf_counter() - t0
            row = _row(q, arm, mode, a, judge_fn(q, a.text), items)
        except Exception as e:  # noqa: BLE001 - keep going; the row records the failure
            log.warning("%s %s/%s failed: %s", q["id"], arm, mode, e)
            row = _error_row(q, arm, mode, e)
        buffer.append(json.dumps(row, ensure_ascii=False))
        store.put(rows_key, "\n".join(buffer) + "\n", "application/x-ndjson")  # written after every row
        if progress:
            progress(f"[{n}/{len(todo)}] {q['id']:9} {combo_key(arm, mode):22} evidence={row['evidence_hits']:5} "
                     f"{row['verdict']:9} {row['total_s']:5.1f}s {row['total_tokens']:>6} tok"
                     + (f"  vision x{row['images']}" if row["images"] else ""))
    rows = store.jsonl(rows_key) if store.exists(rows_key) else []
    summary = report_mod.summarise(rows, arms, modes)
    summary["run"] = run_name
    store.put_json(f"{key}/summary.json", summary)
    store.put(f"{key}/report.md", report_mod.markdown(summary, rows), "text/markdown")
    return key


def load_run(run_name: str, store: Store | None = None) -> tuple[dict, list[dict], dict]:
    """(summary, rows, config) of a stored run."""
    store = store or open_store()
    key = run_key(run_name)
    rows = store.jsonl(f"{key}/rows.jsonl") if store.exists(f"{key}/rows.jsonl") else []
    summary = store.json(f"{key}/summary.json", {}) if store.exists(f"{key}/summary.json") else {}
    config = store.json(f"{key}/config.json", {}) if store.exists(f"{key}/config.json") else {}
    if not summary and rows:
        summary = report_mod.summarise(rows, config.get("arms") or sorted({r["arm"] for r in rows}),
                                       config.get("modes") or sorted({r["mode"] for r in rows}))
    return summary, rows, config
