"""Every fixture feature must appear in the extractor's JSON; chart values must equal the source exactly.

Known gaps are marked xfail(strict=True) with the PR that closes them, so the suite is green today and turns red the
moment a gap is fixed without its marker being removed. `python -m tests.loss_report` writes the full table.
"""
from pathlib import Path

import pytest

from tests.fixtures import checks
from tests.fixtures.build import EXPECTED, build_all


@pytest.fixture(scope="session")
def fixtures(tmp_path_factory) -> dict[str, Path]:
    return build_all(tmp_path_factory.mktemp("fixtures"))


@pytest.fixture(scope="session")
def features(fixtures):
    return checks.features_checks(fixtures["features"])


PRESENT = ["headings with levels", "nested list (levels 0 and 1)", "table: merged header (colspan)",
           "table: merged down (rowspan)", "footnote text", "tracked insertion (kept)", "text box content",
           "captioned picture: image saved", "comment text", "tracked deletion (recorded as deleted)", "header text",
           "footer text", "captioned picture: caption linked (figure_no)"]
GAPS: dict[str, str] = {}  # closed in PR 3; a new gap goes here with the PR that will close it


@pytest.mark.parametrize("feature", PRESENT)
def test_feature_in_json(features, feature):
    in_json, _md, _stock, note = features[feature]
    assert in_json, note


@pytest.mark.parametrize("feature", list(GAPS))
def test_known_gap(features, feature):
    in_json, _md, _stock, note = features[feature]
    if in_json:
        pytest.fail(f"{feature} is now extracted: remove it from GAPS and add it to PRESENT")
    pytest.xfail(GAPS[feature] + (f" ({note})" if note else ""))


def test_chart_values_exact(fixtures):
    res = checks.chart_checks(fixtures["charts"], "cached")
    bad = {k: v[3] for k, v in res.items() if not v[0]}
    assert not bad, bad


def test_chart_values_without_cache_come_from_the_workbook(fixtures):
    res = checks.chart_checks(fixtures["charts_nocache"], "no cache")
    bad = {k: v[3] for k, v in res.items() if not v[0]}
    assert not bad, bad
    doc = checks.extract_doc(fixtures["charts_nocache"])
    sources = {s["values_source"] for f in checks.elements(doc, "figure") for i in f["content"]
               if i.get("kind") == "chart" for s in i["chart"]["series"]}
    assert sources == {"workbook"}, sources
    doc = checks.extract_doc(fixtures["charts"])
    sources = {s["values_source"] for f in checks.elements(doc, "figure") for i in f["content"]
               if i.get("kind") == "chart" for s in i["chart"]["series"]}
    assert sources == {"cache"}, sources


def test_editorial_content_stays_out_of_chunks(fixtures):
    """Comments, deletions, header and footer text are in the JSON for audit, never in what gets indexed."""
    from research_qa.ingest.chunks import build_chunks

    doc = checks.extract_doc(fixtures["features"])
    chunks = build_chunks(doc, "FIX", "features.docx", {}, {}, [])
    text = "\n".join(c.text for c in chunks if c.kind != "metadata")
    for key in ("comment", "deleted", "header", "footer"):
        assert EXPECTED[key] not in text, key
    # the one place a short header line is allowed: the metadata chunk's sector field (existing behaviour)
    meta = next(c.text for c in chunks if c.kind == "metadata")
    assert EXPECTED["comment"] not in meta and EXPECTED["deleted"] not in meta and EXPECTED["footer"] not in meta
    for key in ("paragraph", "inserted", "footnote", "textbox"):
        assert EXPECTED[key] in text, key
    comments = [e for e in checks.elements(doc, "paragraph") if e.get("comments")]
    assert comments and comments[0]["comments"][0]["text"] == EXPECTED["comment"]
    assert comments[0]["comments"][0]["author"] == "Reviewer"
    tracked = [e for e in checks.elements(doc, "paragraph") if e.get("tracked")]
    assert tracked and tracked[0]["tracked"] == {"inserted": [EXPECTED["inserted"]], "deleted": [EXPECTED["deleted"]]}
    assert EXPECTED["deleted"] not in tracked[0]["text"] and EXPECTED["inserted"] in tracked[0]["text"]
    fig = next(f for f in checks.elements(doc, "figure") if any(i.get("kind") == "picture" for i in f["content"]))
    assert fig["figure_no"] == "Fig 1" and fig["caption"] == EXPECTED["caption"]
    assert not checks.elements(doc, "caption"), "the linked Caption paragraph is folded into the figure"


def test_pptx_control_converts(fixtures):
    res = checks.pptx_checks(fixtures["pptx"])
    assert res, "stock MarkItDown did not return anything for the PPTX control"


def test_expected_is_self_consistent():
    for _kind, _caption, cats, series in EXPECTED["charts"]:
        for _name, values in series:
            assert len(values) == len(cats)


def _docx_with_custom_xml(path: Path, xml: bytes | None = None):
    from docx import Document
    import zipfile

    Document().save(path)
    if xml is not None:
        with zipfile.ZipFile(path, "a") as package:
            package.writestr("customXml/item42.xml", xml)
    return path


def test_sharepoint_metadata_is_discovered_and_normalized(tmp_path):
    from research_ingest.extract import extract

    xml = b'''<root xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <documentManagement>
        <PublicationID><Value>RA-42</Value></PublicationID>
        <PublicationName><Value>Example Research</Value></PublicationName>
        <PublicationType><Value>Equity Research</Value></PublicationType>
        <PublicationAnalysts><Value>Alex Analyst</Value><Value>Sam Researcher</Value></PublicationAnalysts>
        <PublicationSectors><Value>Beverages</Value></PublicationSectors>
        <PublicationCompanies><Value>Example Co</Value></PublicationCompanies>
        <PublicationTargetDate><Value>2026-11-12T10:00:00Z</Value></PublicationTargetDate>
        <PublicationStatus><Value>Published</Value></PublicationStatus>
        <PublicationVersion><Value>3</Value></PublicationVersion>
      </documentManagement></root>'''
    path = _docx_with_custom_xml(tmp_path / "metadata.docx", xml)
    doc = extract(str(path), str(tmp_path / "out"), doc_id="RA42")
    md = doc["metadata"]

    assert md["publication_id"] == "RA-42"
    assert md["title"] == "Example Research"
    assert md["publication_type"] == "Equity Research"
    assert md["analysts"] == ["Alex Analyst", "Sam Researcher"]
    assert md["sectors"] == ["Beverages"]
    assert md["companies"] == ["Example Co"]
    assert md["published_date"] == md["date"] == "2026-11-12"
    assert md["metadata_sources"]["analysts"] == "sharepoint"
    assert md["metadata_sources"]["title"] == "sharepoint"
    assert not any("documentManagement" in warning for warning in doc["stats"]["warnings"])


def test_sharepoint_nil_companies_and_missing_block_are_graceful(tmp_path):
    from research_ingest.extract import extract

    nil_xml = b'''<root xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <documentManagement><PublicationCompanies xsi:nil="true"/></documentManagement></root>'''
    path = _docx_with_custom_xml(tmp_path / "nil.docx", nil_xml)
    doc = extract(str(path), str(tmp_path / "nil-out"), doc_id="NIL")
    assert doc["metadata"]["companies"] == []

    missing = _docx_with_custom_xml(tmp_path / "missing.docx")
    doc = extract(str(missing), str(tmp_path / "missing-out"), doc_id="MISSING")
    assert any("documentManagement metadata block not found" in w for w in doc["stats"]["warnings"])
    assert doc["metadata"]["analysts"] == []
    assert doc["metadata"]["sectors"] == []
    assert doc["metadata"]["companies"] == []


def test_malformed_sharepoint_xml_falls_back_with_warning(tmp_path):
    from research_ingest.extract import extract

    path = _docx_with_custom_xml(tmp_path / "malformed.docx", b"<documentManagement>")
    doc = extract(str(path), str(tmp_path / "malformed-out"), doc_id="BROKEN")
    assert doc["metadata"]["doc_id"] == "BROKEN"
    assert any("SharePoint metadata XML malformed" in w for w in doc["stats"]["warnings"])


@pytest.mark.parametrize("name,series,expected", [
    ("unnamed_series", [{"name": None, "mark": "line", "points": [("x1", 1), ("x2", 2)], "axis": "left"},
                        {"name": "B", "mark": "line", "points": [("x1", 2), ("x2", 3)], "axis": "left"}],
     "unnamed_series"),
    ("missing_x_positions", [{"name": "A", "mark": "line", "points": [(None, 1), (None, 2)], "axis": "left"}],
     "missing_x_positions"),
    ("axis_sign_conflict", [{"name": "A", "mark": "line", "points": [("x1", -3), ("x2", -2)], "axis": "left"}],
     "axis_sign_conflict"),
    ("no_series", [], "no_series"),
])
def test_digitiser_quality_gate_names_every_failure(name, series, expected):
    from research_ingest.emf_chart import _quality_gate, _vet_series

    vetted, issues, checks = _vet_series(series, [{"labels": ["0", "10", "20"]}], "months")
    issues, checks = _quality_gate(vetted, issues, checks, series)
    assert expected in checks, (name, issues)


def test_duplicates_and_positional_notes_do_not_withhold_a_chart():
    from research_ingest.emf_chart import _quality_gate, _vet_series

    twin = {"name": "A", "mark": "line", "points": [("x1", 1), ("x2", 2)], "axis": "left"}
    vetted, issues, checks = _vet_series([twin, dict(twin)], [{"labels": ["0", "10", "20"]}], "months")
    _, checks = _quality_gate(vetted, issues, checks, [twin, twin])
    assert checks == [] and len(vetted) == 1


def test_low_quality_digitisation_does_not_create_facts():
    from research_qa.ingest.facts import facts_for

    doc = {"assets": {"asset": {"digitised": {"quality": "low", "series": [
        {"name": "Unreliable", "points": [(None, -10), (None, -20)]}]}}},
           "elements": [{"type": "figure", "figure_no": "Fig 4", "caption": "Synthetic", "content": [
               {"kind": "picture", "asset": "asset"}]}]}
    assert facts_for(doc, "RA01220") == []


def test_gate_records_sign_and_missing_x_together():
    from research_ingest.emf_chart import _quality_gate, _vet_series

    series = [{"name": "A", "mark": "line", "points": [(None, -3), (None, -2)], "axis": "left"}]
    vetted, issues, checks = _vet_series(series, [{"labels": ["0", "10", "20"]}], None)
    _, checks = _quality_gate(vetted, issues, checks, series)
    assert {"axis_sign_conflict", "missing_x_positions", "no_series"} <= set(checks)


def test_chunk_header_includes_sharepoint_analysts(tmp_path):
    from research_qa.ingest.chunks import build_chunks
    from research_ingest.extract import extract

    xml = b'''<root><documentManagement><PublicationAnalysts><Value>Alex Analyst</Value></PublicationAnalysts>
        <PublicationCompanies><Value>Example Co</Value></PublicationCompanies></documentManagement></root>'''
    path = _docx_with_custom_xml(tmp_path / "header.docx", xml)
    doc = extract(str(path), str(tmp_path / "header-out"), doc_id="HEADER")
    chunks = build_chunks(doc, "HEADER", "header.docx", {}, {}, [])
    assert all("Analysts: Alex Analyst" in chunk.text and "Companies: Example Co" in chunk.text for chunk in chunks)


def test_manifest_keeps_list_metadata_and_provenance():
    from types import SimpleNamespace

    from research_qa.ingest.pipeline import _manifest_entry

    doc = {"metadata": {"title": "Example", "analysts": ["Alex Analyst"], "companies": ["Example Co"],
                        "sectors": ["Beverages"], "metadata_sources": {"analysts": "sharepoint"}},
           "elements": [], "assets": {}}
    conv = SimpleNamespace(doc=doc, doc_id="EXAMPLE", warnings=[], asset_info={}, assets={},
                           markdown="",)
    manifest = _manifest_entry(conv, {}, [], [], 0)
    assert manifest.metadata["companies"] == ["Example Co"]
    assert manifest.metadata["metadata_sources"]["analysts"] == "sharepoint"


def test_metadata_context_flag_can_disable_retrieval_header(monkeypatch):
    from dataclasses import replace

    import research_qa.ingest.chunks as chunk_module

    monkeypatch.setattr(chunk_module, "settings", replace(chunk_module.settings, retrieval_metadata_context=False))
    text = chunk_module.header({"title": "Example", "analysts": ["Alex"], "companies": ["Example Co"]}, "D1")
    assert "Analysts:" not in text and "Companies:" not in text
