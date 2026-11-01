# Audit: final report (31 October 2026)

Eight pull requests, stacked on `code-revisit-requirement`, one per phase of the re-scoped audit
(`00`: Phase 0 findings are in the conversation record and summarised below). Every PR carries tests in `tests/`
and a results note in this folder. The vector stores were not replaced; two fields were added (`acl` on both,
`values_source` on chart series in the JSON), nothing removed.

## What each PR changed, and what was measured

| PR | Branch | Change | Measured here (offline) | To measure live |
|---|---|---|---|---|
| 1 | `audit/01-baseline-report` | `report --run` : recall@k, p50/p95 latency, tokens per question type from a run's rows | 4 tests; runs on the preview rows end to end | `report --run baseline-langchain --k 10` gives the "before" |
| 2 | `audit/02-fixtures` | synthetic DOCX fixtures (merged cells, footnotes, comments, tracked changes, header/footer, text box, charts with and without caches) + PPTX control; loss report | extractor JSON lost 9 of 27 feature checks; stock MarkItDown lost Word chart values entirely but kept PPTX ones | — |
| 3 | `audit/03-extractor-gaps` | comments, tracked deletions, header/footer text, caption linking for body figures, chart values from the embedded workbook; two latent chart-formula defects fixed | loss report 0 lost; four real reports: 467 chunks, 121/14 charts digitised, chunk text identical in size, retention 0 lost | — |
| 4 | `audit/04-retrieval` | filters on both hybrid legs, parallel legs, figure boost, pluggable reranker (Cohere via Foundry), flags recorded per run; Azure `filterAddOn` | 7 tests over fake stores; parallel = sequential fusion | A/B `RETRIEVAL_FIGURE_BOOST=0/1`, single-pass mode; reranker once an endpoint exists |
| 5 | `audit/05-picture-at-answer` | `SINGLE_PASS_IMAGES`: original pictures with the question in single-pass mode, both engines | 5 tests | `--types image,chart_picture` with the flag on, against the baseline |
| 6 | `audit/06-hash-and-deletions` | ingest decides by content hash; `reconcile` removes deleted documents from store and engine | 6 tests; four reports: hashes recorded, all four skipped on re-run, nothing to reconcile | delete one blob, `reconcile --dry-run`, `reconcile` |
| 7 | `audit/07-access-control` | `acl.json` → `acl` on every chunk → enforced in both legs, the boost, the facts tool, the Azure filter; `PRINCIPALS` identity | 7 tests incl. two users with different groups | write `acl.json`, `index --reset`, ask as two `PRINCIPALS` |
| 8 | `audit/08-streaming-and-report` | direct answers stream (both engines), first-token time recorded in rows and the report, the Ask page shows text as it arrives | 5 tests | p95 to first token appears in `report` once a run exists |

51 tests pass (`uv run pytest`); one parametrised test is skipped because its known-gap list is empty.

## Phase 0 findings and where they ended up

| Item | Phase 0 | Now |
|---|---|---|
| Structured intermediate | existed (typed JSON) | unchanged in shape; nine losses closed (PR 3) |
| Chart coverage | native caches read; no workbook fallback; formulas never read | workbook fallback; formulas read; `values_source` per series (PR 3); fixtures prove exact values (PR 2) |
| Hybrid + filters | hybrid both engines; `doc_id` only | filters on five fields, both legs; Azure filter sent (PR 4) |
| Reranker | Azure semantic ranker only | pluggable, off by default, Cohere client written (PR 4) |
| Context line per chunk | existed | unchanged |
| Chart/image chunk + original image | per-figure chunk existed; agent could inspect; direct could not | direct can, behind a flag (PR 5) |
| Caching | descriptions by image hash; documents by timestamp | documents by content hash (PR 6) |
| Deletions | nothing | `reconcile` (PR 6); Azure Blob-built Word index excluded and said so |
| Access control | none | per-chunk ACL, both engines, two-user test (PR 7); Foundry agent path excluded and said so |
| Evaluation and latency | typed golden set, evidence at 12, p95 | recall@k and p95 per type (PR 1); first-token p95 (PR 8) |

## The protocol for before-and-after numbers

The same 122 verified questions, same model, same judge. One run per configuration, then one `report` per run.
Suggested sequence on the machine with the store:

```bash
uv run python -m research_qa report --run baseline-langchain --k 10                 # before everything
uv run python -m research_qa ingest --force && uv run python -m research_qa index --reset
uv run python -m research_qa eval --arm markdown-plus --mode direct,agent --run after-ingest-v3
uv run python -m research_qa report --run after-ingest-v3 --k 10
RETRIEVAL_FIGURE_BOOST=0 uv run python -m research_qa eval --arm markdown-plus --mode direct --run boost-off
uv run python -m research_qa report --run boost-off --k 10
SINGLE_PASS_IMAGES=1 uv run python -m research_qa eval --arm markdown-plus --mode direct --types image,chart_picture --run images-on
uv run python -m research_qa report --run images-on --k 10
```

Each report lands in `eval/<run>/baseline-k10.md` in the store and the Scorecard's run-against-run table shows any
two side by side. The recall table is the retrieval measure; the latency table shows p95 total and, for streamed
direct runs, p95 to first token.

## Decisions taken rather than code written

- **Page-image fallback for chart questions**: not built. The condition in the brief was "if chart queries lag".
  Chart questions are the strongest category for Markdown+ (digitised values, facts lookups); the lagging category
  was pictures, which PR 3's structured descriptions, PR 4's figure boost and PR 5's picture-at-answer address
  directly. Revisit only if the `images-on` run still trails.
- **Comments and tracked changes** are captured and kept out of chunks by default: they are editorial, not what the
  analyst published, and indexing them is a compliance question.
- **A second OOXML parser** was not written; the fixture set showed the existing one was sound and nine fixes away
  from complete.
- **Azure's Blob-built Word index** is Azure's: deletions and access there are not reached by our code, in both
  places the notes say so instead of papering over it.

## Not verified live, in one place

Cohere Rerank client and model id; Azure `filterAddOn` syntax and the `acl` field addition; image content blocks
through LangChain's `HumanMessage` to Azure OpenAI (the same blocks run live through the OpenAI client); whether
Word opens the chart fixtures. Each is one command on the machine with credentials and is listed in its PR note.
