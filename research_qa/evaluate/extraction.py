"""Deterministic evidence coverage over Markdown+ and structured facts."""
from __future__ import annotations

import json
from collections import defaultdict

from ..storage import FACTS, MARKDOWN, Store
from .golden import load_questions
from .report import wilson_interval
from .retention import evidence_items, normalise, present


def _fact_text(facts: list[dict]) -> str:
    parts = []
    for figure in facts:
        parts.extend((figure.get("figure", ""), figure.get("figure_label", ""), figure.get("caption", "")))
        for series in figure.get("series", []):
            name = series.get("name", "")
            parts.append(name)
            for x, value in series.get("points", []):
                parts.extend((str(x), str(value), f"{x}: {value}", f"{name} {x}: {value}"))
        for cell in figure.get("cells", []):
            row, col, value = cell.get("row", ""), cell.get("col", ""), cell.get("state", "")
            parts.extend((str(row), str(col), str(value), f"{row} {col}: {value}", f"{row}: {value}"))
    return " ".join(str(part) for part in parts if part)


def check(store: Store, questions: list[dict] | None = None) -> dict:
    questions = questions or load_questions()
    rows = []
    for question in questions:
        items = evidence_items(question)
        if not items:
            continue
        docs = question.get("docs") or [question["doc"]]
        missing_docs = [doc for doc in docs if not store.exists(f"{MARKDOWN}/{doc}.md")]
        if missing_docs:
            rows.append({"id": question["id"], "type": question["type"],
                         "source_of_truth": question.get("source_of_truth", "extractor_output"),
                         "available": False, "docs_missing": missing_docs})
            continue
        content = []
        for doc in docs:
            content.append(store.text(f"{MARKDOWN}/{doc}.md"))
            key = f"{FACTS}/{doc}.json"
            if store.exists(key):
                raw = store.json(key)
                content.append(_fact_text(raw if isinstance(raw, list) else raw.get("figures", [])))
        hits = present(items, normalise("\n".join(content)))
        rows.append({"id": question["id"], "type": question["type"],
                     "source_of_truth": question.get("source_of_truth", "extractor_output"),
                     "available": True, "extracted_all": all(hits), "missing_count": hits.count(False)})
    by_source = defaultdict(lambda: {"n": 0, "extracted": 0})
    by_type = defaultdict(lambda: {"n": 0, "extracted": 0})
    for row in rows:
        if not row["available"]:
            continue
        for key, value in ((by_source, row["source_of_truth"]), (by_type, row["type"])):
            key[value]["n"] += 1
            key[value]["extracted"] += row["extracted_all"]
    for group in (by_source, by_type):
        for result in group.values():
            result["rate"] = result["extracted"] / result["n"] if result["n"] else None
            result["ci"] = wilson_interval(result["extracted"], result["n"])
    return {"rows": rows, "by_source_of_truth": dict(by_source), "by_type": dict(by_type),
            "unavailable": [row for row in rows if not row["available"]]}


def markdown(result: dict) -> str:
    out = ["Evidence strings present in Markdown+ and structured facts. This is a conversion-coverage measure, not an independent review.", "",
           "WARNING: extractor_output questions were verified against extractor output; only non-extractor sources are independent.", "",
           "| source_of_truth | extracted / n | rate | 95% Wilson CI |", "|---|---:|---:|---|"]
    for source, stats in sorted(result["by_source_of_truth"].items()):
        ci = "-" if stats["ci"] is None else f"{stats['ci'][0]:.0%}–{stats['ci'][1]:.0%}"
        rate = "-" if stats["rate"] is None else f"{stats['rate']:.0%}"
        out.append(f"| {source} | {stats['extracted']}/{stats['n']} | {rate} | {ci} |")
    if result["unavailable"]:
        out += ["", "Unavailable documents: " + ", ".join(sorted({doc for row in result["unavailable"]
                                                                  for doc in row["docs_missing"]}))]
    return "\n".join(out)