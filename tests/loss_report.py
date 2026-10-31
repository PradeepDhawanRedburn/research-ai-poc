"""Write docs/audit/02-loss-report.md: what the current converters keep or lose on the fixtures.

    uv run python -m tests.loss_report
"""
from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

from tests.fixtures import checks
from tests.fixtures.build import build_all


def main(out: Path = Path("docs/audit/02-loss-report.md")) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        f = build_all(Path(tmp))
        rows = {}
        rows.update(checks.features_checks(f["features"]))
        rows.update(checks.chart_checks(f["charts"], "cached"))
        rows.update(checks.chart_checks(f["charts_nocache"], "no cache"))
        rows.update(checks.pptx_checks(f["pptx"]))
    from importlib.metadata import version

    markitdown_v, mammoth_v = version("markitdown"), version("mammoth")
    y = lambda b: "yes" if b else "**no**"  # noqa: E731
    lines = [f"# Audit PR 2: loss report on synthetic fixtures ({date.today().isoformat()})", "",
             "Fixtures are built by `tests/fixtures/build.py` with python-docx and python-pptx, plus raw OOXML for "
             "footnotes, tracked changes, text boxes and Word charts; every expected value is in `EXPECTED` there. "
             "Three converters are compared on the same files: the research extractor's JSON document (the source of "
             "truth for chunks and facts), the Markdown rendered from that JSON, and stock MarkItDown "
             f"{markitdown_v} (mammoth {mammoth_v}) with plugins disabled.", "",
             "| feature | extractor JSON | our Markdown | stock MarkItDown | note |", "|---|---|---|---|---|"]
    for k, (a, b, c, note) in rows.items():
        lines.append(f"| {k} | {y(a)} | {y(b)} | {y(c)} | {note} |")
    lost = [k for k, v in rows.items() if not v[0] and not k.startswith("pptx")]
    lines += ["", f"**Lost or flattened in the extractor JSON: {len(lost)}.**" + (" " + "; ".join(lost) + "." if lost else ""), "",
              "`tests/test_fixtures.py` asserts everything marked yes in the extractor JSON column; any gap is carried "
              "as a strict xfail that fails the moment the gap is closed without updating the test. Comments, tracked "
              "deletions, header and footer text are in the JSON by design and excluded from chunks (editorial and "
              "boilerplate), which is why our Markdown shows no for the first two.", ""]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/audit/02-loss-report.md"))
