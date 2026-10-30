"""Turn one or more eval jsonl files into a scores file: one row per question, then a summary and a comparison.

    python eval/scores.py reports/rag_eval.jsonl reports/rag_scores.md
    python eval/scores.py reports/rag_eval.jsonl reports/agentic_eval.jsonl reports/four_way.md

A row's column is its `source` (docx, markdown), prefixed with its `approach` when the row has one (agentic-docx);
`rag eval` rows have no approach, so a merged file reads docx | markdown | agentic-docx | agentic-markdown.

Answer: 1 = correct, 0.5 = partial, 0 = incorrect (LLM judge). Retrieval: 1 = all golden evidence in the retrieved
chunks, 0.5 = some, 0 = none, `-` = no evidence defined. Times and tokens are included when the jsonl has them.
"""
from __future__ import annotations

import collections
import json
import statistics
import sys
from pathlib import Path

SOURCES = ("docx", "markdown")  # column order; approach-prefixed labels follow in this order too
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


def label(r: dict) -> str:
    return f"{r['approach']}-{r['source']}" if r.get("approach") else r["source"]


def main(srcs: list[Path] | Path, dst: Path) -> None:
    rows = [json.loads(line) for src in ([srcs] if isinstance(srcs, Path) else srcs)
            for line in src.open(encoding="utf-8") if line.strip()]
    labels = {label(r) for r in rows}
    sources = sorted(labels, key=lambda l: (l.count("-"), SOURCES.index(l.split("-")[-1])
                                            if l.split("-")[-1] in SOURCES else 9, l))
    qs: dict[str, dict] = collections.OrderedDict()
    for r in rows:
        q = qs.setdefault(r["id"], {"type": r["type"]})
        q[label(r)] = {"ret": retrieval_score(r), "ans": ANSWER[r["verdict"]], "raw": r}
    missing = {"ret": None, "ans": None, "raw": {}}
    for q in qs.values():  # a question missing from one file scores nothing there, instead of crashing the table
        for s in sources:
            q.setdefault(s, missing)
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
        for what, v in (("answers correct (1)", 1.0), ("answers partial (0.5)", 0.5), ("answers incorrect (0)", 0.0)):
            x, y = (sum(q[s]["ans"] == v for q in qlist) for s in sources)
            out.append(f"| {what} | {x} | {y} | {y - x:+d} |")
        both = [q for q in qlist if q[a]["ans"] is not None and q[b]["ans"] is not None]
        w = collections.Counter("first higher" if q[a]["ans"] > q[b]["ans"] else
                                "second higher" if q[b]["ans"] > q[a]["ans"] else "same" for q in both)
        out += ["", f"Answer score per question: {a} higher on {w['first higher']}, {b} higher on {w['second higher']}, "
                    f"same on {w['same']} of {len(both)}.", "", "## Questions where the solutions differ (answer score)", "",
                f"| id | type | {a} | {b} |", "|---|---|---|---|"]
        out += [f"| {i} | {q['type']} | {num(q[a]['ans'])} | {num(q[b]['ans'])} |" for i, q in qs.items()
                if q[a]["ans"] != q[b]["ans"] and None not in (q[a]["ans"], q[b]["ans"])]

    if timed:
        out += ["", "## Time and tokens", "",
                "Seconds per question, excluding the judge. RAG rows: retrieval = query embedding + vector search + "
                "BM25 fusion, generation = the answer model call. Agentic rows: retrieval = the agent rounds that "
                "called the knowledge base (the agent usually writes its answer in that same round, so generation "
                "shows 0 unless a picture round followed), vision = inspect_figure calls, generation = other rounds. "
                "Total covers the whole question. Tokens are those of the answer model (judge not counted).", "",
                "| solution | metric | mean | median | p95 | sum |", "|---|---|---|---|---|---|"]
        for s in sources:
            rs = [q[s]["raw"] for q in qlist if q[s]["raw"]]
            for key, name, unit in (("retrieval_s", "retrieval time", "s"), ("generation_s", "generation time", "s"),
                                    ("vision_s", "vision time", "s"), ("total_s", "total time", "s"),
                                    ("input_tokens", "input tokens", ""), ("output_tokens", "output tokens", ""),
                                    ("reasoning_tokens", "reasoning tokens", ""), ("vision_tokens", "vision tokens", ""),
                                    ("total_tokens", "total tokens", "")):
                xs = sorted(r[key] for r in rs if key in r)
                if not xs:
                    continue
                f = (lambda v: f"{v:.2f}") if unit else (lambda v: f"{v:,.0f}")
                out.append(f"| {s} | {name} | {f(sum(xs) / len(xs))} | {f(statistics.median(xs))} | "
                           f"{f(xs[min(len(xs) - 1, int(0.95 * len(xs)))])} | {f(sum(xs))} |")
        out += ["", "### Per question", "",
                "| id | type | " + " | ".join(f"{s} retr. s | {s} gen. s | {s} total s | {s} in tok | {s} out tok"
                                              for s in sources) + " |", "|---|---|" + "---|---|---|---|---|" * len(sources)]
        for i, q in qs.items():
            cells = []
            for s in sources:
                r = q[s]["raw"]
                cells += ([f"{r['retrieval_s']:.2f}", f"{r['generation_s']:.2f}", f"{r['total_s']:.2f}",
                           f"{r['input_tokens']:,}", f"{r['output_tokens']:,}"] if r else ["-"] * 5)
            out.append(f"| {i} | {q['type']} | " + " | ".join(cells) + " |")

    dst.write_text("\n".join(out) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main([Path(p) for p in sys.argv[1:-1]], Path(sys.argv[-1]))
