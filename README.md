# research-ai-poc — Phase 0: making research reports AI-queryable

Proof of concept for turning analyst research (Word documents with text, tables, charts, images and
colour-coded symbols) into a form an AI system can search and answer questions from accurately.

Three pipelines are compared on the same four sample reports and the same golden question set:

| Pipeline | What it is | Where |
|---|---|---|
| **A** | Plain Markdown conversion with Microsoft MarkItDown, stock converters (the original proposal) | `pipelines/run_markitdown.py` → `out/A/` |
| **A+** | **The chosen direction.** MarkItDown as the chassis, with our DOCX converter registered as a MarkItDown plugin | `pipelines/run_markitdown_plus.py` → `out/A+/` |
| **C** | The same extractor run directly, with structure-aware chunks (used to study retrieval) | `src/research_ingest/` → `out/C/<doc>/{canonical.json, rendered.md, chunks.jsonl, assets/}` |
| B | Azure AI Content Understanding — **dropped** (the service needs an embeddings deployment we have no budget for). Adapter kept in `pipelines/run_azure_cu.py` | `out/B/` |

A+ and C produce the same text; A+ is how it ships (one Microsoft-supported tool, `pip install` + plugin), C is the
development harness.

## Using the plugin

```python
from markitdown import MarkItDown
md = MarkItDown(enable_plugins=True)           # finds research_ingest via the markitdown.plugin entry point
result = md.convert("report.docx")             # charts, tick/cross matrices and spanned tables survive
print(result.markdown)
```

Or `markitdown --use-plugins report.docx` from the command line once the package is installed (`uv sync` does this).

The evaluation (`eval/retention.py`) needs no LLM. For every golden question it checks whether the
evidence strings the answer rests on (a number, a name, a tick/cross state) survive in each pipeline's
output, and whether a plain BM25 search over that pipeline's chunks brings them into the top-k.

## Setup

```bash
uv sync
```

Corporate TLS inspection: set `UV_SYSTEM_CERTS=1` for `uv add`. tiktoken's encoding file is fetched once
with PowerShell (system certificate store) into `.tiktoken_cache/`; without it token counts fall back to characters/4.

Put the sample `.docx` files in `data/raw/` (ignored by git; the reports are confidential).

## Run

```bash
# Pipeline C: extract  (PowerShell: pass (Get-ChildItem data/raw/*.docx).FullName instead of the glob)
uv run python -m research_ingest.cli extract data/raw/*.docx --out out/C

# Pipeline A: MarkItDown baseline
uv run python pipelines/run_markitdown.py data/raw/*.docx

# Pipeline A+: MarkItDown with our plugin
uv run python pipelines/run_markitdown_plus.py data/raw/*.docx

# Compare
uv run python eval/retention.py --k 5
```

Results land in `reports/retention.md`.

## What pipeline C extracts

Everything is read from the OOXML package; no model is involved at this stage.

- Reading order, heading tree and section breadcrumbs; paragraph roles from the template's styles
  (thesis, body, bullet, caption, source, key data, disclosure boilerplate).
- Tables with row and column spans, including the tiny spacer columns authors use for layout: cells are
  mapped onto visible columns by x-extent, so multi-row headers such as "vs old / FY26E" line up.
- Figure grids (caption / picture / source laid out as a table) flattened into figure objects with a
  position-based ID, because figure numbers repeat within a report.
- Native Word charts: series names, categories (Excel date serials converted) and cached values.
- Symbol fonts: Wingdings ticks and crosses with their run colour, rendered as `✓ (green)` / `✗ (red)`.
- Images saved by content hash. **Excel charts pasted as EMF pictures are digitised** (`emf_chart.py`): the EMF
  is a vector drawing, so the series polyline, the axis tick marks, gridlines and labels are all there. Labels are
  paired with ticks and gridlines, pixels are mapped to values and months, legend swatches give series names,
  and a right-hand axis is honoured for series labelled "(rhs)". Validated against the same series published as
  native charts in the next edition: Atlanta Fed wage tracker max error 0.004 on a 0–8 axis, ISM surveys max
  0.04 on a 30–70 axis, all months within 1% of axis range. Output is labelled approximate. Covers line
  charts, bar and stacked-bar charts (rectangles grouped by brush fill, value = top − bottom through the axis fit)
  and stacked areas (upper minus lower edge). A y-axis must be monotone with regular steps and an area must span a
  quarter of the plot, which keeps maps, scatter markers and data-label boxes out. 135 of the 148 EMFs in the four
  sample reports are digitised; raster EMFs (screenshots) and scatter plots fall back to the text-record description.
  PNG/JPEG/SVG are marked "vision description pending".
- Footnotes, hyperlinks, content controls, headers/footers, publication timestamps, ticker, price,
  target, market cap, analysts, an inferred rating.

Not yet done: EMF→PNG rendering (needs LibreOffice or Inkscape in the container), vision descriptions
of pictures, embedded-workbook range reads, the LLM answer/judge stage.

## Golden questions

`golden/questions.yaml` holds 77 draft questions across eight types (text fact, table lookup, native chart,
picture chart, symbol matrix, relationship, cross-document, unanswerable). They were written by an engineer
from the extracted content and must be checked by analysts against the published PDFs before sign-off.

## Layout

```
src/research_ingest/   package.py (OOXML access), extract.py (canonical model), charts.py, emf.py,
                       render.py (compact text + chunks), cli.py
pipelines/             run_markitdown.py (A), run_azure_cu.py (B)
eval/                  retention.py (evidence retention + BM25 retrieval, token counts)
golden/                questions.yaml
reports/               generated comparison reports
data/raw/              sample reports (git-ignored)
out/                   pipeline outputs (git-ignored)
```
