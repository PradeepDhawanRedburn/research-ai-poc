"""The Markdown+ agent's two function tools. Both run on our side (function tools are executed by the client)
and read only the store: `lookup_facts` over facts/<doc>.json, `inspect_figure` over assets/<doc>/<sha>.png.

The Word arm has neither: its data does not exist there, which is the point of the comparison.
"""
from __future__ import annotations

import base64
import re
import time
from dataclasses import dataclass, field

from ..config import settings
from ..schema import FactFigure
from ..storage import FACTS, Store

MAX_ROWS = 40
MAX_PICTURES = 3

# ----------------------------------------------------------------------------- lookup_facts

FACTS_TOOL = "lookup_facts"
FACTS_DESCRIPTION = (
    "Exact values read from the reports' charts and matrices at ingestion: every series point of a native Word "
    "chart or a digitised Excel chart (value, unit, date or category) and every cell of a tick/cross matrix "
    "(row, column, state such as tick_green or cross_red). Use it for any number from a chart or any state from "
    "a matrix before looking at a picture. Filter by report id (doc_id, e.g. RA01220), figure (e.g. 'Fig 27'), "
    "series name (substring), x (a year such as 2030, a month such as 2025-06, or a category label) or a free-text "
    "query over captions and series names. Returns rows {doc_id, figure, caption, series, x, value, unit, "
    "approximate}; approximate=true means the value was read from a drawing to within about 1% of the axis range."
)
FACTS_PARAMETERS = {
    "type": "object",
    "properties": {
        "doc_id": {"type": ["string", "null"], "description": "report id such as RA01220, or null"},
        "figure": {"type": ["string", "null"], "description": "figure label such as 'Fig 27', or null"},
        "series": {"type": ["string", "null"], "description": "part of a series or row name, e.g. 'Germany', or null"},
        "x": {"type": ["string", "null"],
              "description": "a year (2030), a month (2025-06), a date, or a category/column label, or null"},
        "query": {"type": ["string", "null"], "description": "free text matched against captions and series names, or null"},
    },
    "required": ["doc_id", "figure", "series", "x", "query"],
    "additionalProperties": False,
}

FIG_RE = re.compile(r"(fig(?:ure)?|chart|table|exhibit)\.?\s*(\d+[a-z]?)", re.I)


def _norm_figure(s: str | None) -> str:
    m = FIG_RE.search(s or "")
    return m.group(2).lower() if m else (s or "").strip().lower()


def _x_matches(x: str, want: str) -> bool:
    x, want = str(x).strip().lower(), want.strip().lower()
    if not want:
        return True
    if x == want:
        return True
    # year or month prefix: "2030" matches "2030", "2030-12-31"; "2025-06" matches "2025-06-15"
    if re.fullmatch(r"\d{4}(-\d{2})?", want) and x.startswith(want):
        return True
    # "Jun-25" / "June 2025" style labels against "2025-06"
    return want in x


def load_facts(store: Store, doc_id: str | None = None) -> list[FactFigure]:
    keys = [f"{FACTS}/{doc_id}.json"] if doc_id else store.keys(f"{FACTS}/", ".json")
    out: list[FactFigure] = []
    for k in keys:
        if not store.exists(k):
            continue
        data = store.json(k)
        out.extend(FactFigure.from_dict(d) for d in (data if isinstance(data, list) else data.get("figures", [])))
    return out


def lookup_facts(store: Store, doc_id: str | None = None, figure: str | None = None, series: str | None = None,
                 x: str | None = None, query: str | None = None) -> dict:
    """Rows matching the filters, at most MAX_ROWS, plus a note when truncated or empty."""
    doc_id = (doc_id or "").strip().upper() or None
    fig = _norm_figure(figure) if figure else ""
    ser = (series or "").strip().lower()
    q = (query or "").strip().lower()
    rows: list[dict] = []
    total = 0
    for f in load_facts(store, doc_id):
        if fig and _norm_figure(f.figure) != fig:
            continue
        if q and q not in f.caption.lower() and not any(q in s.name.lower() for s in f.series) \
                and not any(q in c.row.lower() or q in c.col.lower() for c in f.cells):
            continue
        base = {"doc_id": f.doc_id, "figure": f.figure, "caption": f.caption, "approximate": f.approximate}
        for s in f.series:
            if ser and ser not in s.name.lower():
                continue
            for px, py in s.points:
                if x and not _x_matches(px, x):
                    continue
                total += 1
                if len(rows) < MAX_ROWS:
                    rows.append({**base, "series": s.name, "x": px, "value": py, "unit": s.unit})
        for c in f.cells:
            if ser and ser not in c.row.lower():
                continue
            if x and not _x_matches(c.col, x):
                continue
            total += 1
            if len(rows) < MAX_ROWS:
                rows.append({**base, "series": c.row, "x": c.col, "value": c.state, "unit": ""})
    note = ""
    if not rows:
        note = ("No facts match. Facts exist only for charts and matrices; try the figure label (e.g. 'Fig 27'), "
                "a different series name, or no x filter. For a picture use inspect_figure.")
    elif total > len(rows):
        note = f"{total} rows match; showing the first {len(rows)}. Narrow with figure, series or x."
    return {"rows": rows, "count": total, "note": note}


# ----------------------------------------------------------------------------- inspect_figure

FIGURE_TOOL = "inspect_figure"
FIGURE_DESCRIPTION = (
    "Look at the picture behind a figure chunk and answer a question about what it shows: counting objects, "
    "colours, labels, layout, or values read from a chart that lookup_facts does not have. Use it only when the "
    "answer depends on the picture and neither the chunk text nor lookup_facts gives it exactly. chunk_id is the "
    "`id` field inside the retrieved content (for example RA02752-00145) of a chunk whose kind is figure or chart."
)
FIGURE_PARAMETERS = {
    "type": "object",
    "properties": {
        "chunk_id": {"type": "string", "description": "id of the figure chunk, e.g. RA02752-00145"},
        "question": {"type": "string", "description": "precise question about the picture, in the user's own words"},
    },
    "required": ["chunk_id", "question"],
    "additionalProperties": False,
}

VISION_SYSTEM = (
    "You look at a picture from a financial research report and answer one question about it. Describe only what is "
    "visible. When counting, first list every instance you find (where it is, what form it takes: whole fruit, "
    "slice, wedge, drawing, logo), then give the count per item (can, panel) and the total. Include partly hidden "
    "or cut ones and say which they are. Consider the whole picture: if the question names only part of it (for "
    "example 'the Fanta image' when other brands are shown), answer for that part and also report the rest "
    "separately. Read chart labels and values exactly as printed. If something is unclear or cut off, say so "
    "instead of guessing."
)


@dataclass
class FigureLook:
    chunk_id: str
    question: str
    answer: str
    assets: list[str] = field(default_factory=list)   # store keys shown to the model
    seconds: float = 0.0
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


def data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


def inspect_figure(store: Store, chunk_id: str, chunk_assets: list[str], caption_and_text: str,
                   question: str) -> FigureLook:
    """A vision model looks at up to MAX_PICTURES pictures of the chunk (fetched from the store) and answers."""
    t0 = time.perf_counter()
    assets = [a for a in chunk_assets if store.exists(a)][:MAX_PICTURES]
    if not assets:
        return FigureLook(chunk_id, question, f"Chunk {chunk_id} has no picture in the store; answer from its text "
                          "or lookup_facts.", seconds=time.perf_counter() - t0)
    from ..search.azure import openai_client

    content = [{"type": "text", "text": f"Caption and description of the figure:\n{caption_and_text[:1500]}\n\n"
                                        f"Question: {question}"}]
    content += [{"type": "image_url", "image_url": {"url": data_url(store.get(a)), "detail": "high"}} for a in assets]
    reasoning = settings.vision_deployment.startswith(("gpt-5", "o"))  # reasoning models: no temperature
    kw = {"reasoning_effort": settings.vision_effort} if reasoning else {"temperature": 0}
    resp = openai_client().chat.completions.create(
        model=settings.vision_deployment,
        messages=[{"role": "system", "content": VISION_SYSTEM}, {"role": "user", "content": content}],
        max_completion_tokens=2000, **kw,
    )
    u = getattr(resp, "usage", None)
    return FigureLook(chunk_id, question, (resp.choices[0].message.content or "").strip(), assets,
                      time.perf_counter() - t0, settings.vision_deployment,
                      int(getattr(u, "prompt_tokens", 0) or 0), int(getattr(u, "completion_tokens", 0) or 0))
