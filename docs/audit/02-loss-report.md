# Audit PR 2: loss report on synthetic fixtures (2026-10-31)

Fixtures are built by `tests/fixtures/build.py` with python-docx and python-pptx, plus raw OOXML for footnotes, tracked changes, text boxes and Word charts; every expected value is in `EXPECTED` there. Three converters are compared on the same files: the research extractor's JSON document (the source of truth for chunks and facts), the Markdown rendered from that JSON, and stock MarkItDown 0.1.9 (mammoth 1.13.0) with plugins disabled.

| feature | extractor JSON | our Markdown | stock MarkItDown | note |
|---|---|---|---|---|
| headings with levels | yes | yes | yes | levels [1, 2] |
| nested list (levels 0 and 1) | yes | yes | yes | list_level [0, 1] |
| table: merged header (colspan) | yes | yes | yes | colspan recorded |
| table: merged down (rowspan) | yes | yes | yes | rowspan recorded |
| footnote text | yes | yes | yes |  |
| comment text | **no** | **no** | **no** | comments.xml is not read |
| tracked insertion (kept) | yes | yes | yes |  |
| tracked deletion (recorded as deleted) | **no** | **no** | **no** | deleted text is dropped silently |
| header text | **no** | yes | **no** | read into metadata, not emitted as content |
| footer text | **no** | **no** | **no** | not read |
| text box content | yes | yes | **no** |  |
| captioned picture: image saved | yes | yes | yes |  |
| captioned picture: caption linked (figure_no) | **no** | yes | yes | figure_no=None: a body picture does not take the preceding Caption paragraph |
| cached: bar chart, series 'Revenue' exact | yes | yes | **no** |  |
| cached: bar chart, series 'Cost' exact | yes | yes | **no** |  |
| cached: line chart, series 'Index' exact | yes | yes | **no** |  |
| cached: pie chart, series 'Share' exact | yes | yes | **no** |  |
| cached: all 4 series present | yes | yes | yes | 4 parsed |
| no cache: bar chart, series 'Revenue' exact | **no** | **no** | **no** | name=None categories=[] values=[]; external_data=word/embeddings/Microsoft_Excel_Sheet1.xlsx |
| no cache: bar chart, series 'Cost' exact | **no** | **no** | **no** | name=None categories=[] values=[]; external_data=word/embeddings/Microsoft_Excel_Sheet1.xlsx |
| no cache: line chart, series 'Index' exact | **no** | **no** | **no** | name=None categories=[] values=[]; external_data=word/embeddings/Microsoft_Excel_Sheet2.xlsx |
| no cache: pie chart, series 'Share' exact | **no** | **no** | **no** | name=None categories=[] values=[]; external_data=word/embeddings/Microsoft_Excel_Sheet3.xlsx |
| no cache: all 4 series present | yes | yes | yes | 4 parsed |
| pptx control: bar chart, series 'Revenue' values in stock MarkItDown | yes | yes | yes |  |
| pptx control: bar chart, series 'Cost' values in stock MarkItDown | yes | yes | yes |  |
| pptx control: line chart, series 'Index' values in stock MarkItDown | yes | yes | yes |  |
| pptx control: pie chart, series 'Share' values in stock MarkItDown | yes | yes | yes |  |

**Lost or flattened in the extractor JSON: 9.** comment text; tracked deletion (recorded as deleted); header text; footer text; captioned picture: caption linked (figure_no); no cache: bar chart, series 'Revenue' exact; no cache: bar chart, series 'Cost' exact; no cache: line chart, series 'Index' exact; no cache: pie chart, series 'Share' exact.

These are the work items for PR 3. `tests/test_fixtures.py` asserts everything marked yes and carries each gap as a strict xfail that fails the moment the gap is closed without updating the test.
