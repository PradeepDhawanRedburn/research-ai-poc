"""Golden questions whose evidence is verbatim in the raw DOCX, not just in the extractor's output.

The golden set was written against Markdown+ output (`source_of_truth: extractor_output`), which favours that arm.
This picks the questions whose every evidence string also appears in the Word file's own XML (body text, tables,
native chart caches), so the score on them does not depend on the extractor's formatting.

    uv run python -m research_qa.evaluate.independent            # prints counts and writes eval ids
"""
from __future__ import annotations

import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

from .golden import load_questions

RAW = Path("data/raw")
OUT = Path("golden/independent_ids.txt")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip().lower()


def raw_corpus(docx: Path) -> str:
    parts: list[str] = []
    with zipfile.ZipFile(docx) as z:
        for name in z.namelist():
            if name == "word/document.xml" or re.fullmatch(r"word/charts/chart\d+\.xml", name):
                xml = z.read(name).decode("utf8", "ignore")
                parts.append(" ".join(re.findall(r"<(?:w:t|c:v)[^>]*>([^<]*)<", xml)))
    return _norm(" ".join(parts))


def independent_ids() -> tuple[list[str], Counter, Counter]:
    corpora: dict[str, str] = {}
    ids: list[str] = []
    total, kept = Counter(), Counter()
    for q in load_questions():
        total[q["type"]] += 1
        doc = q["doc"]
        if not q.get("evidence"):
            continue
        if doc not in corpora:
            corpora[doc] = raw_corpus(RAW / f"{doc}.docx")
        items = [e if isinstance(e, list) else [e] for e in q["evidence"]]
        if all(any(_norm(alt) in corpora[doc] for alt in alts) for alts in items):
            ids.append(q["id"])
            kept[q["type"]] += 1
    return ids, total, kept


if __name__ == "__main__":
    ids, total, kept = independent_ids()
    OUT.write_text("\n".join(ids) + "\n", encoding="utf8")
    for t in sorted(total):
        print(f"{t:16} {kept[t]:3}/{total[t]}")
    print(f"independent: {len(ids)}/{sum(total.values())} -> {OUT}")
    sys.exit(0)
