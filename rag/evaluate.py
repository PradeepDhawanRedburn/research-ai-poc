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
from .config import ROOT
from .pipeline import answer

log = logging.getLogger(__name__)

_spec = importlib.util.spec_from_file_location("retention", ROOT / "eval" / "retention.py")
retention = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(retention)

JUDGE_SYSTEM = """You grade answers from a document QA system against an expected answer.
correct: states the expected facts (numbers may differ only in rounding or formatting; extra correct detail is fine).
partial: some expected facts present, some missing or wrong.
incorrect: wrong, missing, or claims the documents do not say when they do.
For an unanswerable question the expected answer says the documents do not cover it; then \
"correct" means the system declined, and any made-up answer is "incorrect"."""


class Verdict(BaseModel):
    verdict: Literal["correct", "partial", "incorrect"]
    reason: str


def judge(q: dict, got: str) -> Verdict:
    model = chat_model(max_tokens=300).with_structured_output(Verdict)
    return model.invoke([SystemMessage(JUDGE_SYSTEM), HumanMessage(
        f"Question type: {q['type']}\nQuestion: {q['question']}\nExpected: {q.get('answer', '')}\n"
        f"System answer: {got}")])


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
                   "images": len(a.images)}
            rows.append(row)
            log.info("%s %-8s retrieved=%s verdict=%s", q["id"], source, row["evidence_hits"], v.verdict)

    with open(out_dir / "rag_eval.jsonl", "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    path = out_dir / "rag_eval.md"
    path.write_text(_report(rows, sources), encoding="utf-8")
    return path


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
    out += ["", "## Per question", "", "| id | type | " + " | ".join(sources) + " |",
            "|---|---|" + "---|" * len(sources)]
    per = defaultdict(dict)
    for r in rows:
        per[(r["id"], r["type"])][r["source"]] = f"{r['verdict']} ({r['evidence_hits']})"
    for (qid, t), v in per.items():
        out.append(f"| {qid} | {t} | " + " | ".join(v.get(s, "") for s in sources) + " |")
    out += ["", "Full answers and judge reasons: `reports/rag_eval.jsonl`.", ""]
    return "\n".join(out)
