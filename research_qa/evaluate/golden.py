"""The golden question set: golden/*.yaml, with filters by id, type and count."""
from __future__ import annotations

from pathlib import Path

import yaml

from ..config import ROOT

GOLDEN_DIR = ROOT / "golden"
QUESTION_TYPES = {"text_fact", "table_lookup", "chart_native", "chart_picture", "symbol_matrix", "image",
                  "relationship", "cross_document", "unanswerable", "temporal_change", "analyst_view",
                  "metadata_filter", "multi_doc_aggregate", "wrong_edition_trap"}
SOURCE_OF_TRUTH = {"pdf_analyst", "word_engineer", "extractor_output"}


def validate_questions(questions: list[dict]) -> None:
    for question in questions:
        if question.get("type") not in QUESTION_TYPES:
            raise ValueError(f"{question.get('id', '<unknown>')}: unsupported question type {question.get('type')!r}")
        source = question.get("source_of_truth", "extractor_output")
        if source not in SOURCE_OF_TRUTH:
            raise ValueError(f"{question.get('id', '<unknown>')}: invalid source_of_truth {source!r}")
        for key in ("verified_by", "verified_on"):
            if key in question and not isinstance(question[key], str):
                raise ValueError(f"{question.get('id', '<unknown>')}: {key} must be a string")


def load_questions(ids: set[str] | list[str] | None = None, types: set[str] | list[str] | None = None,
                   limit: int | None = None, folder: Path = GOLDEN_DIR) -> list[dict]:
    qs: list[dict] = []
    for f in sorted(Path(folder).glob("*.yaml")):
        qs.extend(yaml.safe_load(f.read_text(encoding="utf-8")) or [])
    validate_questions(qs)
    ids, types = set(ids or []), set(types or [])
    qs = [q for q in qs if (not ids or q["id"] in ids) and (not types or q["type"] in types)]
    return qs[:limit] if limit else qs


def question_types(qs: list[dict]) -> list[str]:
    return sorted({q["type"] for q in qs})
