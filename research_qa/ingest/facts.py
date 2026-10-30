"""Exact values behind figures: native chart series, digitised chart series, matrix cells.

Markdown is for reading and searching; `facts/<doc>.json` is for lookups ("Germany 2030 in Fig 27",
"Evolution on Stake in the Italy matrix"). Only digitisations that passed the quality gate become facts.
"""
from __future__ import annotations

from ..schema import FactCell, FactFigure, FactSeries
from .elements import charts_of, figure_key, is_matrix, matrix_cells, pictures_of, tables_of


def _unit(fmt: str | None, pct: bool = False) -> str:
    if pct or (fmt and "%" in fmt):
        return "%"
    return ""


def _native(chart: dict) -> list[FactSeries]:
    out = []
    for s in chart.get("series", []):
        pts = [[c, v] for c, v in zip(s.get("categories", []), s.get("values", []))
               if isinstance(v, (int, float)) and c not in (None, "")]
        if pts:
            out.append(FactSeries(s.get("name") or "", pts, _unit(s.get("value_format"))))
    return out


def _digitised(d: dict) -> list[FactSeries]:
    out = []
    for s in d.get("series", []):
        pts = [[c, round(v, 4)] for c, v in s.get("points", []) if isinstance(v, (int, float)) and c is not None]
        if pts:
            out.append(FactSeries(s.get("name") or "", pts, _unit(None, bool(s.get("pct"))), s.get("axis") or ""))
    return out


def facts_for(doc: dict, doc_id: str, chunk_ids: dict[str, str] | None = None) -> list[FactFigure]:
    """FactFigure rows for a canonical document. `chunk_ids` maps element id -> chunk id when known (chunks.py also
    fills `chunk_id` in place when it builds the figure chunks)."""
    chunk_ids = chunk_ids or {}
    assets = doc.get("assets", {})
    out: list[FactFigure] = []
    for el in doc.get("elements", []):
        if el.get("type") not in ("figure", "table"):
            continue
        key = figure_key(el)
        caption = (el.get("caption") or "").strip()
        cid = chunk_ids.get(el.get("id", ""), "")
        for ch in charts_of(el):
            series = _native(ch)
            if series:
                out.append(FactFigure(doc_id, key, caption, "native_chart", cid, "native Word chart: cached values",
                                      False, (ch.get("title") or ""), series))
        for pic in pictures_of(el):
            a = assets.get(pic.get("asset") or "", {})
            d = a.get("digitised")
            if d and d.get("quality", "ok") == "ok":
                series = _digitised(d)
                if series:
                    out.append(FactFigure(doc_id, key, caption, "digitised_chart", cid, d.get("method", ""), True,
                                          d.get("precision_note", ""), series))
        for t in tables_of(el):
            if is_matrix(t):
                cells = [FactCell(r, c, s) for r, c, s in matrix_cells(t)]
                if cells:
                    out.append(FactFigure(doc_id, key, caption, "matrix", cid, "Wingdings symbols with run colour",
                                          False, "", [], cells))
    return out


def lookup(facts: list[FactFigure], figure: str = "", series: str = "", x: str = "", row: str = "",
           col: str = "", limit: int = 60) -> list[dict]:
    """Case-insensitive substring filter over facts; the agent's lookup_facts tool uses this."""
    fig_q, ser_q, x_q, row_q, col_q = (s.lower().strip() for s in (figure, series, x, row, col))
    rows: list[dict] = []
    for f in facts:
        if fig_q and fig_q not in f.figure.lower() and fig_q not in f.caption.lower():
            continue
        for s in f.series:
            if ser_q and ser_q not in (s.name or "").lower():
                continue
            for px, py in s.points:
                if x_q and x_q not in str(px).lower():
                    continue
                rows.append({"doc_id": f.doc_id, "figure": f.figure, "caption": f.caption, "series": s.name,
                             "x": px, "value": py, "unit": s.unit, "approximate": f.approximate})
        for c in f.cells:
            if (row_q and row_q not in c.row.lower()) or (col_q and col_q not in c.col.lower()):
                continue
            rows.append({"doc_id": f.doc_id, "figure": f.figure, "caption": f.caption, "row": c.row, "col": c.col,
                         "state": c.state})
    return rows[:limit]
