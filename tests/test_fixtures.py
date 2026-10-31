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
           "captioned picture: image saved"]
GAPS = {"comment text": "PR 3: read comments.xml", "tracked deletion (recorded as deleted)": "PR 3: surface deletions",
        "header text": "PR 3: emit header text", "footer text": "PR 3: emit footer text",
        "captioned picture: caption linked (figure_no)": "PR 3: link a body figure to the Caption paragraph before it"}


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


def test_chart_values_without_cache(fixtures):
    res = checks.chart_checks(fixtures["charts_nocache"], "no cache")
    exact = [k for k, v in res.items() if "exact" in k and v[0]]
    if exact:
        pytest.fail("workbook fallback now works: turn this test into a plain assertion")
    pytest.xfail("PR 3: read series from the embedded workbook when caches are absent")


def test_pptx_control_converts(fixtures):
    res = checks.pptx_checks(fixtures["pptx"])
    assert res, "stock MarkItDown did not return anything for the PPTX control"


def test_expected_is_self_consistent():
    for _kind, _caption, cats, series in EXPECTED["charts"]:
        for _name, values in series:
            assert len(values) == len(cats)
