"""The golden question set: golden/*.yaml, with filters by id, type and count."""
from __future__ import annotations

from pathlib import Path

import yaml

from ..config import ROOT

GOLDEN_DIR = ROOT / "golden"


def load_questions(ids: set[str] | list[str] | None = None, types: set[str] | list[str] | None = None,
                   limit: int | None = None, folder: Path = GOLDEN_DIR) -> list[dict]:
    qs: list[dict] = []
    for f in sorted(Path(folder).glob("*.yaml")):
        qs.extend(yaml.safe_load(f.read_text(encoding="utf-8")) or [])
    ids, types = set(ids or []), set(types or [])
    qs = [q for q in qs if (not ids or q["id"] in ids) and (not types or q["type"] in types)]
    return qs[:limit] if limit else qs


def question_types(qs: list[dict]) -> list[str]:
    return sorted({q["type"] for q in qs})
