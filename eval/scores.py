"""Turn a rag_eval.jsonl into a scores file: one row per question, then a summary and a comparison of the solutions.

    python eval/scores.py reports/rag_eval.jsonl reports/rag_scores.md

Answer: 1 = correct, 0.5 = partial, 0 = incorrect (LLM judge). Retrieval: 1 = all golden evidence in the retrieved
chunks, 0.5 = some, 0 = none, `-` = no evidence defined. Times and tokens are included when the jsonl has them.
"""
from __future__ import annotations

import collections
import json
import statistics
import sys
from pathlib import Path

SOURCES = ("docx", "markdown")
ANSWER = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}


def retrieval_score(r: dict) -> float | None:
    hits, n = map(int, r["evidence_hits"].split("/"))
    return None if n == 0 else 1.0 if hits == n else 0.0 if hits == 0 else 0.5


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def pct(v):
    return "-" if v is None else f"{v:.0%}"


def num(v):
    return "-" if v is None else f"{v:g}"


def main(src: Path, dst: Path) -> None:
    rows = [json.loads(line) for line in src.open(encoding="utf-8")]
    sources = [s for s in SOURCES if any(r["source"] == s for r in rows)]
    qs: dict[str, dict] = collections.OrderedDict()
    for r in rows:
        q = qs.setdefault(r["id"], {"type": r["type"]})
        q[r["source"]] = {"ret": retrieval_score(r), "ans": ANSWER[r["verdict"]], "raw": r}
    qlist = list(qs.values())
    timed = all("total_s" in r for r in rows)

    out = ["# Scores per question", "",
           "Answer: 1 = correct, 0.5 = partial, 0 = incorrect (LLM judge). Retrieval: 1 = all golden evidence in the "
           "retrieved chunks, 0.5 = some of it, 0 = none, `-` = no evidence defined (unanswerable questions).", "",
           "| id | type | " + " | ".join(f"{s} retrieval | {s} answer" for s in sources) + " |",
           "|---|---|" + "---|---|" * len(sources)]
    for i, q in qs.items():
        out.append(f"| {i} | {q['type']} | " + " | ".join(f"{num(q[s]['ret'])} | {num(q[s]['ans'])}" for s in sources) + " |")

    out += ["", "# Summary", "", "## Per type (mean score)", "",
            "| type | n | " + " | ".join(f"{s} retrieval | {s} answer" for s in sources) + " |",
            "|---|---|" + "---|---|" * len(sources)]
    for t in sorted({q["type"] for q in qlist}) + ["ALL"]:
        sel = [q for q in qlist if t == "ALL" or q["type"] == t]
        name = f"**{t}**" if t == "ALL" else t
        out.append(f"| {name} | {len(sel)} | " + " | ".join(
            f"{pct(mean([q[s]['ret'] for q in sel]))} | {pct(mean([q[s]['ans'] for q in sel]))}" for s in sources) + " |")

    if len(sources) == 2:
        a, b = sources
        out += ["", "## Comparison", "", f"| | {a} | {b} | difference ({b} - {a}) |", "|---|---|---|---|"]
        for key, name in (("ret", "mean retrieval"), ("ans", "mean answer")):
            x, y = (mean([q[s][key] for q in qlist]) for s in sources)
            out.append(f"| {name} | {pct(x)} | {pct(y)} | {(y - x) * 100:+.1f} pp |")
        for label, v in (("answers correct (1)", 1.0), ("answers partial (0.5)", 0.5), ("answers incorrect (0)", 0.0)):
            x, y = (sum(q[s]["ans"] == v for q in qlist) for s in sources)
            out.append(f"| {label} | {x} | {y} | {y - x:+d} |")
        w = collections.Counter("first higher" if q[a]["ans"] > q[b]["ans"] else
                                "second higher" if q[b]["ans"] > q[a]["ans"] else "same" for q in qlist)
        out += ["", f"Answer score per question: {a} higher on {w['first higher']}, {b} higher on {w['second higher']}, "
                    f"same on {w['same']} of {len(qlist)}.", "", "## Questions where the solutions differ (answer score)", "",
                f"| id | type | {a} | {b} |", "|---|---|---|---|"]
        out += [f"| {i} | {q['type']} | {num(q[a]['ans'])} | {num(q[b]['ans'])} |" for i, q in qs.items()
                if q[a]["ans"] != q[b]["ans"]]

    if timed:
        out += ["", "## Time and tokens", "",
                "Seconds per question, excluding the judge. Retrieval = query embedding + vector search + BM25 fusion; "
                "generation = the answer model call; total = retrieval + context assembly + generation. "
                "Tokens are those of the answer call (query embedding and judge not counted).", "",
                "| solution | metric | mean | median | p95 | sum |", "|---|---|---|---|---|---|"]
        for s in sources:
            rs = [q[s]["raw"] for q in qlist]
            for key, label, unit in (("retrieval_s", "retrieval time", "s"), ("generation_s", "generation time", "s"),
                                     ("total_s", "total time", "s"), ("input_tokens", "input tokens", ""),
                                     ("output_tokens", "output tokens", ""), ("total_tokens", "total tokens", "")):
                xs = sorted(r[key] for r in rs)
                f = (lambda v: f"{v:.2f}") if unit else (lambda v: f"{v:,.0f}")
                out.append(f"| {s} | {label} | {f(sum(xs) / len(xs))} | {f(statistics.median(xs))} | "
                           f"{f(xs[min(len(xs) - 1, int(0.95 * len(xs)))])} | {f(sum(xs))} |")
        out += ["", "### Per question", "",
                "| id | type | " + " | ".join(f"{s} retr. s | {s} gen. s | {s} total s | {s} in tok | {s} out tok"
                                              for s in sources) + " |", "|---|---|" + "---|---|---|---|---|" * len(sources)]
        for i, q in qs.items():
            cells = []
            for s in sources:
                r = q[s]["raw"]
                cells += [f"{r['retrieval_s']:.2f}", f"{r['generation_s']:.2f}", f"{r['total_s']:.2f}",
                          f"{r['input_tokens']:,}", f"{r['output_tokens']:,}"]
            out.append(f"| {i} | {q['type']} | " + " | ".join(cells) + " |")

    dst.write_text("\n".join(out) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
