# Audit PR 2: loss report on synthetic fixtures (2026-10-31)

Fixtures are built by `tests/fixtures/build.py` with python-docx and python-pptx, plus raw OOXML for footnotes, tracked changes, text boxes and Word charts; every expected value is in `EXPECTED` there. Three converters are compared on the same files: the research extractor's JSON document (the source of truth for chunks and facts), the Markdown rendered from that JSON, and stock MarkItDown 0.1.9 (mammoth 1.13.0) with plugins disabled.

| feature | extractor JSON | our Markdown | stock MarkItDown | note |
|---|---|---|---|---|
| headings with levels | yes | yes | yes | levels [1, 2] |
| nested list (levels 0 and 1) | yes | yes | yes | list_level [0, 1] |
| table: merged header (colspan) | yes | yes | yes | colspan recorded |
| table: merged down (rowspan) | yes | yes | yes | rowspan recorded |
| footnote text | yes | yes | yes |  |
| comment text | yes | **no** | **no** |  |
| tracked insertion (kept) | yes | yes | yes |  |
| tracked deletion (recorded as deleted) | yes | **no** | **no** |  |
| header text | yes | yes | **no** |  |
| footer text | yes | yes | **no** |  |
| text box content | yes | yes | **no** |  |
| captioned picture: image saved | yes | yes | yes |  |
| captioned picture: caption linked (figure_no) | yes | yes | yes |  |
| cached: bar chart, series 'Revenue' exact | yes | yes | **no** |  |
| cached: bar chart, series 'Cost' exact | yes | yes | **no** |  |
| cached: line chart, series 'Index' exact | yes | yes | **no** |  |
| cached: pie chart, series 'Share' exact | yes | yes | **no** |  |
| cached: all 4 series present | yes | yes | yes | 4 parsed |
| no cache: bar chart, series 'Revenue' exact | yes | yes | **no** |  |
| no cache: bar chart, series 'Cost' exact | yes | yes | **no** |  |
| no cache: line chart, series 'Index' exact | yes | yes | **no** |  |
| no cache: pie chart, series 'Share' exact | yes | yes | **no** |  |
| no cache: all 4 series present | yes | yes | yes | 4 parsed |
| pptx control: bar chart, series 'Revenue' values in stock MarkItDown | yes | yes | yes |  |
| pptx control: bar chart, series 'Cost' values in stock MarkItDown | yes | yes | yes |  |
| pptx control: line chart, series 'Index' values in stock MarkItDown | yes | yes | yes |  |
| pptx control: pie chart, series 'Share' values in stock MarkItDown | yes | yes | yes |  |

**Lost or flattened in the extractor JSON: 0.**

`tests/test_fixtures.py` asserts everything marked yes in the extractor JSON column; any gap is carried as a strict xfail that fails the moment the gap is closed without updating the test. Comments, tracked deletions, header and footer text are in the JSON by design and excluded from chunks (editorial and boilerplate), which is why our Markdown shows no for the first two.
