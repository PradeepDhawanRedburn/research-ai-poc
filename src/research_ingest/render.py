"""Render the canonical model into compact text for LLMs, plus retrieval chunks.

The canonical JSON is the store. This module is one *view* of it: Markdown-like text
with stable element IDs, tables with merged headers expanded, figures with their chart
series or image descriptions inline, boilerplate moved to the end.
"""
from __future__ import annotations

import json
import re

from .charts import chart_summary_text
from .emf_chart import digitised_summary_text, parse_date


def _clean(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(r"[ \t]+", " ", s.replace("\n", " ").replace("|", "\\|")).strip()


# ----------------------------------------------------------------------------- tables

SEMANTIC_FILLS = {"green", "light green", "red", "dark red", "amber", "yellow"}
SPACER_TWIPS = 120  # grid columns narrower than this are layout spacers, not data columns
DASH_PAD = re.compile(r"\s*-{3,}\s*")


def _has_content(c: dict | None) -> bool:
    return bool(c and (_clean(c.get("text")) or c.get("symbols") or c.get("drawings") or c.get("tables")))


def cell_text(c: dict | None, with_symbols: bool = True) -> str:
    if c is None:
        return ""
    t = DASH_PAD.sub(" ", _clean(c.get("text"))).strip()
    syms = c.get("symbols") or []
    if with_symbols and syms:
        rendered = []
        for s in syms:
            g = s.get("glyph") or s.get("raw") or "?"
            rendered.append(f"{g} ({s['color']})" if s.get("color") else g)
        # the glyphs already appear in text; replace them with the coloured form
        for s, rr in zip(syms, rendered):
            g = s.get("glyph")
            if g and g in t:
                t = t.replace(g, rr, 1)
        if not t.strip():
            t = " ".join(rendered)
    if not t and c.get("fill") in SEMANTIC_FILLS:
        t = f"[{c['fill']} fill]"
    for d in c.get("drawings") or []:
        k = d.get("kind")
        if k == "shape":
            tag = f"[shape {d.get('geometry') or ''} {d.get('fill') or d.get('line') or ''}".strip() + "]"
        elif k == "chart":
            tag = "[chart]"
        else:
            tag = f"[{k}]"
        t = (t + " " if t else "") + tag
    return t


def _layout(table: dict):
    """Leaf columns (visible data columns) and a lookup from (row, leaf) -> cell using x-extents.

    Authors build these grids visually: tiny spacer columns and header cells that span
    them. Index-based alignment is wrong by one; x-overlap against the leaf column
    centres is what the reader sees.
    """
    n_cols = table["n_cols"]
    widths = table.get("col_widths") or [1000] * n_cols
    widths = [w if w > 0 else 1000 for w in widths] + [1000] * (n_cols - len(widths))
    x0 = [0]
    for w in widths[:n_cols]:
        x0.append(x0[-1] + w)
    leaf = [c for c in range(n_cols) if widths[c] >= SPACER_TWIPS] or list(range(n_cols))
    centre = {c: (x0[c] + x0[c + 1]) / 2 for c in leaf}
    by_row: dict[int, list[dict]] = {}
    for cell in table["cells"]:
        for r in range(cell["row"], min(cell["row"] + cell["rowspan"], table["n_rows"])):
            by_row.setdefault(r, []).append(cell)

    def at(r: int, c: int) -> dict | None:
        cx = centre[c]
        for cell in by_row.get(r, []):
            xa, xb = x0[cell["col"]], x0[min(cell["col"] + cell["colspan"], n_cols)]
            if xa <= cx < xb or (xa <= cx <= xb and cell["col"] + cell["colspan"] >= n_cols):
                return cell
        return None

    # drop leaf columns with no content anywhere
    leaf = [c for c in leaf if any(_has_content(at(r, c)) for r in range(table["n_rows"]))] or leaf
    return leaf, at


def guess_header_rows(table: dict, leaf: list[int], at) -> list[int]:
    if table.get("header_rows"):
        return table["header_rows"]
    if table["n_rows"] < 2:
        return []
    hdr = [0]
    row0 = {id(at(0, c)): at(0, c) for c in leaf if at(0, c)}
    spans_group = any(c["colspan"] > 1 for c in row0.values()) or len(row0) < len(leaf) // 2 + 1
    if spans_group and table["n_rows"] > 2:
        hdr.append(1)
    return hdr


def render_table(table: dict, indent: str = "", row_range: tuple[int, int] | None = None) -> str:
    """Pipe table over the visible columns. row_range limits the body rows (for row-group chunks)."""
    if table["n_rows"] == 0 or table["n_cols"] == 0:
        return ""
    leaf, at = _layout(table)
    hdr_rows = guess_header_rows(table, leaf, at)
    lines = []
    headers = []
    for c in leaf:
        parts: list[str] = []
        for r in hdr_rows:
            txt = cell_text(at(r, c))
            if txt and (not parts or parts[-1] != txt):
                parts.append(txt)
        headers.append(" / ".join(parts))
    if any(headers):
        lines.append(indent + "| " + " | ".join(h or " " for h in headers) + " |")
        lines.append(indent + "|" + "---|" * len(leaf))
    body_start = (max(hdr_rows) + 1) if hdr_rows and any(headers) else 0
    nested: list[dict] = []
    body_rows = range(body_start, table["n_rows"])
    if row_range is not None:
        body_rows = range(max(body_start, row_range[0]), min(table["n_rows"], row_range[1]))
    for r in body_rows:
        row = []
        prev = None
        for c in leaf:
            cell = at(r, c)
            if cell is prev and cell is not None:
                row.append("")  # merged across: blank after the first leaf column
            elif cell is not None and cell["row"] != r and cell["rowspan"] > 1:
                row.append("〃")  # merged down: repeat marker
            else:
                row.append(cell_text(cell))
            prev = cell
            if cell and cell.get("tables") and cell not in nested:
                nested.append(cell)
        if any(x.strip() for x in row):
            lines.append(indent + "| " + " | ".join(row) + " |")
    out = "\n".join(lines)
    if row_range is not None:
        return out
    for cell in nested:
        for nt in cell["tables"]:
            sub = render_table(nt, indent)
            if sub.strip():
                out += f"\n{indent}Nested table in cell (row {cell['row'] + 1}, col {cell['col'] + 1}):\n" + sub
    return out


def table_row_groups(table: dict, group: int = 6, min_rows: int = 9) -> list[tuple[int, int, str]]:
    """(start, end, text) for row groups of a large table, each with the header repeated.

    Long tables (country matrices, peer tables) are one block in reading order but many
    separate facts for retrieval; a row group with its header is the unit a question hits.
    """
    if table["n_rows"] < min_rows:
        return []
    leaf, at = _layout(table)
    hdr = guess_header_rows(table, leaf, at)
    start = (max(hdr) + 1) if hdr else 0
    out = []
    for s in range(start, table["n_rows"], group):
        e = min(s + group, table["n_rows"])
        txt = render_table(table, row_range=(s, e))
        if txt.strip():
            out.append((s, e, txt))
    return out


# ----------------------------------------------------------------------------- figures

def render_drawing(d: dict, assets: dict) -> str:
    kind = d.get("kind")
    if kind == "chart":
        ch = d.get("chart")
        if not ch:
            return "Chart: (no data)"
        return chart_summary_text(ch)
    if kind == "picture":
        a = assets.get(d.get("asset") or "", {})
        bits = [f"Image: {a.get('file', d.get('asset'))}"]
        if d.get("alt_text"):
            bits.append(f"alt text: {d['alt_text']}")
        dig = a.get("digitised")
        emf = a.get("emf_text")
        if dig:
            bits.append(digitised_summary_text(dig))
            if emf and emf.get("labels"):
                other = [x for x in emf["labels"] if len(x) > 1 and x not in dig.get("legend", []) and not parse_date(x)][:20]
                if other:
                    bits.append("other text in image: " + "; ".join(other))
        elif emf and emf.get("n_records"):
            labels = [x for x in emf["labels"] if len(x) > 1][:40]
            axis = emf["axis_values"][:40]
            if labels:
                bits.append("labels in image: " + "; ".join(labels))
            if axis:
                bits.append("axis values in image: " + ", ".join(axis))
        if a.get("ext") in ("png", "jpeg", "jpg", "svg"):
            bits.append("(vision description pending)")
        return "\n".join(bits)
    if kind == "shape":
        desc = f"Shape: {d.get('geometry') or 'shape'}"
        if d.get("fill") or d.get("line"):
            desc += f" ({d.get('fill') or d.get('line')})"
        if d.get("text"):
            desc += f' text "{d["text"]}"'
        return desc
    if kind == "group":
        return "Shape group: " + "; ".join(render_drawing(c, assets) for c in d.get("children", []))
    if kind == "table":
        return render_table(d["table"])
    return f"{kind or 'object'}"


def render_figure(el: dict, assets: dict) -> str:
    head = el.get("figure_no") or "Figure"
    cap = el.get("caption")
    title = f"**{head}: {cap}**" if cap else f"**{head}**"
    lines = [title]
    for item in el.get("content", []):
        lines.append(render_drawing(item, assets))
    if el.get("extra_text"):
        lines.extend(el["extra_text"])
    if el.get("note"):
        lines.append(f"Note: {el['note']}")
    if el.get("source"):
        lines.append(f"Source: {el['source']}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- document

def render_metadata(md: dict) -> str:
    keys = ["doc_id", "title", "subtitle", "date", "sector", "report_type", "ticker", "share_price", "target_price",
            "mkt_cap", "rating", "published_at", "priced_as_of"]
    lines = ["---"]
    for k in keys:
        if md.get(k):
            lines.append(f"{k}: {md[k]}")
    if md.get("analysts"):
        lines.append("analysts: " + "; ".join(f"{a.get('name') or ''} <{a['email']}>".strip() for a in md["analysts"]))
    lines.append("---")
    return "\n".join(lines)


def render_element(el: dict, assets: dict, footnotes: dict) -> str:
    t = el["type"]
    tag = f"[{el['id']}]"
    if t == "heading":
        return f"{'#' * min(el['level'] + 1, 6)} {el['text']} {tag}"
    if t == "title":
        return f"# {el['text']} {tag}"
    if t == "subtitle":
        return f"## {el['text']} {tag}"
    if t == "paragraph":
        s = el["text"]
        if el.get("symbols"):
            for sym in el["symbols"]:
                if sym.get("glyph") and sym.get("color"):
                    s = s.replace(sym["glyph"], f"{sym['glyph']} ({sym['color']})", 1)
        out = f"{s} {tag}"
        if el.get("footnotes"):
            for fid, txt in el["footnotes"].items():
                out += f"\n[^{fid}]: {txt}"
        return out
    if t == "list_item":
        return f"{'  ' * (el.get('list_level') or 0)}- {el['text']} {tag}"
    if t in ("key_data", "date", "author", "caption", "source", "note"):
        return f"{el['text']} {tag}"
    if t == "author_block":
        return "Analysts: " + "; ".join(f"{a.get('name') or ''} <{a['email']}>" for a in el["analysts"]) + f" {tag}"
    if t == "table":
        cap = f"**Table: {el['caption']}**\n" if el.get("caption") else ""
        return f"{cap}{render_table(el['table'])}\n{tag}"
    if t == "figure":
        return render_figure(el, assets) + f"\n{tag}"
    return f"{el.get('text', '')} {tag}"


def render_document(doc: dict, include_boilerplate: bool = True) -> str:
    assets = doc.get("assets", {})
    fn = doc.get("footnotes", {})
    parts = [render_metadata(doc["metadata"]), ""]
    boiler = []
    for el in doc["elements"]:
        if el.get("boilerplate"):
            boiler.append(el)
            continue
        parts.append(render_element(el, assets, fn))
        parts.append("")
    if include_boilerplate and boiler:
        parts.append("## Regulatory disclosures (boilerplate)")
        parts.append("")
        for el in boiler:
            parts.append(render_element(el, assets, fn))
            parts.append("")
    return "\n".join(parts).strip() + "\n"


# ----------------------------------------------------------------------------- chunks

def build_chunks(doc: dict, max_chars: int = 1800) -> list[dict]:
    """Structure-aware chunks: each table/figure alone; prose merged within a section."""
    md = doc["metadata"]
    assets = doc.get("assets", {})
    fn = doc.get("footnotes", {})
    base = {k: md.get(k) for k in ("doc_id", "title", "date", "sector", "ticker", "report_type", "rating")}
    chunks: list[dict] = []
    buf: list[dict] = []
    buf_path: list[str] | None = None

    def flush():
        nonlocal buf, buf_path
        if not buf:
            return
        text = "\n".join(render_element(e, assets, fn) for e in buf)
        chunks.append({**base, "chunk_id": f"{md['doc_id']}:{buf[0]['id']}-{buf[-1]['id']}", "kind": "prose",
                       "section_path": buf_path or [], "element_ids": [e["id"] for e in buf],
                       "text": " > ".join(buf_path or []) + "\n" + text if buf_path else text,
                       "boilerplate": bool(buf[0].get("boilerplate"))})
        buf, buf_path = [], None

    for el in doc["elements"]:
        if el["type"] in ("table", "figure", "author_block"):
            flush()
            text = render_element(el, assets, fn)
            crumbs = " > ".join(el.get("section_path", []))
            chunks.append({**base, "chunk_id": f"{md['doc_id']}:{el['id']}", "kind": el["type"],
                           "section_path": el.get("section_path", []), "element_ids": [el["id"]],
                           "figure_no": el.get("figure_no"), "caption": el.get("caption"),
                           "text": (crumbs + "\n" if crumbs else "") + text, "boilerplate": False})
            # row-group chunks for large tables, header repeated, caption kept
            tables = [el["table"]] if el["type"] == "table" else [c["table"] for c in el.get("content", []) if c.get("kind") == "table"]
            title = f"**{el.get('figure_no') or 'Table'}: {el.get('caption') or ''}**".strip()
            for ti, tbl in enumerate(tables):
                for s, e, txt in table_row_groups(tbl):
                    chunks.append({**base, "chunk_id": f"{md['doc_id']}:{el['id']}:t{ti}:r{s}-{e}", "kind": "table_rows",
                                   "section_path": el.get("section_path", []), "element_ids": [el["id"]],
                                   "figure_no": el.get("figure_no"), "caption": el.get("caption"),
                                   "text": (crumbs + "\n" if crumbs else "") + title + f" (rows {s + 1}-{e})\n" + txt,
                                   "boilerplate": False})
            continue
        if el["type"] == "heading":
            flush()
            continue
        path = el.get("section_path", [])
        if buf and (path != buf_path or bool(el.get("boilerplate")) != bool(buf[0].get("boilerplate"))
                    or sum(len(e.get("text", "")) for e in buf) + len(el.get("text", "")) > max_chars):
            flush()
        if not buf:
            buf_path = path
        buf.append(el)
    flush()
    return chunks


def write_chunks(chunks: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for c in chunks:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
