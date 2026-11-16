"""Human calibration samples and agreement statistics for the answer judge."""
from __future__ import annotations

import csv
import random
from collections import defaultdict
from pathlib import Path

from ..storage import Store
from .golden import load_questions
from .run import run_key

FIELDS = ["id", "type", "arm", "mode", "question", "answer", "judge_verdict", "human_verdict"]


def stratified_sample(rows: list[dict], questions: list[dict], n: int = 50, seed: int = 0) -> list[dict]:
    question_by_id = {q["id"]: q for q in questions}
    groups = defaultdict(list)
    for row in rows:
        if row.get("answer", "").strip() and row["id"] in question_by_id:
            groups[row.get("type", "unknown")].append(row)
    rng = random.Random(seed)
    for group in groups.values():
        rng.shuffle(group)
    sample = []
    types = sorted(groups)
    while len(sample) < n and types:
        remaining = []
        for question_type in types:
            if groups[question_type] and len(sample) < n:
                row = groups[question_type].pop()
                q = question_by_id[row["id"]]
                sample.append({"id": row["id"], "type": row.get("type", "unknown"), "arm": row.get("arm", ""),
                               "mode": row.get("mode", ""), "question": q.get("question", ""),
                               "answer": row.get("answer", ""), "judge_verdict": row.get("verdict", ""),
                               "human_verdict": ""})
            if groups[question_type]:
                remaining.append(question_type)
        types = remaining
    return sample


def export_sample(store: Store, run_name: str, output: Path, n: int = 50,
                  questions: list[dict] | None = None, seed: int = 0) -> int:
    rows_key = f"{run_key(run_name)}/rows.jsonl"
    if not store.exists(rows_key):
        raise ValueError(f"run {run_name!r} has no rows")
    rows = store.jsonl(rows_key)
    selected = stratified_sample(rows, questions or load_questions(), n=n, seed=seed)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(selected)
    return len(selected)


def agreement(path: Path) -> dict:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as stream:
        rows = [row for row in csv.DictReader(stream) if row.get("human_verdict", "").strip()]
    return _agreement_from_rows(rows)


def _agreement_from_rows(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0, "agreement": None, "kappa": None}
    judge = [row.get("judge_verdict", "").strip().casefold() for row in rows]
    human = [row.get("human_verdict", "").strip().casefold() for row in rows]
    categories = sorted(set(judge) | set(human))
    observed = sum(a == b for a, b in zip(judge, human)) / len(rows)
    expected = sum(judge.count(c) * human.count(c) for c in categories) / (len(rows) ** 2)
    kappa = (observed - expected) / (1 - expected) if expected != 1 else None
    return {"n": len(rows), "agreement": observed, "kappa": kappa}


def agreement_markdown(stats: dict) -> str:
    if not stats["n"]:
        return "No graded rows found in the CSV."
    kappa = "undefined" if stats["kappa"] is None else f"{stats['kappa']:.3f}"
    return f"Graded rows: {stats['n']}\nPercent agreement: {stats['agreement']:.1%}\nCohen's kappa: {kappa}"
