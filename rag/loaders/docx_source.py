"""Solution 1: load the original Word reports directly.

A deliberately self-contained reader (python-docx + the OOXML underneath), independent of `research_ingest`, so
the comparison with solution 2 shows what the A+ preprocessing buys. What it extracts, in reading order:

- paragraphs, with headings from the template's styles turned into a section breadcrumb;
- Wingdings ticks and crosses as ✓ / ✗ (with green/red when the run is coloured);
- tables as Markdown; tables used as figure grids (caption / picture / source) are walked cell by cell;
- native Word charts ("Excel charts"): chart type, title, series names, categories and cached values from
  the chart XML, so the numbers are exact;
- pictures (PNG/JPEG, and EMF which is how pasted Excel charts arrive): rasterised and described by the vision
  model, with the caption and source as context. The PNG is kept for the answer step.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

import docx
from docx.document import Document as DocxDocument
from lxml import etree

from ..chunking import Block, DocInfo, is_caption
from ..images import RasterImage, describe_images, rasterise

log = logging.getLogger(__name__)

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "v": "urn:schemas-microsoft-com:vml",
}
W = "{%s}" % NS["w"]
R_EMBED = "{%s}embed" % NS["r"]
R_ID = "{%s}id" % NS["r"]
FALLBACK = "{%s}Fallback" % NS["mc"]

SYMBOLS = {"F0FC": "✓", "F0FB": "✗", "F0FE": "☑", "F0FD": "☒", "F0A8": "☐", "F06C": "●", "F0A1": "○"}
WINGDINGS_CHARS = {"ü": "✓", "û": "✗", "þ": "☑", "ý": "☒", "¨": "☐", "l": "●"}
HEADING_RE = re.compile(r"heading\s*(\d)", re.I)


@lru_cache(maxsize=None)
def _compiled(expr: str) -> etree.XPath:
    return etree.XPath(expr, namespaces=NS)


def _xp(el: etree._Element, expr: str) -> list:
    """XPath with our prefixes (python-docx elements override .xpath() with their own namespace map)."""
    return _compiled(expr)(el)


def _colour_name(hex_rgb: str | None) -> str:
    if not hex_rgb or hex_rgb.lower() in ("auto", "000000"):
        return ""
    try:
        r, g, b = (int(hex_rgb[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return ""
    if g > r + 40 and g > b:
        return "green"
    if r > g + 40 and r > b:
        return "red"
    if r > 180 and g > 120 and b < 90:
        return "amber"
    return ""


def _symbol(char: str, run: etree._Element) -> str:
    colour = _colour_name(next(iter(_xp(run, "./w:rPr/w:color/@w:val")), None))
    return f"{char} ({colour})" if colour else char


def _iter_runs(el: etree._Element):
    """w:r descendants in document order, skipping mc:Fallback copies of the same content."""
    for node in el.iter(W + "r"):
        if any(a.tag == FALLBACK for a in node.iterancestors()):
            continue
        yield node


def paragraph_text(p: etree._Element) -> str:
    out = []
    for run in _iter_runs(p):
        font = " ".join(_xp(run, "./w:rPr/w:rFonts/@*")).lower()
        for child in run:
            tag = child.tag
            if tag == W + "t":
                t = child.text or ""
                if "wingdings" in font:
                    t = "".join(_symbol(WINGDINGS_CHARS[ch], run) if ch in WINGDINGS_CHARS else ch for ch in t)
                out.append(t)
            elif tag == W + "sym":
                code = (child.get(W + "char") or "").upper()
                if code in SYMBOLS:
                    out.append(_symbol(SYMBOLS[code], run))
            elif tag in (W + "tab", W + "ptab"):
                out.append(" ")
            elif tag in (W + "br", W + "cr"):
                out.append("\n")
    return re.sub(r"[ \t]+", " ", "".join(out)).strip()


# ----------------------------------------------------------------------------- native charts

def _excel_date(serial: str) -> str:
    return (datetime(1899, 12, 30) + timedelta(days=float(serial))).strftime("%Y-%m-%d")


def _points(ref: etree._Element | None) -> list[str]:
    if ref is None:
        return []
    cache = ref.find(".//c:numCache", NS)
    if cache is None:
        cache = ref.find(".//c:strCache", NS)
    if cache is None:
        lvl = ref.find(".//c:multiLvlStrCache/c:lvl", NS)
        cache = lvl
    if cache is None:
        return []
    fmt = (cache.findtext("c:formatCode", default="", namespaces=NS) or "").lower()
    is_date = cache.tag.endswith("numCache") and any(x in fmt for x in ("yy", "mmm", "dd"))
    n = int(cache.find("c:ptCount", NS).get("val")) if cache.find("c:ptCount", NS) is not None else 0
    vals = [""] * n
    for pt in cache.findall("c:pt", NS):
        i = int(pt.get("idx"))
        v = pt.findtext("c:v", default="", namespaces=NS)
        if is_date:
            try:
                v = _excel_date(v)
            except ValueError:
                pass
        if i >= len(vals):
            vals.extend([""] * (i + 1 - len(vals)))
        vals[i] = v
    return vals


def _num(v: str) -> str:
    try:
        f = float(v)
    except ValueError:
        return v
    return f"{f:.4g}" if abs(f) < 1e6 else f"{f:,.0f}"


def chart_text(xml: bytes) -> str:
    root = etree.fromstring(xml)
    title = " ".join(t.text or "" for t in root.findall("c:chart/c:title//a:t", NS)).strip()
    lines = [f"Chart title: {title}"] if title else []
    plot = root.find("c:chart/c:plotArea", NS)
    if plot is None:
        return "\n".join(lines)
    for group in plot:
        kind = etree.QName(group).localname
        if not kind.endswith("Chart"):
            continue
        sers = group.findall("c:ser", NS)
        lines.append(f"Chart type: {kind}; {len(sers)} series")
        for ser in sers:
            name = " ".join(x.text or "" for x in ser.findall("c:tx//c:v", NS)).strip() or "(unnamed)"
            cats = _points(ser.find("c:cat", NS) if ser.find("c:cat", NS) is not None else ser.find("c:xVal", NS))
            vals = _points(ser.find("c:val", NS) if ser.find("c:val", NS) is not None else ser.find("c:yVal", NS))
            points = [(c or str(i + 1), v) for i, (c, v) in enumerate(zip(cats or [""] * len(vals), vals)) if v != ""]
            lines.append(f"- series {name} " + _series_text(points))
    return "\n".join(lines)


def _series_text(points: list[tuple[str, str]], full_up_to: int = 40) -> str:
    """All points for short series; for long ones (daily/monthly history) a summary that stays one chunk."""
    fmt = lambda pts: ", ".join(f"{c}: {_num(v)}" for c, v in pts)
    if len(points) <= full_up_to:
        return f"({len(points)} points): {fmt(points)}"
    numeric = [(c, float(v)) for c, v in points if re.fullmatch(r"-?\d+(\.\d+)?([eE]-?\d+)?", v)]
    head = f"({len(points)} points, {points[0][0]} to {points[-1][0]})"
    if numeric:
        lo = min(numeric, key=lambda p: p[1])
        hi = max(numeric, key=lambda p: p[1])
        head += f": min {_num(str(lo[1]))} at {lo[0]}; max {_num(str(hi[1]))} at {hi[0]}"
    step = max(1, len(points) // 24)
    return f"{head}; sampled: {fmt(points[:-12:step])}; last 12: {fmt(points[-12:])}"


# ----------------------------------------------------------------------------- walking the body

class _Walker:
    def __init__(self, document: DocxDocument, doc_id: str):
        self.doc = document
        self.part = document.part
        self.doc_id = doc_id
        self.blocks: list[Block] = []
        self.headings: dict[int, str] = {}
        self.pending_images: list[tuple[Block, RasterImage]] = []
        self.info = DocInfo(doc_id)

    # headings and metadata -------------------------------------------------
    def _style(self, p: etree._Element) -> str:
        sid = next(iter(_xp(p, "./w:pPr/w:pStyle/@w:val")), "")
        if not sid:
            return ""
        try:
            return self.doc.styles.get_by_id(sid, 1).name or sid  # 1 = WD_STYLE_TYPE.PARAGRAPH
        except Exception:
            return sid

    def section(self) -> str:
        return " > ".join(self.headings[k] for k in sorted(self.headings))

    def _paragraph(self, p: etree._Element, in_table: bool) -> None:
        text = paragraph_text(p)
        style = self._style(p)
        sl = style.lower()
        if text and not in_table:
            if "subtitle" in sl and not self.info.subtitle:
                self.info.subtitle = text
            elif "title" in sl and "table" not in sl and not self.info.title:
                self.info.title = text
            elif "date" in sl and not self.info.date:
                self.info.date = text
            m = HEADING_RE.search(style)
            if m or ("title" in sl and "subtitle" not in sl and "table" not in sl):
                level = int(m.group(1)) if m else 0
                self.headings = {k: v for k, v in self.headings.items() if k < level}
                self.headings[level] = text
                return
        for el in p.iter():
            if any(a.tag == FALLBACK for a in el.iterancestors()):
                continue
            if el.tag == "{%s}blip" % NS["a"] and el.get(R_EMBED):
                self._image(el.get(R_EMBED))
            elif el.tag == "{%s}imagedata" % NS["v"] and el.get(R_ID):
                self._image(el.get(R_ID))
            elif el.tag == "{%s}chart" % NS["c"] and el.get(R_ID):
                self._chart(el.get(R_ID))
        if text:
            kind = "caption" if is_caption(text) or "title" in sl and in_table else \
                   "source" if re.match(r"^sources?\s*:", text, re.I) else "text"
            self.blocks.append(Block(kind, text, self.section()))

    def _image(self, rid: str) -> None:
        try:
            part = self.part.related_parts[rid]
        except KeyError:
            return
        ext = Path(str(part.partname)).suffix
        img = rasterise(part.blob, ext)
        if img is None or img.too_small:
            return
        b = Block("figure", "", self.section(), image_path=str(img.png_path), meta={"sha": img.sha})
        self.blocks.append(b)
        self.pending_images.append((b, img))

    def _chart(self, rid: str) -> None:
        try:
            part = self.part.related_parts[rid]
        except KeyError:
            return
        try:
            text = chart_text(part.blob)
        except etree.XMLSyntaxError:
            return
        self.blocks.append(Block("chart", text, self.section()))

    # tables ----------------------------------------------------------------
    def _table(self, tbl: etree._Element) -> None:
        if _xp(tbl, ".//a:blip | .//c:chart | .//v:imagedata"):
            self._figure_grid(tbl)
            return
        rows = []
        for tr in tbl.findall("w:tr", NS):
            cells = []
            for tc in tr.findall("w:tc", NS):
                span = int(next(iter(_xp(tc, "./w:tcPr/w:gridSpan/@w:val")), "1"))
                text = " ".join(t for t in (paragraph_text(p) for p in tc.iter(W + "p")) if t)
                cells.extend([text.replace("|", "/").replace("\n", " ")] + [""] * (span - 1))
            if any(cells):
                rows.append(cells)
        if not rows:
            return
        caption = source = ""
        if len(rows) > 1 and sum(bool(c) for c in rows[0]) == 1 and is_caption(next(c for c in rows[0] if c)):
            caption = next(c for c in rows.pop(0) if c)
        if len(rows) > 1 and sum(bool(c) for c in rows[-1]) == 1 and \
                re.match(r"^sources?\s*:", next(c for c in rows[-1] if c), re.I):
            source = next(c for c in rows.pop() if c)
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        md = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
        md += ["| " + " | ".join(r) + " |" for r in rows[1:]]
        text = "\n".join(([f"**{caption}**"] if caption else []) + md + ([source] if source else []))
        self.blocks.append(Block("table", text, self.section(), caption=caption))

    def _figure_grid(self, tbl: etree._Element) -> None:
        """Caption/picture/source grids: walk column by column so each caption meets its own picture."""
        grid: dict[tuple[int, int], etree._Element] = {}
        for ri, tr in enumerate(tbl.findall("w:tr", NS)):
            col = 0
            for tc in tr.findall("w:tc", NS):
                grid[(ri, col)] = tc
                col += int(next(iter(_xp(tc, "./w:tcPr/w:gridSpan/@w:val")), "1"))
        seen = set()
        for (_, _), tc in sorted(grid.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            if id(tc) in seen:
                continue
            seen.add(id(tc))
            self.walk(tc, in_table=True)

    def walk(self, container: etree._Element, in_table: bool = False) -> None:
        for el in container:
            if el.tag == W + "p":
                self._paragraph(el, in_table)
            elif el.tag == W + "tbl":
                self._table(el)
            elif el.tag == W + "sdt":
                content = el.find("w:sdtContent", NS)
                if content is not None:
                    self.walk(content, in_table)


def _attach_captions(blocks: list[Block]) -> list[Block]:
    """Merge caption / source paragraphs into the figure or chart they belong to."""
    out: list[Block] = []
    for i, b in enumerate(blocks):
        if b.kind in ("figure", "chart"):
            # caption: the nearest caption among the previous 3 blocks, not already used by another figure
            for j in range(len(out) - 1, max(-1, len(out) - 4), -1):
                if out[j].kind == "caption":
                    b.caption = out.pop(j).text
                    break
                if out[j].kind in ("figure", "chart", "table"):
                    break
        if b.kind == "source" and out and out[-1].kind in ("figure", "chart") and "source" not in out[-1].meta:
            out[-1].meta["source"] = b.text
            continue
        out.append(b)
    for b in out:
        if b.kind in ("caption", "source"):
            b.kind = "text"
    return out


def load_docx(path: Path, vision: str = "auto") -> tuple[DocInfo, list[Block]]:
    """vision: "none" skips picture descriptions (for inspecting chunks offline); otherwise every picture."""
    document = docx.Document(str(path))
    w = _Walker(document, path.stem)
    w.walk(document.element.body)
    blocks = _attach_captions(w.blocks)

    # describe every picture in one batch, with its caption and the paragraph before it as context
    by_sha: dict[str, RasterImage] = {img.sha: img for _, img in w.pending_images}
    figures, contexts = [], []
    for i, b in enumerate(blocks):
        if b.kind != "figure":
            continue
        before = next((x.text for x in reversed(blocks[:i]) if x.kind == "text"), "")
        figures.append(b)
        contexts.append("\n".join(x for x in (b.caption, b.meta.get("source", ""), before[-600:]) if x))
    descriptions = [""] * len(figures) if vision == "none" else \
        describe_images([(by_sha[b.meta["sha"]], ctx) for b, ctx in zip(figures, contexts)])
    for b, desc in zip(figures, descriptions):
        b.text = "\n".join(x for x in (f"**{b.caption}**" if b.caption else "**Figure**",
                                       f"Image description (vision model): {desc}" if desc else
                                       "(image could not be described)",
                                       b.meta.get("source", "")) if x)
    for b in blocks:
        if b.kind == "chart":
            b.text = "\n".join(x for x in (f"**{b.caption}**" if b.caption else "", "Native chart data:", b.text,
                                           b.meta.get("source", "")) if x)
    if not w.info.title:
        w.info.title = document.core_properties.title or path.stem
    log.info("%s: %d blocks, %d figures, %d native charts", path.name, len(blocks), len(figures),
             sum(b.kind == "chart" for b in blocks))
    return w.info, blocks


def discover(inputs: list[str] | None, default_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for p in (inputs or [str(default_dir)]):
        pp = Path(p)
        paths.extend(sorted(pp.glob("*.docx")) if pp.is_dir() else [pp])
    return [p for p in paths if not p.name.startswith("~$")]  # Word lock files
