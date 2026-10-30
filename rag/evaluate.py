"""Run the golden questions through one or both solutions and write reports/rag_eval.md.

Two measures per question and solution:
- retrieval: are the golden evidence strings present in the retrieved chunks (same matching as eval/retention.py);
- answer: an LLM judge compares the answer with the expected one (correct / partial / incorrect).
"""
from __future__ import annotations

import importlib.util
import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Literal

import yaml
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from .azure_models import chat_model
from .config import ROOT, settings
from .pipeline import answer, retriever

log = logging.getLogger(__name__)

_spec = importlib.util.spec_from_file_location("retention", ROOT / "eval" / "retention.py")
retention = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(retention)

_jspec = importlib.util.spec_from_file_location("judge", ROOT / "eval" / "judge.py")
_judge = importlib.util.module_from_spec(_jspec)
_jspec.loader.exec_module(_judge)
JUDGE_SYSTEM = _judge.JUDGE_SYSTEM  # shared with `agentic eval`, so both approaches are graded the same way


class Verdict(BaseModel):
    verdict: Literal["correct", "partial", "incorrect"]
    reason: str


def judge(q: dict, got: str) -> Verdict:
    # JUDGE_DEPLOYMENT lets the judge be a different, stronger model than the one that wrote the answers
    model = chat_model(settings.judge_deployment, max_tokens=300).with_structured_output(Verdict)
    return model.invoke([SystemMessage(JUDGE_SYSTEM), HumanMessage(_judge.judge_user_text(q, got))])


def load_questions(ids: set[str] | None, types: set[str] | None, limit: int | None) -> list[dict]:
    qs = []
    for f in sorted((ROOT / "golden").glob("*.yaml")):
        qs.extend(yaml.safe_load(f.read_text(encoding="utf-8")) or [])
    qs = [q for q in qs if (not ids or q["id"] in ids) and (not types or q["type"] in types)]
    return qs[:limit] if limit else qs


def run(sources: list[str], ids: set[str] | None = None, types: set[str] | None = None,
        limit: int | None = None, out_dir: Path = ROOT / "reports") -> Path:
    questions = load_questions(ids, types, limit)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for source in sources:  # load Chroma and build BM25 now, so the first question's timing does not include it
        retriever(source)
    for q in questions:
        items = retention.evidence_items(q)
        for source in sources:
            a = answer(q["question"], source)
            ctx = retention.normalise("\n".join(d.page_content for d in a.sources))
            hits = retention.present(items, ctx)
            v = judge(q, a.answer)
            row = {"id": q["id"], "type": q["type"], "doc": q["doc"], "source": source,
                   "evidence_retrieved": all(hits) if items else None,
                   "evidence_hits": f"{sum(hits)}/{len(hits)}", "verdict": v.verdict, "reason": v.reason,
                   "answer": a.answer, "expected": q.get("answer", ""),
                   "retrieved": [f"{d.metadata.get('doc_id')}#{d.metadata.get('chunk')}" for d in a.sources],
                   "images": len(a.images), **a.timings, **a.usage}
            rows.append(row)
            log.info("%s %-8s retrieved=%s verdict=%s", q["id"], source, row["evidence_hits"], v.verdict)

    with open(out_dir / "rag_eval.jsonl", "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    path = out_dir / "rag_eval.md"
    path.write_text(_report(rows, sources), encoding="utf-8")
    return path


def _cost_section(rows: list[dict], sources: list[str]) -> list[str]:
    """Time and token summary. Time is per question, excluding the judge; tokens are those of the answer call
    (the query embedding and the judge are not counted)."""
    if not any("total_s" in r for r in rows):
        return []

    def stats(xs: list[float]) -> tuple[float, float, float]:
        xs = sorted(xs)
        return sum(xs) / len(xs), xs[len(xs) // 2], xs[min(len(xs) - 1, int(0.95 * len(xs)))]

    out = ["", "## Time and tokens", "",
           "Seconds per question (mean / median / p95). Retrieval = query embedding + vector search + BM25 fusion; "
           "generation = the answer model call; total = retrieval + context assembly + generation.", "",
           "| solution | n | retrieval s | generation s | total s | input tokens (mean) | output tokens (mean) "
           "| total tokens (sum) |", "|---|---|---|---|---|---|---|---|"]
    for s in sources:
        rs = [r for r in rows if r["source"] == s and "total_s" in r]
        if not rs:
            continue
        cells = [" / ".join(f"{v:.2f}" for v in stats([r[k] for r in rs]))
                 for k in ("retrieval_s", "generation_s", "total_s")]
        out.append(f"| {s} | {len(rs)} | " + " | ".join(cells) +
                   f" | {sum(r['input_tokens'] for r in rs) / len(rs):,.0f}"
                   f" | {sum(r['output_tokens'] for r in rs) / len(rs):,.0f}"
                   f" | {sum(r['total_tokens'] for r in rs):,} |")
    return out


def _report(rows: list[dict], sources: list[str]) -> str:
    score = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}
    by = defaultdict(list)
    for r in rows:
        by[(r["type"], r["source"])].append(r)
        by[("ALL", r["source"])].append(r)
    types = sorted({t for t, _ in by if t != "ALL"}) + ["ALL"]
    out = ["# Multimodal RAG evaluation", "",
           "Answer score: correct = 1, partial = 0.5 (LLM judge). Retrieval: share of questions whose golden "
           "evidence is all present in the retrieved chunks.", "",
           "| type | n | " + " | ".join(f"{s} answer | {s} retrieval" for s in sources) + " |",
           "|---|---|" + "---|---|" * len(sources)]
    for t in types:
        cells = []
        n = 0
        for s in sources:
            rs = by.get((t, s), [])
            n = max(n, len(rs))
            ans = sum(score[r["verdict"]] for r in rs) / len(rs) if rs else 0
            ret = [r["evidence_retrieved"] for r in rs if r["evidence_retrieved"] is not None]
            cells += [f"{ans:.0%}", f"{sum(ret) / len(ret):.0%}" if ret else "-"]
        out.append(f"| {'**ALL**' if t == 'ALL' else t} | {n} | " + " | ".join(cells) + " |")
    out += _cost_section(rows, sources)
    out += ["", "## Per question", "", "| id | type | " + " | ".join(sources) + " |",
            "|---|---|" + "---|" * len(sources)]
    per = defaultdict(dict)
    for r in rows:
        per[(r["id"], r["type"])][r["source"]] = f"{r['verdict']} ({r['evidence_hits']})"
    for (qid, t), v in per.items():
        out.append(f"| {qid} | {t} | " + " | ".join(v.get(s, "") for s in sources) + " |")
    out += ["", "Full answers and judge reasons: `reports/rag_eval.jsonl`.", ""]
    return "\n".join(out)
