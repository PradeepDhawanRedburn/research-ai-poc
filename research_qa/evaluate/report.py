"""Summaries and the Markdown report of an evaluation run. Pure functions over the rows, used by run.py and the app."""
from __future__ import annotations

import statistics
import math
from collections import defaultdict

from ..config import ARMS, combo_key, combo_label
from .judge import SCORE

TIME_KEYS = [("retrieval_s", "retrieval s"), ("generation_s", "generation s"), ("tools_s", "tools s"),
             ("total_s", "total s")]
TOKEN_KEYS = [("input_tokens", "input tokens"), ("output_tokens", "output tokens"),
              ("reasoning_tokens", "reasoning tokens"), ("vision_tokens", "vision tokens"),
              ("total_tokens", "total tokens")]


def wilson_interval(successes: float, n: int, z: float = 1.959963984540054) -> tuple[float, float] | None:
    if n <= 0:
        return None
    p = max(0.0, min(1.0, successes / n))
    z2 = z * z
    denominator = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denominator
    margin = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def _exact_mcnemar(left_wins: int, right_wins: int) -> float:
    discordant = left_wins + right_wins
    if not discordant:
        return 1.0
    tail = sum(math.comb(discordant, i) for i in range(min(left_wins, right_wins) + 1)) / (2 ** discordant)
    return min(1.0, 2 * tail)


def paired_comparison(rows: list[dict], left: str, right: str) -> dict:
    by_id = defaultdict(dict)
    for row in rows:
        by_id[row["id"]][row.get("combo") or combo_key(row["arm"], row["mode"])] = row["verdict"]
    paired = [(v[left], v[right]) for v in by_id.values() if left in v and right in v]
    result = {"left": left, "right": right, "n": len(paired)}
    for label, fn in (("strict", lambda v: v == "correct"),
                      ("lenient", lambda v: v in ("correct", "partial"))):
        wins_left = sum(fn(a) and not fn(b) for a, b in paired)
        wins_right = sum(fn(b) and not fn(a) for a, b in paired)
        p = _exact_mcnemar(wins_left, wins_right)
        result[label] = {"left_wins": wins_left, "right_wins": wins_right, "p": p,
                         "significance": "not significant" if p >= 0.05 else "significant"}
    return result


def _stats(xs: list[float]) -> dict:
    if not xs:
        return {"mean": 0, "median": 0, "p95": 0, "sum": 0, "n": 0}
    xs = sorted(xs)
    return {"mean": sum(xs) / len(xs), "median": statistics.median(xs), "p95": xs[min(len(xs) - 1, int(0.95 * len(xs)))],
            "sum": sum(xs), "n": len(xs)}


def combo_summary(rs: list[dict]) -> dict:
    """Scores, time and tokens for one set of rows (one combo, or one combo × type)."""
    ev = [r["evidence_retrieved"] for r in rs if r.get("evidence_retrieved") is not None]
    correct = sum(r["verdict"] == "correct" for r in rs)
    partial = sum(r["verdict"] == "partial" for r in rs)
    weighted = correct + 0.5 * partial
    evidence_successes = sum(ev)
    return {
        "n": len(rs),
        "accuracy": weighted / len(rs) if rs else None,
        "accuracy_ci": wilson_interval(weighted, len(rs)),
        "correct": correct,
        "partial": partial,
        "incorrect": sum(r["verdict"] == "incorrect" for r in rs),
        "failed": sum(1 for r in rs if r.get("errors") and not r.get("answer")),
        "evidence_found": evidence_successes / len(ev) if ev else None,
        "evidence_ci": wilson_interval(evidence_successes, len(ev)),
        "evidence_n": len(ev),
        "time": {k: _stats([r.get(k, 0) or 0 for r in rs]) for k, _ in TIME_KEYS},
        "tokens": {k: _stats([r.get(k, 0) or 0 for r in rs]) for k, _ in TOKEN_KEYS},
        "images": sum(r.get("images", 0) or 0 for r in rs),
        "tool_calls": sum(len(r.get("tools") or []) for r in rs),
    }


def summarise(rows: list[dict], arms: list[str], modes: list[str]) -> dict:
    combos = [combo_key(a, m) for a in arms for m in modes]
    by_combo = defaultdict(list)
    by_source = defaultdict(lambda: defaultdict(list))
    for r in rows:
        combo = r.get("combo") or combo_key(r["arm"], r["mode"])
        by_combo[combo].append(r)
        by_source[combo][r.get("source_of_truth", "extractor_output")].append(r)
    types = sorted({r["type"] for r in rows})
    out = {"arms": arms, "modes": modes, "combos": combos, "types": types, "questions": len({r["id"] for r in rows}),
               "per_combo": {}, "per_type": {}, "per_source_of_truth": {}, "disagreements": [], "paired": []}
    for c in combos:
        rs = by_combo.get(c, [])
        out["per_combo"][c] = {"label": combo_label(*c.split("/")), **combo_summary(rs)}
        out["per_type"][c] = {t: combo_summary([r for r in rs if r["type"] == t]) for t in types}
        out["per_source_of_truth"][c] = {source: combo_summary(source_rows)
                         for source, source_rows in by_source.get(c, {}).items()}
    # questions where the combos do not agree on the verdict
    per_q = defaultdict(dict)
    for r in rows:
        per_q[(r["id"], r["type"])][r.get("combo") or combo_key(r["arm"], r["mode"])] = r["verdict"]
    for (qid, t), v in per_q.items():
        if len({v.get(c) for c in combos if c in v}) > 1:
            out["disagreements"].append({"id": qid, "type": t, **{c: v.get(c, "") for c in combos}})
    out["paired"] = [paired_comparison(rows, left, right) for i, left in enumerate(combos)
                     for right in combos[i + 1:]]
    return out


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.0%}"


def _ci(interval: tuple[float, float] | None) -> str:
    return "-" if interval is None else f"{interval[0]:.0%}–{interval[1]:.0%}"


def markdown(summary: dict, rows: list[dict]) -> str:
    combos = summary["combos"]
    labels = {c: summary["per_combo"][c]["label"] for c in combos}
    out = ["# Evaluation run " + str(summary.get("run", "")), "",
           f"{summary['questions']} questions. Accuracy: correct = 1, partial = 0.5 (one LLM judge for every arm and "
           "mode). Evidence found: share of questions whose golden evidence strings are all present in the passages "
           "the system retrieved.", "",
           "## Headline", "",
           "| arm · mode | questions | accuracy (95% CI) | evidence found (95% CI) | total s (mean) | total tokens (mean) |",
           "|---|---:|---|---|---:|---:|"]
    for c in combos:
        s = summary["per_combo"][c]
        out.append(f"| {labels[c]} | {s['n']} | {_pct(s['accuracy'])} ({_ci(s['accuracy_ci'])}) | "
               f"{_pct(s['evidence_found'])} ({_ci(s['evidence_ci'])}) | "
                   f"{s['time']['total_s']['mean']:.1f} | {s['tokens']['total_tokens']['mean']:,.0f} |")
    out += ["", "## By question type", "",
            "| type | n | " + " | ".join(f"{labels[c]} accuracy (95% CI) | {labels[c]} evidence (95% CI)"
                                             for c in combos) + " |",
            "|---|---|" + "---|---|" * len(combos)]
    for t in summary["types"]:
        n = max((summary["per_type"][c][t]["n"] for c in combos), default=0)
        cells = []
        for c in combos:
            s = summary["per_type"][c][t]
            cells += [f"{_pct(s['accuracy'])} ({_ci(s['accuracy_ci'])})",
                      f"{_pct(s['evidence_found'])} ({_ci(s['evidence_ci'])})"]
        out.append(f"| {t} | {n} | " + " | ".join(cells) + " |")
    sources = sorted({source for combo in summary.get("per_source_of_truth", {}).values() for source in combo})
    if sources:
        out += ["", "## Answer accuracy by source of truth", "",
                "| source_of_truth | " + " | ".join(labels[c] for c in combos) + " |",
                "|---|" + "---|" * len(combos)]
        for source in sources:
            cells = []
            for combo in combos:
                score = summary.get("per_source_of_truth", {}).get(combo, {}).get(source)
                cells.append("-" if not score else f"{_pct(score['accuracy'])} ({_ci(score['accuracy_ci'])}), n={score['n']}")
            out.append(f"| {source} | " + " | ".join(cells) + " |")
    out += ["", "## Time and tokens", "",
            "Seconds per question, excluding the judge. Retrieval = knowledge-base calls (an agent usually writes its "
            "answer in the same round, so generation can be 0); tools = facts lookups and picture inspections. "
            "Tokens are everything the question used: answer model plus vision calls.", "",
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
    if summary.get("paired"):
        out += ["", "## Paired McNemar comparisons", "",
                "Exact binomial test on discordant paired questions; strict counts only correct as success, lenient "
                "counts correct or partial. Differences with p ≥ 0.05 are not significant.", "",
                "| comparison | n | strict wins (left/right) | strict p | strict result | lenient wins (left/right) | lenient p | lenient result |",
                "|---|---:|---:|---:|---|---:|---:|---|"]
        for pair in summary["paired"]:
            strict, lenient = pair["strict"], pair["lenient"]
            out.append(f"| {labels.get(pair['left'], pair['left'])} vs {labels.get(pair['right'], pair['right'])} | "
                       f"{pair['n']} | {strict['left_wins']}/{strict['right_wins']} | {strict['p']:.4f} | "
                       f"{strict['significance']} | {lenient['left_wins']}/{lenient['right_wins']} | "
                       f"{lenient['p']:.4f} | {lenient['significance']} |")
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
    out += ["", "Full answers and judge reasons: rows.jsonl next to this file.", ""]
    return "\n".join(out)


def headline_rows(summary: dict) -> list[dict]:
    """Flat rows for a table in the app."""
    rows = []
    for c in summary.get("combos", []):
        s = summary["per_combo"][c]
        rows.append({"arm · mode": s["label"], "questions": s["n"], "accuracy": _pct(s["accuracy"]),
                     "accuracy 95% CI": _ci(s.get("accuracy_ci")),
                     "evidence found": _pct(s["evidence_found"]),
                     "evidence 95% CI": _ci(s.get("evidence_ci")),
                     "seconds per question": round(s["time"]["total_s"]["mean"], 1),
                     "tokens per question": round(s["tokens"]["total_tokens"]["mean"]),
                     "pictures inspected": s["images"], "tool calls": s["tool_calls"], "failed": s["failed"]})
    return rows


def arm_of(combo: str) -> str:
    return ARMS[combo.split("/")[0]].label
