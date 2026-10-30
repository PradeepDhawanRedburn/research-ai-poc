# Phase 0, week 1: does Markdown lose the answer? Measured on four reports

**Date:** 25 October 2026 · **Status:** engineer-drafted golden set, not yet checked by analysts · **No LLM involved yet**

## Result in one line

Plain Markdown conversion (pipeline A, MarkItDown) keeps the evidence for **52 of 71** answerable questions; the
structured extraction (pipeline C) keeps **71 of 71**. Every lost question is a native chart, a tick/cross matrix or a
picture chart. Pipeline C also hands the model less text per question (about 1.2k tokens versus 1.8–2.3k).

## What was measured

- **Reports:** RA01220 and RA01734 (Money Map macro notes, April and July 2025), RA02283 (Evolution AB), RA02752 (CCEP site visit).
- **Questions:** 77 drafted, of which 71 are answerable and 6 unanswerable (reserved for the LLM stage). Eight types:
  text fact, table lookup, native chart, picture chart, symbol matrix, relationship, cross-document, unanswerable.
- **Pipelines:** A = MarkItDown with its stock converters; A+ = MarkItDown with our DOCX converter registered as a
  plugin (the chosen direction, 26 Oct); C = the same extractor run directly with structure-aware chunks;
  C+route = C searched with the figure-number metadata the agent layer would use.
  B = Azure Content Understanding was **dropped on 26 Oct**: enabling the service required a text-embedding-large
  deployment for which there is no approval or budget. The adapter is kept in case that changes.
- **Metrics:**
  - *Retained* — all evidence strings for the question are present somewhere in the pipeline's full output.
    This is the direct test of "does the conversion lose the information".
  - *Retrieved@5* — all evidence strings appear in the top-5 chunks a plain BM25 search returns for the question.
    A proxy for retrieval, deliberately crude: no embeddings, no reranker.
  - Token counts with tiktoken cl100k_base.

## Results

| Type | A retained | A retrieved@5 | A+ retained | A+ retrieved@5 | C retrieved@5 | C+route retrieved@5 |
|---|---|---|---|---|---|---|
| chart_native (9) | 0 | 0 | 9 | 8 | 7 | 9 |
| chart_picture (5) | 2 | 2 | 5 | 5 | 5 | 5 |
| symbol_matrix (7) | 0 | 0 | 7 | 4 | 6 | 7 |
| table_lookup (13) | 13 | 12 | 13 | 12 | 12 | 12 |
| text_fact (29) | 29 | 27 | 29 | 29 | 27 | 27 |
| relationship (6) | 6 | 6 | 6 | 6 | 6 | 6 |
| cross_document (2) | 2 | 2 | 2 | 2 | 2 | 2 |
| **All (71)** | **52** | **49** | **71** | **66** | **65** | **68** |

A+ and C retain the same 71/71 (same extractor). A+ is chunked with the generic Markdown chunker, C with
structure-aware chunks; the symbol-matrix retrieval gap (4 vs 6 vs 7) shows what table-aware chunking and
figure routing add on top of the plugin. Pipeline B: dropped, see above.

| Report | A full text (tokens) | C full text (tokens) | A context per question | C context per question |
|---|---|---|---|---|
| RA01220 | 7,768 | 19,277 | 1,808 | 1,195 |
| RA01734 | 8,319 | 26,199 | 1,742 | 1,388 |
| RA02283 | 12,812 | 12,636 | 2,330 | 1,223 |
| RA02752 | 31,307 | 35,243 | 2,247 | 1,336 |

"Context per question" is the average size of the top-5 chunks, which is what a model would read. Pipeline A's
figures exclude the 84+19+17+59 inline base64 images MarkItDown emits; with them the files are far larger.

## Reading the numbers

1. **The Markdown argument is settled by type, not by average.** On prose and simple tables the two pipelines are
   equal. Markdown fails exactly where the Head of Department predicted: charts (0/9 native, 2/5 picture) and the
   colour-coded matrices (0/7). Those are the questions that distinguish a research assistant from a text search.
2. **Why Markdown loses them.** MarkItDown converts prose and tables only. Native Word charts become their caption;
   EMF images are dropped; Wingdings ticks and crosses come out as the letters "ü" and "û" with no colour, which a
   model cannot read as availability; the 29-row country matrix is emitted as one pipe-stuffed cell.
3. **Pipeline C's full text is larger for the macro notes** because 66 charts' series are written out. That is the
   store, not what the model reads; per question it sends about a third less than A.
4. **Retrieval is the next problem, and it is a search problem, not a data problem.** With plain BM25, C missed 6
   of 71 although every answer was present. Row-group chunks with repeated headers (added this week) and
   figure-number routing recovered all but three. The three that remain: two "who wrote this report" questions,
   which should be answered from the metadata field rather than by search, and one "net sales 2024" lookup where
   the summary table loses to many prose mentions of net sales. Hybrid search with embeddings and a reranker, as
   planned on Azure AI Search, is the standard fix and will be measured in week 3.
5. **Fairness caveats.** The golden set was written by an engineer from the extracted content; analysts must check
   it against the published PDFs. Some evidence strings for native charts follow pipeline C's "year: value" rendering;
   alternatives were added, and a stricter check (number adjacent to its category in any format) will replace them.
   Pipeline A was run with default MarkItDown settings and no LLM image captioning; a tuned Markdown pipeline (A+) is
   planned so the original proposal is tested at its best.

## Addendum, 26 Oct: picture charts now carry their values

The remaining weak spot was charts pasted from Excel as EMF pictures, which until now yielded only their axis
labels. These EMFs are vector drawings, so the plotted line and the tick marks are recoverable: `emf_chart.py`
maps the polyline back to values and months through the axis labels. Coverage on the four reports: RA01220 71 of
84 EMFs digitised (181 series), RA01734 18 of 19, RA02752 14 of 34, RA02283 2 of 11 (its EMFs are mostly
screenshots). Accuracy, checked against the same series published as native charts in the July edition:

| Series (April EMF vs July native) | Months compared | Mean error | Max error | Axis range |
|---|---|---|---|---|
| Atlanta Fed wage tracker | 226 | 0.002 | 0.004 | 5.1 |
| Manufacturing ISM | 226 | 0.017 | 0.040 | 29.3 |
| Non-manufacturing ISM | 226 | 0.019 | 0.039 | 29.7 |
| US 10yr–2yr yield curve | 238 | 0.073 | 0.57 | 3.8 |

The first three are exact to the printed precision. The yield-curve pair may not share a data frequency across
editions, so it is reported, not claimed.

Later the same day the digitiser was extended to bar, stacked-bar and stacked-area charts. Fig 84 (Bank of Japan
assets, a stacked monthly bar chart) now reads as two series of 120 months: JGBs ending at ¥5,893,039 (¥100m) in
March 2025, loans at ¥765,237, consistent with the BoJ balance sheet. Coverage is now 135 of 148 EMFs (RA01220 83/84,
RA01734 18/19, RA02752 30/34, RA02283 4/11; the Evolution note's EMFs are mostly screenshots). Still outside reach:
raster images, scatter plots, and the roughly one series in three whose legend swatch could not be matched to a
name (its values are kept, labelled "(unnamed series)"); those still want a vision model or the source workbook.

## What was built this week

- `src/research_ingest/`: OOXML extractor (reading order, heading tree, tables with spans mapped by column x-extent,
  figure grids flattened, native chart series with date conversion, Wingdings+colour symbols, images by hash with EMF
  text records, footnotes, content controls, metadata), renderer to compact text, structure-aware chunker.
  Runs in under a second per report.
- `pipelines/run_markitdown.py` (A) and `pipelines/run_azure_cu.py` (B, skips cleanly without credentials).
- `eval/retention.py`: evidence retention, BM25 retrieval@k with optional metadata routing, token counts.
- `golden/questions.yaml`: 77 draft questions with evidence strings.

## Weeks 2 to 6

| Week | Work | Needs |
|---|---|---|
| 2 | Analysts check and extend the golden set (target ~100, add sector notes); fix extraction gaps they find | 2 analyst-days |
| 2 | Harden the MarkItDown plugin (tests per report family, error handling, packaging for internal PyPI) | — |
| 3 | Hybrid retrieval on Azure AI Search (BM25 + vectors + semantic ranker, metadata filters); re-measure retrieval | Azure AI Search, embeddings model |
| 3 | Vision descriptions for PNG/EMF figures (EMF rendered in a container with LibreOffice), cached by image hash | Azure OpenAI vision model |
| 4 | LLM answer and judge stage: accuracy, citation correctness, refusal on unanswerable questions | Chat model; analyst spot-check |
| 5 | Cost per report ingested and per question; two more report families | Volume figures from Research Ops |
| 6 | Gate G1 report and recommendation | Sponsor |

## Files

- `reports/retention.md` — full comparison including every missed question per pipeline
- `reports/retention_rows.json` — per question, per pipeline rows
- `out/C/<report>/rendered.md` — what pipeline C would show a model; `canonical.json` — the store
