"""What the current converters make of the fixtures: one record per feature, used by the tests and the loss report.

Each check returns (in_json, in_markdown, in_stock, note): whether the feature's known content is present in the
extractor's JSON document, in the Markdown rendered from it, and in stock MarkItDown's Markdown of the same file.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .build import EXPECTED


def extract_doc(path: Path) -> dict:
    from research_ingest.extract import extract

    with tempfile.TemporaryDirectory() as tmp:
        return extract(str(path), tmp, doc_id=path.stem)


def our_markdown(doc: dict) -> str:
    from research_ingest.render import render_document

    return render_document(doc)


def stock_markdown(path: Path) -> str:
    from markitdown import MarkItDown

    return MarkItDown(enable_plugins=False).convert(str(path)).markdown or ""


def json_text(doc: dict) -> str:
    """Every string in the JSON document's content elements (not metadata or stats), so presence checks do not
    depend on which element a feature was filed under, but do require it to be content a chunk can carry."""
    return json.dumps(doc.get("elements", []), ensure_ascii=False)


def elements(doc: dict, kind: str) -> list[dict]:
    return [e for e in doc.get("elements", []) if e.get("type") == kind]


def chart_series(doc: dict) -> list[dict]:
    """Every parsed chart series in reading order."""
    out = []
    for f in elements(doc, "figure"):
        for item in f.get("content", []):
            if item.get("kind") == "chart" and item.get("chart"):
                for s in item["chart"]["series"]:
                    out.append({"name": s.get("name"), "categories": s.get("categories"), "values": s.get("values"),
                                "external_data": item["chart"].get("external_data")})
    return out


def features_checks(path: Path) -> dict[str, tuple[bool, bool, bool, str]]:
    doc = extract_doc(path)
    j, md, st = json_text(doc), our_markdown(doc), stock_markdown(path)
    e = EXPECTED

    def has(s: str) -> tuple[bool, bool, bool]:
        return s in j, s in md, s in st

    out: dict[str, tuple[bool, bool, bool, str]] = {}
    heads = [x for x in elements(doc, "heading")]
    ok = [h["text"] for h in heads] == e["headings"] and [h["level"] for h in heads] == [1, 2]
    out["headings with levels"] = (ok, all(h in md for h in e["headings"]), all(h in st for h in e["headings"]),
                                   f"levels {[h.get('level') for h in heads]}")
    items = elements(doc, "list_item")
    ok = [i.get("list_level") for i in items] == [0, 1]
    out["nested list (levels 0 and 1)"] = (ok, all(x in md for x in e["list"]), all(x in st for x in e["list"]),
                                           f"list_level {[i.get('list_level') for i in items]}")
    tbls = elements(doc, "table")
    cells = tbls[0]["table"]["cells"] if tbls else []
    span = any(c.get("colspan", 1) > 1 and e["table"]["merged_header"] in c.get("text", "") for c in cells)
    vmerge = any(c.get("rowspan", 1) > 1 and e["table"]["merged_down"] in c.get("text", "") for c in cells)
    out["table: merged header (colspan)"] = (span, e["table"]["merged_header"] in md, e["table"]["merged_header"] in st,
                                             "colspan recorded" if span else "no colspan > 1")
    out["table: merged down (rowspan)"] = (vmerge, e["table"]["merged_down"] in md, e["table"]["merged_down"] in st,
                                           "rowspan recorded" if vmerge else "no rowspan > 1")
    out["footnote text"] = (*has(e["footnote"]), "")
    out["comment text"] = (*has(e["comment"]), "comments.xml is not read" if e["comment"] not in j else "")
    out["tracked insertion (kept)"] = (*has(e["inserted"]), "")
    d = has(e["deleted"])
    out["tracked deletion (recorded as deleted)"] = (*d, "deleted text is dropped silently" if not d[0] else "")
    meta = json.dumps({k: v for k, v in doc.items() if k != "elements"}, ensure_ascii=False)
    out["header text"] = (*has(e["header"]), "" if e["header"] in j else
                          ("read into metadata, not emitted as content" if e["header"] in meta else "not read"))
    out["footer text"] = (*has(e["footer"]), "" if e["footer"] in j else
                          ("read into metadata, not emitted as content" if e["footer"] in meta else "not read"))
    out["text box content"] = (*has(e["textbox"]), "")
    figs = [f for f in elements(doc, "figure") if any(i.get("kind") == "picture" for i in f.get("content", []))]
    with_asset = bool(figs) and any(i.get("asset") for f in figs for i in f["content"] if i.get("kind") == "picture")
    out["captioned picture: image saved"] = (with_asset, "Image:" in md, "![" in st or "image" in st.lower(), "")
    numbered = bool(figs) and figs[0].get("figure_no") in ("1", "Fig 1")
    out["captioned picture: caption linked (figure_no)"] = (
        numbered, e["caption"] in md, e["caption"] in st,
        "" if numbered else f"figure_no={figs[0].get('figure_no') if figs else None}: a body picture does not take the "
                            "preceding Caption paragraph")
    return out


def chart_checks(path: Path, label: str) -> dict[str, tuple[bool, bool, bool, str]]:
    doc = extract_doc(path)
    md, st = our_markdown(doc), stock_markdown(path)
    found = chart_series(doc)
    out: dict[str, tuple[bool, bool, bool, str]] = {}
    i = 0
    for kind, caption, cats, series in EXPECTED["charts"]:
        for name, values in series:
            s = found[i] if i < len(found) else {}
            i += 1
            names_ok = s.get("name") == name
            cats_ok = [str(c) for c in (s.get("categories") or [])] == cats
            vals_ok = [float(v) for v in (s.get("values") or []) if v is not None] == values
            in_md = name in md and all(f"{v:g}" in md for v in values)
            in_st = name in st and all(f"{v:g}" in st for v in values)
            note = "" if (names_ok and cats_ok and vals_ok) else (
                f"name={s.get('name')!r} categories={s.get('categories')} values={s.get('values')}"
                + (f"; external_data={s.get('external_data')}" if s.get("external_data") else ""))
            out[f"{label}: {kind} chart, series '{name}' exact"] = (names_ok and cats_ok and vals_ok, in_md, in_st, note)
    out[f"{label}: all {sum(len(s) for *_, s in EXPECTED['charts'])} series present"] = (
        len(found) == sum(len(s) for *_, s in EXPECTED["charts"]), True, True, f"{len(found)} parsed")
    return out


def pptx_checks(path: Path) -> dict[str, tuple[bool, bool, bool, str]]:
    st = stock_markdown(path)
    out = {}
    for kind, caption, cats, series in EXPECTED["charts"]:
        for name, values in series:
            ok = name in st and all(f"{v:g}" in st for v in values)
            out[f"pptx control: {kind} chart, series '{name}' values in stock MarkItDown"] = (ok, ok, ok, "")
    return out
