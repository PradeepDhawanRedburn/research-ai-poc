"""Deterministic extraction of a research .docx into a canonical, lossless-enough JSON model.

No LLM is involved. Everything here is read from the OOXML package itself:
reading order, heading tree, tables with spans, figure grids flattened into figures,
native chart series, symbol+colour cells, images (with EMF text), footnotes, metadata.
"""
from __future__ import annotations

import hashlib
import os
import posixpath
import re
from dataclasses import dataclass, field

from lxml import etree

from .charts import parse_chart
from .emf import emf_text_records, summarise_emf_text
from .emf_chart import digitise
from .package import NS, DocxPackage, q, wval

# --------------------------------------------------------------------------- styles

# Template style names -> semantic role. Heuristics are applied after this map.
STYLE_ROLES = {
    "Thesis": "thesis",
    "BodyText": "body",
    "Normal0": "body",
    "Normal": "body",
    "Bullet": "list",
    "Bullet2": "list",
    "TableTitle": "caption",
    "TableSource": "source",
    "Tableplaceholder": "placeholder",
    "Tablespaceafter": "placeholder",
    "TableText0": "table_text",
    "KeyDatatext": "key_data",
    "AnalystContact": "disclosure",
    "MainAuthor": "author",
    "SectorDate": "date",
    "DateCompanyAggregates": "date",
    "ReportTitleSectorAggregates": "report_title",
    "ReportTitleAggregates": "report_title",
    "ReportSubTitle": "report_subtitle",
    "SECTIONChapter": "heading",
}

HEADING_RE = re.compile(r"^Heading\s*(\d)", re.I)
CAPTION_RE = re.compile(r"^\s*((?:Fig(?:ure)?|Table|Chart|Exhibit)\.?\s*\d+[a-zA-Z]?)\s*[:.\-–]?\s*(.*)$", re.S)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
DISCLOSURE_HINTS = ("Important note", "regulatory disclosures", "Conflicts of Interest", "FINRA",
                    "This research was", "Rothschild & Co Redburn argues that", "solely for the information")

SYMBOL_FONTS = {"wingdings", "wingdings 2", "wingdings 3", "webdings", "symbol"}
WINGDINGS = {
    0xFC: "✓", 0xFB: "✗", 0xFE: "☑", 0xFD: "☒", 0x6C: "●", 0x6D: "○", 0x6E: "■", 0x6F: "□",
    0x75: "◆", 0x76: "❖", 0xE8: "→", 0xE7: "←", 0xE9: "↑", 0xEA: "↓", 0xD8: "➢", 0xA7: "▪",
    0x9F: "•", 0xB7: "•", 0xF0: "⇨", 0xF1: "⇦", 0xF2: "⇧", 0xF3: "⇩",
}
WINGDINGS3 = {0x70: "▲", 0x71: "▼", 0x73: "◄", 0x75: "►", 0x68: "▲", 0x69: "▼"}
SYMBOLFONT = {0xAE: "→", 0xAC: "←", 0xAD: "↑", 0xAF: "↓", 0xB7: "•", 0xD6: "√", 0xB1: "±", 0xA3: "≤", 0xB3: "≥"}
COLOR_NAMES = {
    "00B050": "green", "92D050": "light green", "FF0000": "red", "C00000": "dark red", "0000FF": "blue",
    "000000": "black", "FFC000": "amber", "FFFF00": "yellow", "7F7F7F": "grey", "A6A6A6": "grey",
    "1C355E": "navy", "FFFFFF": "white",
}


def color_name(hexv: str | None) -> str | None:
    if not hexv or hexv.lower() == "auto":
        return None
    return COLOR_NAMES.get(hexv.upper(), "#" + hexv.upper())


def map_symbol(font: str | None, code: int) -> str | None:
    f = (font or "").lower()
    code &= 0xFF
    if f.startswith("wingdings 3"):
        return WINGDINGS3.get(code)
    if f.startswith("wingdings"):
        return WINGDINGS.get(code)
    if f == "symbol":
        return SYMBOLFONT.get(code)
    if f == "webdings":
        return None
    return None


# --------------------------------------------------------------------------- data model


@dataclass
class Para:
    style: str | None
    role: str
    text: str
    symbols: list[dict] = field(default_factory=list)
    footnotes: list[str] = field(default_factory=list)
    list_level: int | None = None
    links: list[dict] = field(default_factory=list)
    drawings: list[dict] = field(default_factory=list)
    bold_all: bool = False

    def to_json(self):
        d = {"style": self.style, "role": self.role, "text": self.text}
        for k in ("symbols", "footnotes", "links", "drawings"):
            v = getattr(self, k)
            if v:
                d[k] = v
        if self.list_level is not None:
            d["list_level"] = self.list_level
        return d


class Extractor:
    def __init__(self, path: str, assets_dir: str, doc_id: str | None = None):
        self.pkg = DocxPackage(path)
        self.path = path
        self.doc_id = doc_id or os.path.splitext(os.path.basename(path))[0]
        self.assets_dir = assets_dir
        os.makedirs(assets_dir, exist_ok=True)
        self.doc_rels = self.pkg.rels("word/document.xml")
        self.assets: dict[str, dict] = {}
        self.charts: dict[str, dict] = {}
        self.elements: list[dict] = []
        self.section_path: list[str] = []
        self._eid = 0
        self.footnotes = self._load_notes("word/footnotes.xml", "footnote")
        self.endnotes = self._load_notes("word/endnotes.xml", "endnote")
        self.warnings: list[str] = []

    # ------------------------------------------------------------------ helpers
    def next_id(self) -> str:
        self._eid += 1
        return f"e{self._eid:04d}"

    def _load_notes(self, part: str, tag: str) -> dict[str, str]:
        out = {}
        if not self.pkg.has(part):
            return out
        root = self.pkg.xml(part)
        for n in root.findall(f"w:{tag}", NS):
            if wval(n, "type") in ("separator", "continuationSeparator"):
                continue
            text = " ".join(self.para_text_simple(p) for p in n.findall("w:p", NS)).strip()
            out[wval(n, "id")] = text
        return out

    def para_text_simple(self, p) -> str:
        return "".join(t.text or "" for t in p.iter(q("w:t")))

    # ------------------------------------------------------------------ runs
    def run_segments(self, r, para: Para):
        """Append the text of one run to para.text, resolving symbols and colours."""
        rpr = r.find("w:rPr", NS)
        font = None
        color = None
        if rpr is not None:
            rf = rpr.find("w:rFonts", NS)
            if rf is not None:
                font = rf.get(q("w:ascii")) or rf.get(q("w:hAnsi")) or rf.get(q("w:cs"))
            col = rpr.find("w:color", NS)
            color = color_name(wval(col))
        is_symbol_font = (font or "").lower() in SYMBOL_FONTS or (font or "").lower().startswith("wingdings")
        out = []
        for ch in r:
            if not isinstance(ch.tag, str):
                continue
            tag = ch.tag
            if tag == q("w:t"):
                txt = ch.text or ""
                if is_symbol_font:
                    conv = []
                    for c in txt:
                        g = map_symbol(font, ord(c))
                        if g:
                            conv.append(g)
                            para.symbols.append({"glyph": g, "font": font, "code": "%02X" % (ord(c) & 0xFF), "color": color})
                        else:
                            conv.append(c)
                            para.symbols.append({"glyph": None, "font": font, "code": "%02X" % (ord(c) & 0xFF), "color": color,
                                                 "raw": c})
                    txt = "".join(conv)
                out.append(txt)
            elif tag == q("w:sym"):
                f = ch.get(q("w:font"))
                code = int(ch.get(q("w:char"), "0"), 16)
                g = map_symbol(f, code) or f"‹{f}:{code:X}›"
                para.symbols.append({"glyph": g, "font": f, "code": "%02X" % (code & 0xFF), "color": color})
                out.append(g)
            elif tag == q("w:tab"):
                out.append("\t")
            elif tag in (q("w:br"), q("w:cr")):
                out.append("\n")
            elif tag == q("w:noBreakHyphen"):
                out.append("-")
            elif tag == q("w:softHyphen"):
                pass
            elif tag == q("w:footnoteReference"):
                fid = wval(ch, "id")
                para.footnotes.append(fid)
                out.append(f"[^{fid}]")
            elif tag == q("w:endnoteReference"):
                fid = wval(ch, "id")
                para.footnotes.append("end" + fid)
                out.append(f"[^end{fid}]")
            elif tag == q("w:drawing"):
                para.drawings.extend(self.parse_drawing(ch))
            elif tag == q("w:pict"):
                para.drawings.extend(self.parse_vml(ch))
            elif tag == q("w:object"):
                para.drawings.extend(self.parse_vml(ch))
            elif tag == q("mc:AlternateContent"):
                choice = ch.find("mc:Choice", NS)
                fallback = ch.find("mc:Fallback", NS)
                target = choice if choice is not None else fallback
                if target is not None:
                    for d in target.iter(q("w:drawing")):
                        para.drawings.extend(self.parse_drawing(d))
                    if choice is None:
                        for d in target.iter(q("w:pict")):
                            para.drawings.extend(self.parse_vml(d))
        para.text += "".join(out)

    def parse_paragraph(self, p) -> Para:
        ppr = p.find("w:pPr", NS)
        style = wval(ppr.find("w:pStyle", NS)) if ppr is not None else None
        role = self.role_for_style(style)
        para = Para(style=style, role=role, text="")
        if ppr is not None:
            num = ppr.find("w:numPr", NS)
            if num is not None:
                lvl = num.find("w:ilvl", NS)
                para.list_level = int(wval(lvl) or 0)
                if role == "body":
                    para.role = "list"
        for child in p:
            if not isinstance(child.tag, str):
                continue
            if child.tag == q("w:r"):
                self.run_segments(child, para)
            elif child.tag == q("w:hyperlink"):
                rid = child.get(q("r:id"))
                href = self.doc_rels.target(rid) if rid else child.get(q("w:anchor"))
                start = len(para.text)
                for r in child.findall("w:r", NS):
                    self.run_segments(r, para)
                para.links.append({"text": para.text[start:], "href": href})
            elif child.tag in (q("w:ins"), q("w:smartTag"), q("w:sdt")):
                for r in child.iter(q("w:r")):
                    self.run_segments(r, para)
            elif child.tag == q("w:fldSimple"):
                for r in child.iter(q("w:r")):
                    self.run_segments(r, para)
            elif child.tag == q("m:oMathPara") or child.tag == q("m:oMath"):
                para.text += "".join(t.text or "" for t in child.iter(q("m:t")))
        para.text = para.text.strip()
        if "Bullet" in (style or "") and para.list_level is None:
            para.list_level = 1 if style.endswith("2") else 0
        # heuristics on text
        if role in ("body", "table_text") and para.text:
            m = CAPTION_RE.match(para.text)
            if m and len(para.text) < 160 and not para.text.endswith("."):
                para.role = "caption"
            elif para.text.startswith(("Source:", "Sources:")):
                para.role = "source"
            elif para.text.startswith("Note:") and len(para.text) < 300:
                para.role = "note"
        if role == "body" and any(h in para.text for h in DISCLOSURE_HINTS):
            para.role = "disclosure"
        return para

    @staticmethod
    def role_for_style(style: str | None) -> str:
        if not style:
            return "body"
        if style in STYLE_ROLES:
            return STYLE_ROLES[style]
        if HEADING_RE.match(style):
            return "heading"
        if "Bullet" in style or style.startswith("List"):
            return "list"
        if "Caption" in style or "TableTitle" in style or "FigureTitle" in style:
            return "caption"
        if "Source" in style:
            return "source"
        if "Disclaimer" in style or "Disclosure" in style:
            return "disclosure"
        return "body"

    @staticmethod
    def heading_level(style: str | None) -> int:
        if style == "SECTIONChapter":
            return 1
        m = HEADING_RE.match(style or "")
        return int(m.group(1)) if m else 2

    # ------------------------------------------------------------------ drawings & assets
    def store_asset(self, part: str) -> str | None:
        if not part or not self.pkg.has(part):
            return None
        data = self.pkg.read(part)
        sha = hashlib.sha1(data).hexdigest()[:16]
        ext = posixpath.splitext(part)[1].lower().lstrip(".")
        if sha not in self.assets:
            fname = f"{sha}.{ext}"
            with open(os.path.join(self.assets_dir, fname), "wb") as fh:
                fh.write(data)
            info = {"file": fname, "ext": ext, "bytes": len(data), "part": part, "doc_id": self.doc_id}
            if ext == "emf":
                recs = emf_text_records(data)
                info["emf_text"] = summarise_emf_text(recs)
                try:
                    info["digitised"] = digitise(data)  # None when the EMF is not a vector chart
                except Exception as ex:  # noqa: BLE001
                    self.warnings.append(f"emf digitise failed {part}: {ex}")
            self.assets[sha] = info
        return sha

    def parse_drawing(self, drawing) -> list[dict]:
        out = []
        for container in list(drawing.findall("wp:inline", NS)) + list(drawing.findall("wp:anchor", NS)):
            docpr = container.find("wp:docPr", NS)
            extent = container.find("wp:extent", NS)
            base = {
                "placement": "inline" if container.tag == q("wp:inline") else "floating",
                "name": docpr.get("name") if docpr is not None else None,
                "alt_text": (docpr.get("descr") or None) if docpr is not None else None,
                "title": (docpr.get("title") or None) if docpr is not None else None,
                "extent_emu": [int(extent.get("cx")), int(extent.get("cy"))] if extent is not None else None,
            }
            graphic = container.find("a:graphic/a:graphicData", NS)
            if graphic is None:
                out.append({**base, "kind": "unknown"})
                continue
            out.extend(self.parse_graphic_data(graphic, base))
        return out

    def parse_graphic_data(self, gd, base: dict) -> list[dict]:
        uri = gd.get("uri", "")
        out = []
        if uri.endswith("/picture"):
            out.append({**base, "kind": "picture", **self.parse_pic(gd)})
        elif uri.endswith("/chart"):
            c = gd.find("c:chart", NS)
            rid = c.get(q("r:id")) if c is not None else None
            part = self.doc_rels.target(rid) if rid else None
            out.append({**base, "kind": "chart", "chart_part": part, "chart": self.get_chart(part)})
        elif uri.endswith("/wordprocessingShape"):
            out.append({**base, "kind": "shape", **self.parse_shape(gd.find("wps:wsp", NS))})
        elif uri.endswith("/wordprocessingGroup"):
            grp = gd.find("wpg:wgp", NS)
            children = []
            if grp is not None:
                for w in grp.findall("wps:wsp", NS):
                    children.append({"kind": "shape", **self.parse_shape(w)})
                for p in grp.iter(q("pic:pic")):
                    children.append({"kind": "picture", **self.parse_pic(p.getparent() if p.getparent() is not None else p)})
            out.append({**base, "kind": "group", "children": children})
        elif uri.endswith("/diagram"):
            out.append({**base, "kind": "smartart"})
        else:
            out.append({**base, "kind": "unknown", "uri": uri})
        return out

    def parse_pic(self, gd) -> dict:
        blip = gd.find(".//a:blip", NS)
        info = {}
        if blip is not None:
            rid = blip.get(q("r:embed")) or blip.get(q("r:link"))
            part = self.doc_rels.target(rid) if rid else None
            info["asset"] = self.store_asset(part)
            svg = blip.find(".//asvg:svgBlip", NS)
            if svg is not None:
                srid = svg.get(q("r:embed"))
                info["svg_asset"] = self.store_asset(self.doc_rels.target(srid)) if srid else None
        return info

    def parse_shape(self, wsp) -> dict:
        if wsp is None:
            return {}
        geom = wsp.find(".//a:prstGeom", NS)
        fill = wsp.find("wps:spPr/a:solidFill//a:srgbClr", NS)
        line = wsp.find("wps:spPr/a:ln//a:srgbClr", NS)
        scheme_fill = wsp.find("wps:spPr/a:solidFill//a:schemeClr", NS)
        text = " ".join(t.text or "" for t in wsp.iter(q("w:t"))).strip()
        return {
            "geometry": geom.get("prst") if geom is not None else None,
            "fill": color_name(fill.get("val")) if fill is not None else (scheme_fill.get("val") if scheme_fill is not None else None),
            "line": color_name(line.get("val")) if line is not None else None,
            "text": text or None,
        }

    def parse_vml(self, pict) -> list[dict]:
        out = []
        for im in pict.iter(q("v:imagedata")):
            rid = im.get(q("r:id"))
            part = self.doc_rels.target(rid) if rid else None
            out.append({"kind": "picture", "placement": "vml", "asset": self.store_asset(part),
                        "alt_text": im.get(q("o:title")) if im.get(q("o:title")) else None})
        if not out:
            shapes = list(pict.iter(q("v:shape"))) + list(pict.iter(q("v:rect")))
            for s in shapes:
                text = " ".join(t.text or "" for t in s.iter(q("w:t"))).strip()
                out.append({"kind": "shape", "placement": "vml", "geometry": s.tag.split("}")[1],
                            "fill": s.get("fillcolor"), "text": text or None})
        return out

    def get_chart(self, part: str | None) -> dict | None:
        if not part or not self.pkg.has(part):
            return None
        if part not in self.charts:
            try:
                self.charts[part] = parse_chart(self.pkg, part)
            except Exception as ex:  # noqa: BLE001
                self.warnings.append(f"chart parse failed {part}: {ex}")
                self.charts[part] = {"part": part, "error": str(ex), "series": [], "kinds": [], "axes": {}}
        return self.charts[part]

    # ------------------------------------------------------------------ tables
    def _cell_children(self, container, cell: dict, depth: int) -> None:
        """Collect paragraphs and nested tables from a cell, descending into content controls."""
        for child in container:
            if not isinstance(child.tag, str):
                continue
            if child.tag == q("w:p"):
                para = self.parse_paragraph(child)
                if para.drawings:
                    cell["drawings"].extend(para.drawings)
                    para.drawings = []
                    if not para.text and para.role in ("body", "placeholder", "table_text"):
                        para.role = "placeholder"
                if para.text or para.role == "placeholder":
                    cell["paragraphs"].append(para)
            elif child.tag == q("w:tbl"):
                cell["tables"].append(self.parse_table(child, depth + 1))
            elif child.tag == q("w:sdt"):
                content = child.find("w:sdtContent", NS)
                if content is not None:
                    self._cell_children(content, cell, depth)

    def parse_table(self, tbl, depth: int = 0) -> dict:
        rows = tbl.findall("w:tr", NS)
        cells: list[dict] = []
        occupied: dict[tuple[int, int], dict] = {}
        n_cols = 0
        header_rows = set()
        col_widths = [int(g.get(q("w:w")) or 0) for g in tbl.findall("w:tblGrid/w:gridCol", NS)]
        for ri, tr in enumerate(rows):
            trpr = tr.find("w:trPr", NS)
            if trpr is not None and trpr.find("w:tblHeader", NS) is not None:
                header_rows.add(ri)
            ci = 0
            for tc in tr.findall("w:tc", NS):
                while (ri, ci) in occupied:
                    ci += 1
                tcpr = tc.find("w:tcPr", NS)
                span = int(wval(tcpr.find("w:gridSpan", NS)) or 1) if tcpr is not None else 1
                vmerge = tcpr.find("w:vMerge", NS) if tcpr is not None else None
                shd = tcpr.find("w:shd", NS) if tcpr is not None else None
                fill = color_name(shd.get(q("w:fill"))) if shd is not None else None
                if fill == "white":
                    fill = None
                if vmerge is not None and wval(vmerge) in (None, "continue"):
                    # continuation of the cell above
                    above = occupied.get((ri - 1, ci))
                    if above is not None:
                        above["rowspan"] += 1
                        for k in range(span):
                            occupied[(ri, ci + k)] = above
                        ci += span
                        continue
                cell = {"row": ri, "col": ci, "rowspan": 1, "colspan": span, "paragraphs": [], "tables": [],
                        "drawings": [], "fill": fill}
                self._cell_children(tc, cell, depth)
                cell["text"] = "\n".join(p.text for p in cell["paragraphs"] if p.text)
                cells.append(cell)
                for k in range(span):
                    occupied[(ri, ci + k)] = cell
                ci += span
            n_cols = max(n_cols, ci)
        if len(col_widths) < n_cols:
            col_widths = col_widths + [max(col_widths or [1000])] * (n_cols - len(col_widths))
        return {"n_rows": len(rows), "n_cols": n_cols, "cells": cells, "header_rows": sorted(header_rows), "depth": depth,
                "col_widths": col_widths[:n_cols]}

    @staticmethod
    def cell_items(cell: dict) -> list[tuple[str, object]]:
        """Ordered content of a cell as (kind, payload) with kind in caption/source/note/text/drawing/table."""
        items: list[tuple[str, object]] = []
        for p in cell["paragraphs"]:
            if p.role == "caption":
                items.append(("caption", p))
            elif p.role == "source":
                items.append(("source", p))
            elif p.role == "note":
                items.append(("note", p))
            elif p.role == "placeholder":
                continue
            elif p.text:
                items.append(("text", p))
        for d in cell["drawings"]:
            items.append(("drawing", d))
        for t in cell["tables"]:
            items.append(("table", t))
        return items

    def is_figure_grid(self, table: dict) -> bool:
        has_caption = any(p.role == "caption" for c in table["cells"] for p in c["paragraphs"])
        has_content = any(c["drawings"] or c["tables"] for c in table["cells"])
        has_source = any(p.role == "source" for c in table["cells"] for p in c["paragraphs"])
        if not has_caption:
            return False
        # a data table with a caption row *and* lots of plain text cells is a table, not a figure grid
        plain = sum(1 for c in table["cells"] for p in c["paragraphs"] if p.role in ("text", "body", "table_text") and p.text)
        return (has_content or has_source) and plain <= 2

    def flatten_figure_grid(self, table: dict) -> list[dict]:
        figures: list[dict] = []
        by_col: dict[int, list[dict]] = {}
        for c in table["cells"]:
            by_col.setdefault(c["col"], []).append(c)
        for col, col_cells in sorted(by_col.items()):
            current: dict | None = None
            for cell in sorted(col_cells, key=lambda c: c["row"]):
                for kind, payload in self.cell_items(cell):
                    if kind == "caption":
                        p: Para = payload  # type: ignore[assignment]
                        m = CAPTION_RE.match(p.text)
                        current = {
                            "figure_no": m.group(1).strip() if m else None,
                            "caption": (m.group(2).strip() if m else p.text).strip(),
                            "caption_raw": p.text,
                            "source": None, "note": None, "content": [], "extra_text": [],
                            "_row": cell["row"], "_col": col,
                        }
                        figures.append(current)
                    elif current is None:
                        # content before any caption: open an anonymous figure
                        current = {"figure_no": None, "caption": None, "caption_raw": None, "source": None, "note": None,
                                   "content": [], "extra_text": [], "_row": cell["row"], "_col": col}
                        figures.append(current)
                        self._attach(current, kind, payload)
                    else:
                        self._attach(current, kind, payload)
        figures.sort(key=lambda f: (f["_row"], f["_col"]))
        return figures

    @staticmethod
    def _attach(fig: dict, kind: str, payload) -> None:
        if kind == "source":
            txt = payload.text
            # "Note: ... Source: ..." sometimes share one paragraph
            if "Source:" in txt and not txt.startswith("Source"):
                note, _, src = txt.partition("Source:")
                fig["note"] = (fig["note"] + " " if fig["note"] else "") + note.strip()
                fig["source"] = src.strip()
            else:
                fig["source"] = re.sub(r"^Sources?:\s*", "", txt).strip()
        elif kind == "note":
            fig["note"] = (fig["note"] + " " if fig["note"] else "") + re.sub(r"^Note:\s*", "", payload.text).strip()
        elif kind == "text":
            fig["extra_text"].append(payload.text)
        elif kind == "drawing":
            fig["content"].append({"kind": payload.get("kind"), **{k: v for k, v in payload.items() if k != "kind"}})
        elif kind == "table":
            fig["content"].append({"kind": "table", "table": payload})

    # ------------------------------------------------------------------ serialisation
    def table_to_json(self, t: dict) -> dict:
        return {
            "n_rows": t["n_rows"], "n_cols": t["n_cols"], "header_rows": t["header_rows"], "col_widths": t["col_widths"],
            "cells": [
                {
                    "row": c["row"], "col": c["col"], "rowspan": c["rowspan"], "colspan": c["colspan"],
                    "text": c["text"],
                    **({"fill": c["fill"]} if c["fill"] else {}),
                    **({"symbols": [s for p in c["paragraphs"] for s in p.symbols]} if any(p.symbols for p in c["paragraphs"]) else {}),
                    **({"drawings": c["drawings"]} if c["drawings"] else {}),
                    **({"tables": [self.table_to_json(x) for x in c["tables"]]} if c["tables"] else {}),
                    **({"footnotes": [f for p in c["paragraphs"] for f in p.footnotes]} if any(p.footnotes for p in c["paragraphs"]) else {}),
                }
                for c in t["cells"]
            ],
        }

    def figure_to_json(self, f: dict) -> dict:
        content = []
        for item in f["content"]:
            if item["kind"] == "table":
                content.append({"kind": "table", "table": self.table_to_json(item["table"])})
            else:
                content.append(item)
        return {k: v for k, v in {
            "figure_no": f["figure_no"], "caption": f["caption"], "source": f["source"], "note": f["note"],
            "content": content, "extra_text": f["extra_text"] or None,
        }.items() if v not in (None, [], "")}

    def emit(self, el: dict) -> dict:
        el = {"id": self.next_id(), **el, "section_path": list(self.section_path)}
        self.elements.append(el)
        return el

    # ------------------------------------------------------------------ main walk
    def walk_body(self):
        body = self.pkg.xml("word/document.xml").find("w:body", NS)
        self.walk_children(body)

    def walk_children(self, parent):
        for child in parent:
            if not isinstance(child.tag, str):
                continue
            if child.tag == q("w:p"):
                self.handle_paragraph(child)
            elif child.tag == q("w:tbl"):
                self.handle_table(child)
            elif child.tag == q("w:sdt"):
                content = child.find("w:sdtContent", NS)
                if content is not None:
                    self.walk_children(content)
            elif child.tag == q("w:sectPr"):
                continue

    def handle_paragraph(self, p):
        para = self.parse_paragraph(p)
        if not para.text and not para.drawings:
            return
        if para.role == "heading":
            level = self.heading_level(para.style)
            self.section_path = self.section_path[: max(level - 1, 0)] + [para.text]
            self.emit({"type": "heading", "level": level, "text": para.text, "style": para.style})
            return
        if para.drawings and para.role != "caption":
            # a picture or chart sitting directly in the body, not in a figure grid
            self.emit({"type": "figure", "figure_no": None, "caption": para.text or None, "content": para.drawings,
                       "style": para.style})
            return
        el = {"type": {"list": "list_item", "key_data": "key_data", "caption": "caption", "source": "source",
                       "note": "note", "disclosure": "paragraph", "thesis": "paragraph", "report_title": "title",
                       "report_subtitle": "subtitle", "date": "date", "author": "author"}.get(para.role, "paragraph"),
              "role": para.role, "style": para.style, "text": para.text}
        if para.role == "disclosure":
            el["boilerplate"] = True
        if para.list_level is not None:
            el["list_level"] = para.list_level
        if para.symbols:
            el["symbols"] = para.symbols
        if para.footnotes:
            el["footnotes"] = {f: self.footnotes.get(f) or self.endnotes.get(f.replace("end", "")) for f in para.footnotes}
        if para.links:
            el["links"] = para.links
        self.emit(el)

    def handle_table(self, tbl):
        t = self.parse_table(tbl)
        if self.is_figure_grid(t):
            for f in self.flatten_figure_grid(t):
                self.emit({"type": "figure", **self.figure_to_json(f), "layout": "figure_grid"})
            return
        # caption may be the paragraph just before the table
        caption = None
        if self.elements and self.elements[-1]["type"] == "caption":
            caption = self.elements[-1]["text"]
        texts = [c["text"] for c in t["cells"]]
        emails = [m for txt in texts for m in EMAIL_RE.findall(txt)]
        if emails and len(self.elements) < 3:
            self.emit({"type": "author_block", "analysts": self.parse_analysts(t), "table": self.table_to_json(t)})
            return
        self.emit({"type": "table", "caption": caption, "table": self.table_to_json(t)})

    @staticmethod
    def parse_analysts(t: dict) -> list[dict]:
        out = []
        for c in t["cells"]:
            lines = [ln.strip() for ln in re.split(r"\n|\t|\s\|\s", c["text"]) if ln.strip()]
            name = None
            for ln in lines:
                m = EMAIL_RE.search(ln)
                if m:
                    before = ln[: m.start()].strip(" |,;:-")
                    after = ln[m.end():].strip(" |,;:-")
                    out.append({"name": before or name, "email": m.group(0), "detail": after or None})
                    name = None
                elif name is None and len(ln) < 60 and not re.search(r"\d", ln):
                    name = ln
        return out

    # ------------------------------------------------------------------ metadata
    def header_footer_text(self) -> dict:
        out = {"headers": [], "footers": []}
        for name in sorted(self.pkg.names):
            m = re.fullmatch(r"word/(header|footer)\d*\.xml", name)
            if not m:
                continue
            root = self.pkg.xml(name)
            text = " ".join(self.para_text_simple(p) for p in root.iter(q("w:p"))).strip()
            text = re.sub(r"\s+", " ", text)
            if text:
                out[m.group(1) + "s"].append(text)
        return out

    def build_metadata(self) -> dict:
        md: dict = {"doc_id": self.doc_id, "source_file": os.path.basename(self.path)}
        key_data = {}
        first = self.elements[:40]
        for el in first:
            t = el.get("type")
            if t == "title" and "title" not in md:
                md["title"] = el["text"]
            elif t == "subtitle" and "subtitle" not in md:
                md["subtitle"] = el["text"]
            elif t == "date" and "date" not in md:
                md["date"] = el["text"]
            elif t == "key_data":
                k, _, v = el["text"].partition(":")
                key_data[k.strip().lower().replace(" ", "_")] = v.strip()
            elif t == "author_block":
                md["analysts"] = el["analysts"]
        md.update(key_data)
        hf = self.header_footer_text()
        for h in hf["headers"]:
            if "/" not in h and len(h) < 60:
                md["sector"] = h
        md["report_type"] = "company" if "ticker" in key_data else "macro_or_sector"
        thesis = " ".join(el["text"] for el in first if el.get("role") in ("thesis", "body") and el.get("type") == "paragraph")[:3000]
        m = re.search(r"\b(?:Reiterate|Maintain|Reaffirm|Upgrade to|Downgrade to|Initiate(?:d)? (?:coverage )?(?:at|with))\s+(Buy|Sell|Neutral)\b", thesis)
        if m:
            md["rating"] = m.group(1)
            md["rating_basis"] = "inferred from text"
        else:
            m2 = re.search(r"\b(Buy|Sell|Neutral)\b[- ]rated", thesis)
            if m2 and "ticker" in key_data:
                md["rating"] = m2.group(1)
                md["rating_basis"] = "inferred from text (weak)"
        disclosures = " ".join(el["text"] for el in self.elements if el.get("boilerplate"))
        for key, pat in (("completed_at", r"completed at (\d{4}-\d{2}-\d{2} \d{2}:\d{2})"),
                         ("published_at", r"published at (\d{4}-\d{2}-\d{2} \d{2}:\d{2})"),
                         ("priced_as_of", r"priced as of close (\d{4}-\d{2}-\d{2})")):
            mm = re.search(pat, disclosures)
            if mm:
                md[key] = mm.group(1)
        md["confidential_marking"] = any("Confidential" in f for f in hf["footers"])
        return md

    def stats(self) -> dict:
        from collections import Counter
        c = Counter(el["type"] for el in self.elements)
        figs = [el for el in self.elements if el["type"] == "figure"]
        kinds = Counter(item.get("kind") for f in figs for item in f.get("content", []))
        return {
            "elements": dict(c),
            "figure_content_kinds": dict(kinds),
            "assets": len(self.assets),
            "charts": len(self.charts),
            "text_chars": sum(len(el.get("text", "")) for el in self.elements),
            "boilerplate_paragraphs": sum(1 for el in self.elements if el.get("boilerplate")),
            "footnotes": len(self.footnotes),
            "warnings": self.warnings,
        }

    def run(self) -> dict:
        self.walk_body()
        return {
            "doc_id": self.doc_id,
            "metadata": self.build_metadata(),
            "elements": self.elements,
            "footnotes": self.footnotes,
            "assets": self.assets,
            "stats": self.stats(),
        }


def extract(path: str, out_dir: str, doc_id: str | None = None) -> dict:
    assets_dir = os.path.join(out_dir, "assets")
    return Extractor(path, assets_dir, doc_id).run()
