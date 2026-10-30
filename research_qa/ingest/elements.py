"""Helpers over the canonical model shared by facts.py and chunks.py: figure keys, tables, matrix cells."""
from __future__ import annotations

import re

from research_ingest.render import _layout, cell_text, guess_header_rows  # noqa: PLC2701 - same codebase

NUMBER_RE = re.compile(r"^[\s(\-–−+$€£¥]*[\d.,]+\s*[%x)]*\s*$")
ANCHOR_RE = re.compile(r"\s*\[e\d{4,}\]")


def figure_key(el: dict) -> str:
    """'Fig 22' for numbered figures, else the caption, else the element id."""
    return (el.get("figure_no") or el.get("caption") or el.get("id") or "").strip()


def tables_of(el: dict) -> list[dict]:
    if el.get("type") == "table":
        return [el["table"]]
    return [c["table"] for c in el.get("content", []) if c.get("kind") == "table"]


def charts_of(el: dict) -> list[dict]:
    return [c["chart"] for c in el.get("content", []) if c.get("kind") == "chart" and c.get("chart")]


def pictures_of(el: dict) -> list[dict]:
    """Picture drawings, including those nested in groups."""
    out = []
    for c in el.get("content", []):
        if c.get("kind") == "picture":
            out.append(c)
        elif c.get("kind") == "group":
            out.extend(ch for ch in c.get("children", []) if ch.get("kind") == "picture")
    return out


def symbol_state(cell: dict | None) -> str:
    """tick_green / cross_red / n/a / <glyph>_<colour> / text / '' for a matrix cell."""
    if not cell:
        return ""
    syms = cell.get("symbols") or []
    if syms:
        s = syms[0]
        g, colour = s.get("glyph") or "", (s.get("color") or "").replace(" ", "_")
        name = {"✓": "tick", "☑": "tick", "✗": "cross", "☒": "cross"}.get(g, g or "symbol")
        return f"{name}_{colour}" if colour else name
    t = (cell.get("text") or "").strip()
    if not t:
        return ""
    if t.lower() in ("n/a", "na", "n.a.", "-", "--", "—"):
        return "n/a"
    return t


def is_matrix(table: dict) -> bool:
    """A table whose body is mostly tick/cross symbols."""
    cells = table.get("cells", [])
    with_syms = sum(1 for c in cells if c.get("symbols"))
    return with_syms >= 3 and with_syms >= 0.25 * max(1, len([c for c in cells if c.get("text") or c.get("symbols")]))


def header_labels(table: dict) -> tuple[list[int], list[str], int, object]:
    """(leaf columns, header label per leaf column, first body row, at(row, col)) using the renderer's layout."""
    leaf, at = _layout(table)
    hdr = guess_header_rows(table, leaf, at)
    labels = []
    for c in leaf:
        parts: list[str] = []
        for r in hdr:
            txt = cell_text(at(r, c), with_symbols=False).strip("- ").strip()
            if txt and (not parts or parts[-1] != txt):
                parts.append(txt)
        labels.append(" / ".join(parts))
    return leaf, labels, (max(hdr) + 1) if hdr else 0, at


def matrix_cells(table: dict) -> list[tuple[str, str, str]]:
    """(row label, column label, state) for every symbol or N/A cell of a matrix."""
    leaf, labels, start, at = header_labels(table)
    out = []
    for r in range(start, table["n_rows"]):
        row_cells = [at(r, c) for c in leaf]
        first_sym = next((i for i, c in enumerate(row_cells) if c and c.get("symbols")), None)
        if first_sym is None:
            continue
        label_parts = [cell_text(c, with_symbols=False) for c in row_cells[:first_sym]]
        label = " ".join(p for p in label_parts if p and not NUMBER_RE.match(p)).strip() or \
            " ".join(p for p in label_parts if p).strip()
        seen = set()
        for i in range(first_sym, len(row_cells)):
            c = row_cells[i]
            if not c or id(c) in seen:
                continue
            seen.add(id(c))
            state = symbol_state(c)
            if state:
                out.append((label, labels[i], state))
    return out


def clean_text(s: str | None) -> str:
    return ANCHOR_RE.sub("", s or "").strip()
