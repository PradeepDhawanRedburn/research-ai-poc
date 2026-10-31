"""Synthetic Word fixtures with known content, built with python-docx 1.2 and python-pptx 1.0 (verified against the
installed source) plus raw OOXML where those libraries have no API (footnotes, tracked changes, text boxes, charts
in Word).

    features.docx        headings, nested list, merged-cell table, footnote, comment, tracked insertion and deletion,
                         header and footer text, text box, captioned picture
    charts.docx          bar, line and pie charts with cached values and embedded workbooks
    charts_nocache.docx  the same charts with every <c:numCache>/<c:strCache> removed (values only in the workbook)
    charts.pptx          the same three charts in PowerPoint (control)

Every value is in EXPECTED so tests and the loss report compare against the source, not against each other.
"""
from __future__ import annotations

import copy
import io
from pathlib import Path

from lxml import etree

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
}

CHARTS = [  # (kind, caption, categories, [(series name, values)])
    ("bar", "Revenue and cost by quarter", ["Q1", "Q2", "Q3"], [("Revenue", [10.5, 20.25, 30.0]), ("Cost", [5.0, 6.5, 7.75])]),
    ("line", "Index level", ["Jan", "Feb", "Mar", "Apr"], [("Index", [100.0, 101.5, 99.25, 103.0])]),
    ("pie", "Share of sales", ["Europe", "Asia", "Americas"], [("Share", [0.5, 0.3, 0.2])]),
]

EXPECTED = {
    "headings": ["Section one", "Sub section"],
    "paragraph": "Body text in section one.",
    "list": ["Outer item", "Inner item"],
    "table": {"merged_header": "Merged header", "merged_down": "Merged down", "cell": "r2c3"},
    "footnote": "Footnote body text",
    "comment": "Reviewer comment text",
    "inserted": "inserted text",
    "deleted": "deleted text",
    "header": "Fixture header text",
    "footer": "Fixture footer text",
    "textbox": "Text box content",
    "caption": "Captioned picture",
    "charts": CHARTS,
}


def _png() -> bytes:
    from PIL import Image

    im = Image.new("RGB", (40, 30), (200, 80, 40))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _el(xml: str) -> etree._Element:
    nsdecl = " ".join(f'xmlns:{k}="{v}"' for k, v in NS.items())
    return etree.fromstring(f"<root {nsdecl}>{xml}</root>")[0]


def build_features(path: Path) -> None:
    from docx import Document
    from docx.opc.constants import CONTENT_TYPE as CT, RELATIONSHIP_TYPE as RT
    from docx.opc.packuri import PackURI
    from docx.opc.part import Part
    from docx.shared import Inches

    doc = Document()
    doc.sections[0].header.paragraphs[0].text = EXPECTED["header"]
    doc.sections[0].footer.paragraphs[0].text = EXPECTED["footer"]
    doc.add_heading(EXPECTED["headings"][0], level=1)
    p = doc.add_paragraph(EXPECTED["paragraph"])
    # footnote reference on the paragraph; the footnotes part is written by hand (python-docx has no API)
    p._p.append(_el('<w:r><w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr><w:footnoteReference w:id="1"/></w:r>'))
    footnotes = (
        '<w:footnotes xmlns:w="%s">'
        '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
        '<w:footnote w:type="continuationSeparator" w:id="0"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>'
        '<w:footnote w:id="1"><w:p><w:r><w:t>%s</w:t></w:r></w:p></w:footnote></w:footnotes>'
    ) % (NS["w"], EXPECTED["footnote"])
    fn_part = Part(PackURI("/word/footnotes.xml"), CT.WML_FOOTNOTES, footnotes.encode("utf-8"), doc.part.package)
    doc.part.relate_to(fn_part, RT.FOOTNOTES)

    doc.add_heading(EXPECTED["headings"][1], level=2)
    doc.add_paragraph(EXPECTED["list"][0], style="List Bullet")
    doc.add_paragraph(EXPECTED["list"][1], style="List Bullet 2")

    commented = doc.add_paragraph("A sentence that carries a reviewer comment.")
    doc.add_comment(commented.runs, text=EXPECTED["comment"], author="Reviewer", initials="RV")

    tracked = doc.add_paragraph("Kept text ")
    tracked._p.append(_el('<w:ins w:id="10" w:author="Editor" w:date="2026-10-31T10:00:00Z"><w:r><w:t>%s</w:t></w:r></w:ins>'
                          % EXPECTED["inserted"]))
    tracked._p.append(_el('<w:del w:id="11" w:author="Editor" w:date="2026-10-31T10:00:00Z"><w:r><w:delText>%s</w:delText></w:r></w:del>'
                          % EXPECTED["deleted"]))

    t = doc.add_table(rows=3, cols=4)
    t.style = "Table Grid"
    t.cell(0, 0).text = "Label"
    t.cell(0, 1).merge(t.cell(0, 2)).text = EXPECTED["table"]["merged_header"]
    t.cell(0, 3).text = "Last"
    t.cell(1, 0).merge(t.cell(2, 0)).text = EXPECTED["table"]["merged_down"]
    for r in (1, 2):
        for c in (1, 2, 3):
            t.cell(r, c).text = f"r{r}c{c}"

    cap = doc.add_paragraph(f"Fig 1: {EXPECTED['caption']}", style="Caption")
    pic = doc.add_paragraph()
    pic.add_run().add_picture(io.BytesIO(_png()), width=Inches(1.0))

    tb = doc.add_paragraph()
    tb._p.append(_el(
        '<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="1828800" cy="457200"/>'
        '<wp:docPr id="77" name="Text Box 1"/><a:graphic><a:graphicData uri="%s">'
        '<wps:wsp><wps:cNvSpPr txBox="1"/><wps:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="1828800" cy="457200"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></wps:spPr>'
        '<wps:txbx><w:txbxContent><w:p><w:r><w:t>%s</w:t></w:r></w:p></w:txbxContent></wps:txbx>'
        '<wps:bodyPr/></wps:wsp></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>'
        % (NS["wps"], EXPECTED["textbox"])))
    doc.save(str(path))


def _pptx_charts():
    """(Presentation, [(kind, chart_part)]) with the three charts, built by python-pptx."""
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches

    kinds = {"bar": XL_CHART_TYPE.COLUMN_CLUSTERED, "line": XL_CHART_TYPE.LINE_MARKERS, "pie": XL_CHART_TYPE.PIE}
    prs = Presentation()
    parts = []
    for kind, caption, cats, series in CHARTS:
        slide = prs.slides.add_slide(prs.slide_layouts[5])
        slide.shapes.title.text = caption
        data = CategoryChartData()
        data.categories = cats
        for name, values in series:
            data.add_series(name, values)
        gf = slide.shapes.add_chart(kinds[kind], Inches(1), Inches(1.5), Inches(8), Inches(5), data)
        gf.chart.has_title = True
        gf.chart.chart_title.text_frame.text = caption
        parts.append((kind, gf.chart.part))
    return prs, parts


def build_charts(docx_path: Path, pptx_path: Path, nocache_path: Path) -> None:
    prs, parts = _pptx_charts()
    prs.save(str(pptx_path))
    for target, strip in ((docx_path, False), (nocache_path, True)):
        _docx_with_charts(target, parts, strip)


def _docx_with_charts(path: Path, parts, strip_cache: bool) -> None:
    from docx import Document
    from docx.opc.constants import CONTENT_TYPE as CT, RELATIONSHIP_TYPE as RT
    from docx.opc.packuri import PackURI
    from docx.opc.part import Part

    doc = Document()
    doc.add_heading("Charts", level=1)
    for i, ((kind, caption, _cats, _series), (_k, chart_part)) in enumerate(zip(CHARTS, parts), start=1):
        xml = etree.fromstring(chart_part.blob)
        if strip_cache:
            for tag in ("numCache", "strCache"):
                for el in xml.iter("{%s}%s" % (NS["c"], tag)):
                    el.getparent().remove(el)
        xlsx = Part(PackURI(f"/word/embeddings/Microsoft_Excel_Sheet{i}.xlsx"), CT.SML_SHEET,
                    chart_part.chart_workbook.xlsx_part.blob, doc.part.package)
        cpart = Part(PackURI(f"/word/charts/chart{i}.xml"), CT.DML_CHART, b"", doc.part.package)
        rid = cpart.relate_to(xlsx, RT.PACKAGE)
        ext = xml.find("c:externalData", NS)
        if ext is None:
            ext = etree.SubElement(xml, "{%s}externalData" % NS["c"])
            etree.SubElement(ext, "{%s}autoUpdate" % NS["c"]).set("val", "0")
        ext.set("{%s}id" % NS["r"], rid)
        cpart._blob = etree.tostring(xml, xml_declaration=True, encoding="UTF-8", standalone=True)
        drid = doc.part.relate_to(cpart, RT.CHART)
        doc.add_paragraph(f"Fig {i}: {caption}", style="Caption")
        p = doc.add_paragraph()
        p._p.append(_el(
            '<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="5486400" cy="3200400"/>'
            '<wp:docPr id="%d" name="Chart %d"/><a:graphic><a:graphicData uri="%s"><c:chart r:id="%s"/>'
            '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r>' % (100 + i, i, NS["c"], drid)))
    doc.save(str(path))


def build_all(folder: Path) -> dict[str, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    out = {"features": folder / "features.docx", "charts": folder / "charts.docx",
           "charts_nocache": folder / "charts_nocache.docx", "pptx": folder / "charts.pptx"}
    build_features(out["features"])
    build_charts(out["charts"], out["pptx"], out["charts_nocache"])
    return out


if __name__ == "__main__":
    import sys

    for name, p in build_all(Path(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/out")).items():
        print(name, p, p.stat().st_size, "bytes")
