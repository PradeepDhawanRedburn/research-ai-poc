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
