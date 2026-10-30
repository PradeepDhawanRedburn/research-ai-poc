"""Run the golden questions through the agentic solutions and score them like `rag eval`, so the four-way
comparison (agentic vs RAG, each on docx and markdown) rests on the same 88 questions and the same judge.

Per question and solution:
- retrieval: are the golden evidence strings present in the passages the knowledge base returned (same matching as
  eval/retention.py); for picture questions the evidence is the caption, so this only says the figure was found;
- answer: the judge in eval/judge.py compares the answer with the expected one (correct / partial / incorrect);
- cost: seconds per round, tokens in and out, reasoning tokens, vision calls and their seconds.

Every question starts a fresh conversation, so nothing leaks between questions. Rows are appended to the jsonl as
they are produced; `--resume` skips (id, source) pairs already there, so a run interrupted by a token expiry or a
throttle can be continued. The Markdown report is produced by eval/scores.py, which also merges several jsonl files
(for example this one and rag's) into one table.
"""
from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path

import yaml

from .config import ROOT, settings

log = logging.getLogger(__name__)


def _module(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "eval" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


retention = _module("retention")
judge_mod = _module("judge")
scores = _module("scores")


def load_questions(ids: set[str] | None = None, types: set[str] | None = None, limit: int | None = None) -> list[dict]:
    qs: list[dict] = []
    for f in sorted((ROOT / "golden").glob("*.yaml")):
        qs.extend(yaml.safe_load(f.read_text(encoding="utf-8")) or [])
    qs = [q for q in qs if (not ids or q["id"] in ids) and (not types or q["type"] in types)]
    return qs[:limit] if limit else qs


def _done(path: Path) -> set[tuple[str, str]]:
    if not path.exists():
        return set()
    out = set()
    for line in path.open(encoding="utf-8"):
        try:
            r = json.loads(line)
            out.add((r["id"], r["source"]))
        except (ValueError, KeyError):
            continue
    return out


def _row(q: dict, source: str, a, verdict: dict, items) -> dict:
    """One jsonl row, with the fields eval/scores.py expects plus what is specific to the agent."""
    ctx = retention.normalise("\n".join(p.text for p in a.passages))
    hits = retention.present(items, ctx)
    kb_rounds = [s for s in a.steps if s.kind == "timing" and "knowledge base" in s.title]
    retrieval_s = sum(s.seconds or 0 for s in kb_rounds)
    vision_s = sum(lk.seconds for lk in a.looks)
    return {
        "id": q["id"], "type": q["type"], "doc": q["doc"], "source": source, "approach": "agentic",
        "evidence_retrieved": all(hits) if items else None,
        "evidence_hits": f"{sum(hits)}/{len(hits)}",
        "verdict": verdict["verdict"], "reason": verdict["reason"],
        "answer": a.text, "expected": q.get("answer", ""),
        "retrieved": [p.id for p in a.passages], "cited": [p.id for p in a.cited],
        "queries": a.queries, "images": len(a.looks),
        "retrieval_s": retrieval_s, "generation_s": max(0.0, a.seconds - retrieval_s - vision_s), "vision_s": vision_s,
        "total_s": a.seconds, "rounds": sum(1 for s in a.steps if s.kind == "timing"),
        "input_tokens": a.input_tokens, "output_tokens": a.output_tokens, "reasoning_tokens": a.reasoning_tokens,
        "total_tokens": a.input_tokens + a.output_tokens,
        "errors": a.errors, "model": a.model, "judge": settings.judge_deployment,
    }


def _error_row(q: dict, source: str, err: Exception) -> dict:
    return {"id": q["id"], "type": q["type"], "doc": q["doc"], "source": source, "approach": "agentic",
            "evidence_retrieved": False if q.get("evidence") else None, "evidence_hits": f"0/{len(q.get('evidence', []))}",
            "verdict": "incorrect", "reason": f"run failed: {type(err).__name__}: {str(err)[:300]}",
            "answer": "", "expected": q.get("answer", ""), "retrieved": [], "cited": [], "queries": [], "images": 0,
            "retrieval_s": 0.0, "generation_s": 0.0, "vision_s": 0.0, "total_s": 0.0, "rounds": 0,
            "input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "total_tokens": 0,
            "errors": [str(err)[:300]], "model": settings.agent_model, "judge": settings.judge_deployment}


def run(sources: list[str], ids: set[str] | None = None, types: set[str] | None = None, limit: int | None = None,
        use_images: bool = True, resume: bool = False, out_dir: Path = ROOT / "reports",
        chat_factory=None, judge=None) -> Path:
    """Returns the path of the Markdown report. `chat_factory(source)` and `judge(q, answer_text)` can be swapped
    for tests; by default they are the Foundry agent and the Azure OpenAI judge."""
    if chat_factory is None:
        from .foundry import Chat

        chat_factory = Chat
    if judge is None:
        from .vision import _client

        client = _client()
        judge = lambda q, got: judge_mod.judge_with_openai(client, settings.judge_deployment, q, got)  # noqa: E731

    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / "agentic_eval.jsonl"
    done = _done(jsonl) if resume else set()
    if not resume and jsonl.exists():
        jsonl.unlink()
    questions = load_questions(ids, types, limit)
    todo = [(q, s) for q in questions for s in sources if (q["id"], s) not in done]
    log.info("%d question/solution pairs to run (%d already done)", len(todo), len(done))
    with jsonl.open("a", encoding="utf-8") as fh:
        for n, (q, source) in enumerate(todo, 1):
            items = retention.evidence_items(q)
            try:
                a = chat_factory(source).ask(q["question"], use_images=use_images)  # fresh conversation per question
                row = _row(q, source, a, judge(q, a.text), items)
            except Exception as e:  # noqa: BLE001 - keep going; the row records the failure
                log.warning("%s %s failed: %s", q["id"], source, e)
                row = _error_row(q, source, e)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            print(f"[{n}/{len(todo)}] {q['id']:9} {source:9} retrieved={row['evidence_hits']:5} "
                  f"{row['verdict']:9} {row['total_s']:5.1f}s {row['input_tokens']:>6} tok in"
                  + (f"  vision x{row['images']}" if row["images"] else ""))
    md = out_dir / "agentic_eval.md"
    scores.main([jsonl], md)
    return md
