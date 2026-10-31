"""Structure-aware chunks from the canonical model (docs/DESIGN.md §3.3).

One chunker for everything that is indexed. Every chunk starts with a context header; no anchors, no file names,
no base64. Kinds: metadata (one per report), text (prose per section), table, matrix, figure (a picture with its
description), chart (native or digitised values).
"""
from __future__ import annotations

import re

from research_ingest.charts import chart_summary_text
from research_ingest.emf_chart import digitised_issue_text, digitised_summary_text
from research_ingest.render import render_table, table_row_groups, useful_alt_text

from ..config import settings
from ..schema import Chunk, FactFigure
from .elements import ANCHOR_RE, charts_of, clean_text, figure_key, is_matrix, pictures_of, tables_of

PROSE_TYPES = {"paragraph", "list_item", "key_data", "caption", "source", "note", "date", "author", "title",
               "subtitle"}
META_KEYS = ("title", "subtitle", "date", "sector", "report_type", "ticker", "share_price", "target_price",
             "mkt_cap", "rating", "published_at", "completed_at", "priced_as_of")
META_LABELS = {"mkt_cap": "Market cap", "published_at": "Published at", "completed_at": "Completed at",
               "priced_as_of": "Priced as of close", "report_type": "Report type", "share_price": "Share price",
               "target_price": "Target price"}


def header(md: dict, doc_id: str, section: str = "", figure: str = "") -> str:
    name = " - ".join(x for x in (md.get("title"), md.get("subtitle")) if x)
    h = f"Report {doc_id}: {name}" + (f" ({md['date']})" if md.get("date") else "")
    if section:
        h += f"\nSection: {section}"
    if figure:
        h += f"\nFigure: {figure}"
    return h


def metadata_text(md: dict) -> str:
    lines = ["Report metadata"]
    for k in META_KEYS:
        if md.get(k):
            lines.append(f"{META_LABELS.get(k, k.replace('_', ' ').capitalize())}: {md[k]}")
    if md.get("analysts"):
        names = [f"{a.get('name') or ''} <{a['email']}>".strip() for a in md["analysts"]]
        lines.append(("Analyst (author)" if len(names) == 1 else "Analysts (authors)") + ": " + "; ".join(names))
    extra = {k: v for k, v in md.items() if k not in META_KEYS and k not in
             ("doc_id", "source_file", "analysts", "rating_basis", "confidential_marking") and isinstance(v, str)}
    for k, v in extra.items():
        lines.append(f"{k.replace('_', ' ').capitalize()}: {v}")
    return "\n".join(lines)


def _prose(el: dict) -> str:
    s = clean_text(el.get("text"))
    for sym in el.get("symbols") or []:
        if sym.get("glyph") and sym.get("color"):
            s = s.replace(sym["glyph"], f"{sym['glyph']} ({sym['color']})", 1)
    if el.get("type") == "list_item":
        s = "  " * (el.get("list_level") or 0) + "- " + s
    elif el.get("type") == "title":
        s = f"# {s}"
    elif el.get("type") == "subtitle":
        s = f"## {s}"
    for fid, txt in (el.get("footnotes") or {}).items():
        if txt:
            s += f"\nFootnote: {txt}"
    return s


def _picture_text(pic: dict, assets: dict, descriptions: dict, asset_map: dict) -> tuple[list[str], str]:
    """(lines, kind) for one picture: digitised values, description, issues."""
    a = assets.get(pic.get("asset") or "", {})
    lines, kind = [], "figure"
    if useful_alt_text(pic.get("alt_text")):
        lines.append(f"Alt text: {useful_alt_text(pic['alt_text'])}")
    dig = a.get("digitised")
    if dig and digitised_summary_text(dig):
        lines.append(digitised_summary_text(dig))
        kind = "chart"
    elif dig and digitised_issue_text(dig):
        lines.append(digitised_issue_text(dig))
    key = asset_map.get(pic.get("asset") or "")
    rec = descriptions.get(key) if key else None
    if rec and rec.get("description"):
        lines.append(f"Image description (vision model): {rec['description'].strip()}")
    elif kind == "figure" and key:
        lines.append("(picture; no description available)")
    emf = a.get("emf_text") or {}
    if kind == "figure" and not rec and emf.get("labels"):
        labels = [x for x in emf["labels"] if len(x) > 1][:40]
        if labels:
            lines.append("Text in the picture: " + "; ".join(labels))
    return lines, kind


def _figure_text(el: dict, assets: dict, descriptions: dict, asset_map: dict) -> tuple[str, str, list[str]]:
    """(body, kind, asset keys) for a figure element. Tables inside figures are rendered as tables."""
    head = el.get("figure_no") or "Figure"
    cap = clean_text(el.get("caption"))
    lines = [f"**{head}: {cap}**" if cap else f"**{head}**"]
    kind, keys = "figure", []
    has_chart = has_table = has_pic = False
    for item in el.get("content", []):
        k = item.get("kind")
        if k == "chart" and item.get("chart"):
            lines.append("Native chart data:\n" + chart_summary_text(item["chart"]))
            has_chart = True
        elif k == "table":
            lines.append("\n" + render_table(item["table"]) + "\n")
            has_table = True
        elif k in ("picture", "group"):
            pics = [item] if k == "picture" else [c for c in item.get("children", []) if c.get("kind") == "picture"]
            for pic in pics:
                pl, pk = _picture_text(pic, assets, descriptions, asset_map)
                lines.extend(pl)
                has_chart = has_chart or pk == "chart"
                has_pic = True
                key = asset_map.get(pic.get("asset") or "")
                if key and key not in keys:
                    keys.append(key)
        elif k == "shape" and item.get("text"):
            lines.append(f"Shape text: {item['text']}")
    for t in el.get("extra_text") or []:
        lines.append(clean_text(t))
    if el.get("note"):
        lines.append(f"Note: {clean_text(el['note'])}")
    if el.get("source"):
        lines.append(f"Source: {clean_text(el['source'])}")
    if has_chart:
        kind = "chart"
    elif has_table and not has_pic:
        kind = "matrix" if any(is_matrix(t) for t in tables_of(el)) else "table"
    return "\n".join(l for l in lines if l is not None), kind, keys


def build_chunks(doc: dict, doc_id: str, source_file: str, descriptions: dict[str, dict], asset_map: dict[str, str],
                 facts: list[FactFigure]) -> list[Chunk]:
    """Chunks for one report. `descriptions` is keyed by asset store key, `asset_map` maps extractor sha -> key.
    FactFigure.chunk_id is filled in place for the figure chunks their values belong to."""
    md = doc.get("metadata", {})
    assets = doc.get("assets", {})
    limit = settings.chunk_chars
    title, date = md.get("title", "") or "", md.get("date", "") or ""
    facts_by_fig: dict[str, list[FactFigure]] = {}
    for f in facts:
        facts_by_fig.setdefault(f.figure, []).append(f)
    chunks: list[Chunk] = []

    def add(kind: str, body: str, section: str = "", caption: str = "", figure: str = "",
            keys: list[str] | None = None, has_facts: bool = False) -> Chunk:
        text = header(md, doc_id, section, figure) + "\n\n" + body.strip()
        text = ANCHOR_RE.sub("", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        c = Chunk(id=f"{doc_id}-{len(chunks):05d}", doc_id=doc_id, chunk=len(chunks), kind=kind, text=text,
                  title=title, date=date, section=section, caption=caption, figure=figure,
                  source_file=source_file, assets=list(keys or []), has_facts=has_facts)
        chunks.append(c)
        return c

    add("metadata", metadata_text(md))

    buf: list[str] = []
    buf_section = ""

    def flush() -> None:
        nonlocal buf
        if buf:
            add("text", "\n\n".join(buf), buf_section)
        buf = []

    for el in doc.get("elements", []):
        t = el.get("type")
        section = " > ".join(el.get("section_path") or [])
        if el.get("boilerplate"):
            section = section or "Regulatory disclosures"
        if t == "heading":
            flush()
            continue
        if t in ("figure", "table"):
            flush()
            fkey = figure_key(el)
            fig_no = el.get("figure_no") or ""
            caption = clean_text(el.get("caption"))
            ffacts = facts_by_fig.get(fkey, [])
            if t == "table":
                tbl = el["table"]
                kind = "matrix" if is_matrix(tbl) else "table"
                body = (f"**Table: {caption}**\n\n" if caption else "") + render_table(tbl)
                keys: list[str] = []
            else:
                body, kind, keys = _figure_text(el, assets, descriptions, asset_map)
            if not body.strip():
                continue
            c = add(kind, body, section, caption, fig_no, keys, bool(ffacts))
            for f in ffacts:
                f.chunk_id = f.chunk_id or c.id
            # long tables: row groups with the header repeated, each a chunk of its own
            label = f"**{fig_no or 'Table'}: {caption}**".strip() if caption else f"**{fig_no or 'Table'}**"
            for tbl in tables_of(el):
                for s, e, txt in table_row_groups(tbl):
                    add(kind if kind in ("table", "matrix") else "table", f"{label} (rows {s + 1}-{e})\n\n{txt}",
                        section, caption, fig_no, [], bool(ffacts))
            continue
        if t == "author_block":
            continue  # already in the metadata chunk
        if t not in PROSE_TYPES:
            continue
        text = _prose(el)
        if not text.strip():
            continue
        if buf and (section != buf_section or sum(len(b) for b in buf) + len(text) > limit):
            flush()
        if not buf:
            buf_section = section
        buf.append(text)
    flush()
    return chunks
