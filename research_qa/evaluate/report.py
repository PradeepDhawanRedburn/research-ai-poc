"""Summaries and the Markdown report of an evaluation run. Pure functions over the rows, used by run.py and the app."""
from __future__ import annotations

import statistics
from collections import defaultdict

from ..config import ARMS, combo_key, combo_label
from .cost import fmt_money, prices
from .judge import SCORE

TIME_KEYS = [("retrieval_s", "retrieval s"), ("generation_s", "generation s"), ("tools_s", "tools s"),
             ("total_s", "total s")]
TOKEN_KEYS = [("input_tokens", "input tokens"), ("output_tokens", "output tokens"),
              ("reasoning_tokens", "reasoning tokens"), ("vision_tokens", "vision tokens"),
              ("total_tokens", "total tokens")]


def _stats(xs: list[float]) -> dict:
    if not xs:
        return {"mean": 0, "median": 0, "p95": 0, "sum": 0, "n": 0}
    xs = sorted(xs)
    return {"mean": sum(xs) / len(xs), "median": statistics.median(xs), "p95": xs[min(len(xs) - 1, int(0.95 * len(xs)))],
            "sum": sum(xs), "n": len(xs)}


def combo_summary(rs: list[dict]) -> dict:
    """Scores, time, tokens and cost for one set of rows (one combo, or one combo × type)."""
    ev = [r["evidence_retrieved"] for r in rs if r.get("evidence_retrieved") is not None]
    costs = [r["cost"] for r in rs if r.get("cost") is not None]
    return {
        "n": len(rs),
        "accuracy": sum(SCORE.get(r["verdict"], 0) for r in rs) / len(rs) if rs else None,
        "correct": sum(r["verdict"] == "correct" for r in rs),
        "partial": sum(r["verdict"] == "partial" for r in rs),
        "incorrect": sum(r["verdict"] == "incorrect" for r in rs),
        "failed": sum(1 for r in rs if r.get("errors") and not r.get("answer")),
        "evidence_found": sum(ev) / len(ev) if ev else None,
        "evidence_n": len(ev),
        "time": {k: _stats([r.get(k, 0) or 0 for r in rs]) for k, _ in TIME_KEYS},
        "tokens": {k: _stats([r.get(k, 0) or 0 for r in rs]) for k, _ in TOKEN_KEYS},
        "images": sum(r.get("images", 0) or 0 for r in rs),
        "tool_calls": sum(len(r.get("tools") or []) for r in rs),
        "cost_per_question": sum(costs) / len(costs) if costs and len(costs) == len(rs) else None,
        "cost_priced_rows": len(costs),
        "cost_total": sum(costs) if costs else None,
    }


def summarise(rows: list[dict], arms: list[str], modes: list[str]) -> dict:
    combos = [combo_key(a, m) for a in arms for m in modes]
    by_combo = defaultdict(list)
    for r in rows:
        by_combo[r.get("combo") or combo_key(r["arm"], r["mode"])].append(r)
    types = sorted({r["type"] for r in rows})
    out = {"arms": arms, "modes": modes, "combos": combos, "types": types, "questions": len({r["id"] for r in rows}),
           "currency": prices().currency, "per_combo": {}, "per_type": {}, "disagreements": []}
    for c in combos:
        rs = by_combo.get(c, [])
        out["per_combo"][c] = {"label": combo_label(*c.split("/")), **combo_summary(rs)}
        out["per_type"][c] = {t: combo_summary([r for r in rs if r["type"] == t]) for t in types}
    # questions where the combos do not agree on the verdict
    per_q = defaultdict(dict)
    for r in rows:
        per_q[(r["id"], r["type"])][r.get("combo") or combo_key(r["arm"], r["mode"])] = r["verdict"]
    for (qid, t), v in per_q.items():
        if len({v.get(c) for c in combos if c in v}) > 1:
            out["disagreements"].append({"id": qid, "type": t, **{c: v.get(c, "") for c in combos}})
    return out


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.0%}"


def markdown(summary: dict, rows: list[dict]) -> str:
    combos = summary["combos"]
    labels = {c: summary["per_combo"][c]["label"] for c in combos}
    cur = summary.get("currency")
    out = ["# Evaluation run " + str(summary.get("run", "")), "",
           f"{summary['questions']} questions. Accuracy: correct = 1, partial = 0.5 (one LLM judge for every arm and "
           "mode). Evidence found: share of questions whose golden evidence strings are all present in the passages "
           "the system retrieved. Cost from prices.yaml; 'not priced' when a deployment has no price.", "",
           "## Headline", "",
           "| arm · mode | questions | accuracy | evidence found | total s (mean) | total tokens (mean) | "
           f"cost per question ({cur}) | per 100 questions |", "|---|---|---|---|---|---|---|---|"]
    for c in combos:
        s = summary["per_combo"][c]
        cpq = s["cost_per_question"]
        out.append(f"| {labels[c]} | {s['n']} | {_pct(s['accuracy'])} | {_pct(s['evidence_found'])} | "
                   f"{s['time']['total_s']['mean']:.1f} | {s['tokens']['total_tokens']['mean']:,.0f} | "
                   f"{fmt_money(cpq, cur)} | {fmt_money(cpq * 100 if cpq is not None else None, cur)} |")
    out += ["", "## By question type", "",
            "| type | n | " + " | ".join(f"{labels[c]} accuracy | {labels[c]} evidence" for c in combos) + " |",
            "|---|---|" + "---|---|" * len(combos)]
    for t in summary["types"]:
        n = max((summary["per_type"][c][t]["n"] for c in combos), default=0)
        cells = []
        for c in combos:
            s = summary["per_type"][c][t]
            cells += [_pct(s["accuracy"]), _pct(s["evidence_found"])]
        out.append(f"| {t} | {n} | " + " | ".join(cells) + " |")
    out += ["", "## Time and tokens", "",
            "Seconds per question, excluding the judge. Retrieval = knowledge-base calls (an agent usually writes its "
            "answer in the same round, so generation can be 0); tools = facts lookups and picture inspections. "
            "Tokens are everything the question cost: answer model plus vision calls.", "",
            "| arm · mode | metric | mean | median | p95 | sum |", "|---|---|---|---|---|---|"]
    for c in combos:
        s = summary["per_combo"][c]
        for key, name in TIME_KEYS:
            st = s["time"][key]
            out.append(f"| {labels[c]} | {name} | {st['mean']:.2f} | {st['median']:.2f} | {st['p95']:.2f} | {st['sum']:.1f} |")
        for key, name in TOKEN_KEYS:
            st = s["tokens"][key]
            out.append(f"| {labels[c]} | {name} | {st['mean']:,.0f} | {st['median']:,.0f} | {st['p95']:,.0f} | {st['sum']:,.0f} |")
        out.append(f"| {labels[c]} | pictures inspected | | | | {s['images']} |")
        out.append(f"| {labels[c]} | tool calls | | | | {s['tool_calls']} |")
    if summary["disagreements"]:
        out += ["", "## Where the arms disagree", "",
                "| id | type | " + " | ".join(labels[c] for c in combos) + " |", "|---|---|" + "---|" * len(combos)]
        for d in summary["disagreements"]:
            out.append(f"| {d['id']} | {d['type']} | " + " | ".join(d.get(c, "") for c in combos) + " |")
    out += ["", "## Per question", "",
            "| id | type | " + " | ".join(labels[c] for c in combos) + " |", "|---|---|" + "---|" * len(combos)]
    per = defaultdict(dict)
    for r in rows:
        per[(r["id"], r["type"])][r.get("combo") or combo_key(r["arm"], r["mode"])] = (
            f"{r['verdict']} ({r['evidence_hits']}, {r.get('total_tokens', 0):,} tok)")
    for (qid, t), v in per.items():
        out.append(f"| {qid} | {t} | " + " | ".join(v.get(c, "") for c in combos) + " |")
    unpriced = prices().unpriced([r.get("model", "") for r in rows])
    if unpriced:
        out += ["", f"Not priced: {', '.join(unpriced)}. Add them to prices.yaml to see cost."]
    out += ["", "Full answers and judge reasons: rows.jsonl next to this file.", ""]
    return "\n".join(out)


def headline_rows(summary: dict) -> list[dict]:
    """Flat rows for a table in the app."""
    cur = summary.get("currency")
    rows = []
    for c in summary.get("combos", []):
        s = summary["per_combo"][c]
        rows.append({"arm · mode": s["label"], "questions": s["n"], "accuracy": _pct(s["accuracy"]),
                     "evidence found": _pct(s["evidence_found"]),
                     "seconds per question": round(s["time"]["total_s"]["mean"], 1),
                     "tokens per question": round(s["tokens"]["total_tokens"]["mean"]),
                     f"cost per question ({cur})": fmt_money(s["cost_per_question"], cur),
                     "pictures inspected": s["images"], "tool calls": s["tool_calls"], "failed": s["failed"]})
    return rows


def arm_of(combo: str) -> str:
    return ARMS[combo.split("/")[0]].label
