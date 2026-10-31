"""Recall@k and latency per question type from an evaluation run's rows, with no model call.

A run's rows keep the retrieved passage ids in rank order. Recall@k asks whether every golden evidence string of a
question is present in the text of the first k passages. The texts are read back from the store: Markdown+ chunk
ids resolve to chunks/<doc>.jsonl; the LangChain Word arm's ids resolve to the stock conversion the engine builds;
Azure's own Word chunks have opaque ids and cannot be resolved here, so those rows fall back to the run's
"evidence found" flag (measured at the run's passage limit) and the report says how many did.

This is the before-and-after measure for every ingestion or retrieval change: same rows, same questions.
"""
from __future__ import annotations

import statistics
from collections import defaultdict

from ..config import ARMS, combo_key
from ..schema import Chunk
from ..storage import CHUNKS, Store
from .golden import load_questions
from .retention import evidence_items, normalise, present

TYPE_LABEL = {"text_fact": "text", "table_lookup": "table", "chart_native": "chart (native)",
              "chart_picture": "chart (picture)", "symbol_matrix": "matrix", "image": "image",
              "relationship": "figure and source", "cross_document": "two editions", "unanswerable": "unanswerable"}


def _p(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


class TextIndex:
    """Lazy id -> text over the store, per arm; resolves what it can."""

    def __init__(self, store: Store):
        self.store = store
        self._cache: dict[tuple[str, str], dict[str, str]] = {}

    def texts(self, arm: str, doc_id: str) -> dict[str, str]:
        key = (arm, doc_id)
        if key in self._cache:
            return self._cache[key]
        out: dict[str, str] = {}
        try:
            if ARMS[arm].has_tools:
                k = f"{CHUNKS}/{doc_id}.jsonl"
                if self.store.exists(k):
                    out = {d["id"]: d["text"] for d in self.store.jsonl(k)}
            else:
                from ..lc.word_source import word_chunks

                out = {c.id: c.text for c in word_chunks(doc_id, self.store)}
        except Exception:  # noqa: BLE001 - unresolved ids are reported, not fatal
            out = {}
        self._cache[key] = out
        return out

    def resolve(self, arm: str, ids: list[str], docs: list[str]) -> tuple[list[str], int]:
        """(texts in rank order, number of ids that could not be resolved)."""
        found, missing = [], 0
        pool: dict[str, str] = {}
        for d in docs:
            pool.update(self.texts(arm, d))
        for i in ids:
            if i in pool:
                found.append(pool[i])
                continue
            doc = i.split("-")[0]
            t = self.texts(arm, doc).get(i) if doc else None
            if t is None:
                missing += 1
            else:
                found.append(t)
        return found, missing


def recall_rows(rows: list[dict], store: Store, k: int = 10, questions: list[dict] | None = None) -> list[dict]:
    """One record per row: recall at k (1.0 / 0.0 / None when the question has no evidence) and whether it was
    computed from resolved texts or taken from the run's evidence flag."""
    qs = {q["id"]: q for q in (questions or load_questions())}
    idx = TextIndex(store)
    out = []
    for r in rows:
        q = qs.get(r["id"])
        items = evidence_items(q) if q else []
        rec = {"id": r["id"], "type": r["type"], "arm": r["arm"], "mode": r["mode"],
               "combo": r.get("combo") or combo_key(r["arm"], r["mode"]), "total_s": float(r.get("total_s") or 0),
               "first_token_s": float(r.get("first_token_s") or 0),
               "total_tokens": int(r.get("total_tokens") or 0), "recall": None, "resolved": True}
        if items:
            ids = list(r.get("retrieved") or [])[:k]
            docs = (q.get("docs") or [q.get("doc")]) if q else []
            texts, missing = idx.resolve(r["arm"], ids, [d for d in docs if d])
            if ids and missing == 0:
                rec["recall"] = 1.0 if all(present(items, normalise("\n".join(texts)))) else 0.0
            else:
                rec["recall"] = 1.0 if r.get("evidence_retrieved") else 0.0
                rec["resolved"] = False
        out.append(rec)
    return out


def summarise(recs: list[dict]) -> dict:
    """{combo: {type: {n, recall, p50_s, p95_s, mean_tokens, unresolved}}} plus an 'all' type per combo."""
    by: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in recs:
        by[r["combo"]][r["type"]].append(r)
        by[r["combo"]]["all"].append(r)
    out: dict[str, dict[str, dict]] = {}
    for combo, types in by.items():
        out[combo] = {}
        for t, rs in types.items():
            scored = [r for r in rs if r["recall"] is not None]
            out[combo][t] = {
                "n": len(rs),
                "recall": (sum(r["recall"] for r in scored) / len(scored)) if scored else None,
                "scored": len(scored),
                "unresolved": sum(1 for r in scored if not r["resolved"]),
                "p50_s": _p([r["total_s"] for r in rs], 0.5),
                "p95_s": _p([r["total_s"] for r in rs], 0.95),
                "p95_first_token_s": _p([r["first_token_s"] for r in rs if r["first_token_s"]], 0.95),
                "mean_s": statistics.mean([r["total_s"] for r in rs]) if rs else 0.0,
                "mean_tokens": statistics.mean([r["total_tokens"] for r in rs]) if rs else 0.0,
            }
    return out


def markdown(run_name: str, summary: dict, k: int, config: dict | None = None) -> str:
    config = config or {}
    combos = list(summary)
    types = sorted({t for c in combos for t in summary[c] if t != "all"})
    pct = lambda x: "-" if x is None else f"{x:.0%}"  # noqa: E731
    out = [f"# Baseline: recall@{k} and latency by question type", "",
           f"Run `{run_name}` · model {config.get('answer_model', '')} · passages per retrieval "
           f"{config.get('max_docs', '')}. Recall@{k}: every golden evidence string of the question is in the text "
           f"of the first {k} retrieved passages. Latency is the whole question, in seconds.", "",
           "## Recall@%d" % k, "",
           "| type | n | " + " | ".join(combos) + " |", "|---|---|" + "---|" * len(combos)]
    for t in types + ["all"]:
        n = max((summary[c].get(t, {}).get("n", 0) for c in combos), default=0)
        cells = [pct(summary[c].get(t, {}).get("recall")) for c in combos]
        out.append(f"| {TYPE_LABEL.get(t, t)} | {n} | " + " | ".join(cells) + " |")
    streamed = any(summary[c]["all"].get("p95_first_token_s") for c in combos)
    out += ["", "## Latency (p95 seconds; p50 in brackets" + ("; p95 to first token after the slash)" if streamed else ")"),
            "", "| type | " + " | ".join(combos) + " |", "|---|" + "---|" * len(combos)]
    for t in types + ["all"]:
        cells = []
        for c in combos:
            s = summary[c].get(t)
            if not s:
                cells.append("-")
                continue
            cell = f"{s['p95_s']:.1f} ({s['p50_s']:.1f})"
            if streamed and s.get("p95_first_token_s"):
                cell += f" / {s['p95_first_token_s']:.1f}"
            cells.append(cell)
        out.append(f"| {TYPE_LABEL.get(t, t)} | " + " | ".join(cells) + " |")
    out += ["", "## Tokens per question (mean)", "", "| type | " + " | ".join(combos) + " |",
            "|---|" + "---|" * len(combos)]
    for t in types + ["all"]:
        cells = [f"{summary[c][t]['mean_tokens']:,.0f}" if t in summary[c] else "-" for c in combos]
        out.append(f"| {TYPE_LABEL.get(t, t)} | " + " | ".join(cells) + " |")
    unresolved = {c: summary[c]["all"]["unresolved"] for c in combos if summary[c]["all"]["unresolved"]}
    if unresolved:
        out += ["", "Rows whose passage ids could not be resolved to text (recall taken from the run's evidence flag "
                "at its passage limit instead): " + ", ".join(f"{c}: {n}" for c, n in unresolved.items()) + "."]
    out.append("")
    return "\n".join(out)


def report(run_name: str, store: Store, k: int = 10) -> tuple[str, dict]:
    """Compute, write eval/<run>/baseline-k<k>.md to the store, return (markdown, summary)."""
    from .run import load_run, run_key

    _summary, rows, config = load_run(run_name, store)
    recs = recall_rows(rows, store, k)
    summary = summarise(recs)
    md = markdown(run_name, summary, k, config)
    store.put(f"{run_key(run_name)}/baseline-k{k}.md", md, "text/markdown")
    return md, summary
