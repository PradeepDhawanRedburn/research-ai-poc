# Audit PR 3: the nine extractor gaps from the loss report, closed

Before: `02-loss-report.md` (nine items lost or flattened in the extractor JSON). After: `03-loss-report-after.md`
(zero). Same fixtures, same checks, regenerated with `uv run python -m tests.loss_report docs/audit/03-loss-report-after.md`.

| Gap | What changed | Where it lands |
|---|---|---|
| Comments never read | `word/comments.xml` is read; each paragraph with a `w:commentReference` carries `comments: [{id, author, date, text}]` | JSON only. Not in chunks or Markdown: editorial content |
| Tracked deletions dropped silently | `w:del` text is recorded as `tracked.deleted`; `w:ins` text stays in the paragraph text and is also recorded as `tracked.inserted`. A paragraph that is deletion-only is still emitted (empty text) so the record exists | JSON only |
| Header text reached metadata only; footer text not read | Page header and footer text are emitted as elements of type `header` / `footer`, flagged boilerplate, after the body | JSON; Markdown's boilerplate section; not in chunks. The existing use of a short header as the metadata sector is unchanged |
| A body picture lost its Caption paragraph | A `Caption`-styled paragraph immediately before a picture or chart paragraph becomes that figure's `figure_no` and `caption`, as figure grids already did; the standalone caption element is folded in | JSON, Markdown, chunks (figure header and `figure` field) |
| Chart series empty when caches are absent | `charts.py` reads the series values, categories and name from the embedded workbook (`c:externalData` → `word/embeddings/*.xlsx`) through openpyxl when `numCache`/`strCache` are missing, addressed by the same `c:f` formulas. Each series now says `values_source: cache` or `workbook` | JSON, Markdown, chunks, facts |

Two defects found on the way, both in code that ran on every real report:

- `_cache` looked for the reference formula `c:f` as a direct child of `c:val`/`c:cat`/`c:tx`, where it never is
  (it sits inside `numRef`/`strRef`). `categories_formula` and `values_formula` were therefore always null. They are
  now populated; nothing downstream read them before.
- The scatter-chart probe (`xVal`/`yVal`) ran whenever a series had no cached values and overwrote the formula with
  null, which is why the workbook fallback could never have fired. It now runs only when `yVal` exists.

**Regression on the four real reports (offline, vision off):** 467 chunks as before, 121 charts digitised and 14
withheld as before, hygiene clean, retention zero lost evidence, chunk text byte-identical in size (392,399
characters). No real report has comments or tracked changes, so their chunks are unchanged by construction.

**Tests:** `tests/test_fixtures.py` now asserts all thirteen feature checks as present, exact chart values with and
without caches, the `values_source` marker, and that comments, deletions, header and footer text stay out of chunks.
22 tests pass.

**Not done, by decision:** indexing comments and tracked changes. They are captured so a compliance or editorial
view can use them; the chunker leaves them out by default because they are not what the analyst published.
