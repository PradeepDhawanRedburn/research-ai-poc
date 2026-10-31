"""The records the parts of the system exchange. Plain dataclasses, JSON-friendly, no Azure types.

- ingest writes `Chunk`, `FactFigure` and `ReportManifest` rows to the store;
- search pushes `Chunk` rows into the Markdown+ index;
- agent (both modes, both arms) returns an `Answer`, which the app and the evaluation consume unchanged.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any

CHUNK_KINDS = ("metadata", "text", "table", "figure", "chart", "matrix")


def _from(cls, d: dict):
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in d.items() if k in known})


# ----------------------------------------------------------------------------- ingest output

@dataclass
class Chunk:
    """One retrieval unit. `text` already starts with the context header."""
    id: str                     # <doc_id>-<nnnnn>
    doc_id: str
    chunk: int
    kind: str                   # CHUNK_KINDS
    text: str
    title: str = ""
    date: str = ""
    section: str = ""
    caption: str = ""
    figure: str = ""            # "Fig 22" when the chunk is or belongs to a numbered figure
    source_file: str = ""       # RA02752.docx
    assets: list[str] = field(default_factory=list)   # store keys of pictures behind this chunk
    has_facts: bool = False     # lookup_facts has rows for this figure

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Chunk":
        return _from(cls, d)


@dataclass
class FactSeries:
    name: str
    points: list[list[Any]]     # [[x, y], ...]; x is a date string, a year or a category, y a number
    unit: str = ""
    axis: str = ""              # left | right


@dataclass
class FactCell:
    row: str
    col: str
    state: str                  # tick_green | cross_red | n/a | text


@dataclass
class FactFigure:
    """Exact values behind one figure: chart series or matrix cells."""
    doc_id: str
    figure: str                 # "Fig 27"
    caption: str
    kind: str                   # native_chart | digitised_chart | matrix | table
    chunk_id: str = ""
    method: str = ""            # how the values were read
    approximate: bool = False   # digitised from a drawing
    note: str = ""
    series: list[FactSeries] = field(default_factory=list)
    cells: list[FactCell] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FactFigure":
        f = _from(cls, {k: v for k, v in d.items() if k not in ("series", "cells")})
        f.series = [_from(FactSeries, s) for s in d.get("series", [])]
        f.cells = [_from(FactCell, c) for c in d.get("cells", [])]
        return f


@dataclass
class ReportManifest:
    doc_id: str
    source_file: str
    title: str = ""
    subtitle: str = ""
    date: str = ""
    analysts: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)      # ticker, rating, target price, sector, ...
    ingested_at: str = ""
    seconds: float = 0.0
    chunks: int = 0
    chunk_kinds: dict = field(default_factory=dict)
    figures: int = 0
    charts_native: int = 0
    charts_digitised_ok: int = 0
    charts_digitised_withheld: int = 0
    pictures: int = 0
    pictures_described: int = 0
    vision_calls: int = 0
    matrices: int = 0
    tables: int = 0
    facts_figures: int = 0
    warnings: list[str] = field(default_factory=list)
    markdown_chars: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ReportManifest":
        return _from(cls, d)


# ----------------------------------------------------------------------------- answers

@dataclass
class Passage:
    rank: int
    id: str
    doc_id: str
    source_file: str
    kind: str
    heading: str                # caption or section
    text: str
    cited: bool = False
    inspected: bool = False     # a vision model looked at its picture during this answer
    assets: list[str] = field(default_factory=list)   # store keys (Markdown+); Word arm chunks have none
    title: str = ""
    date: str = ""
    url: str = ""               # the reference URL the knowledge base gave, used to resolve citations


@dataclass
class Step:
    kind: str                   # plan | retrieve | facts | look | reason | answer
    title: str
    detail: str = ""
    items: list[str] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    seconds: float | None = None


@dataclass
class Answer:
    """What any arm in any mode returns. Tokens are everything the question cost: answer model plus vision."""
    arm: str
    mode: str
    question: str
    text: str                   # citations as [n] into `passages`
    raw_text: str = ""
    model: str = ""
    store: str = ""             # index or knowledge base the passages came from
    passages: list[Passage] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)   # {"tool", "args", "result", "seconds"}
    errors: list[str] = field(default_factory=list)
    retrieval_s: float = 0.0
    generation_s: float = 0.0
    tools_s: float = 0.0        # facts lookups and vision calls
    total_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    vision_tokens: int = 0      # inspect_figure calls (in + out)
    rounds: int = 1

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.vision_tokens

    @property
    def cited(self) -> list[Passage]:
        return [p for p in self.passages if p.cited]

    @property
    def inspected_assets(self) -> list[str]:
        return [a for p in self.passages if p.inspected for a in p.assets]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["total_tokens"] = self.total_tokens
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Answer":
        a = _from(cls, {k: v for k, v in d.items() if k not in ("passages", "steps", "total_tokens")})
        a.passages = [_from(Passage, p) for p in d.get("passages", [])]
        a.steps = [_from(Step, s) for s in d.get("steps", [])]
        return a
