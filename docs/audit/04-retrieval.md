# Audit PR 4: retrieval quality flags (LangChain engine), each one an A/B switch

Every flag is read from `.env`, applied inside `research_qa/lc/index.py`'s `Retriever`, and written into each
evaluation run's `config.json` under `retrieval`, which the Scorecard shows in its caption and the baseline report
can key on. Runs with different flags are compared with `report --run <a>` and `--run <b>`, or side by side on the
Scorecard's run-against-run table.

| Flag | Default | What it does | Where |
|---|---|---|---|
| metadata filters | always on | `retrieve(query, k, doc_id, filters)` applies exact matches on `doc_id`, `kind`, `figure`, `source_file`, `date` to **both** legs: Chroma `where` for the vector leg, the same predicate over the in-memory chunks for BM25. Before, only `doc_id` was filtered | `where_clause`, `matches` |
| parallel legs | always on | the vector and BM25 legs run concurrently; fusion is unchanged and a test proves parallel and sequential give the same order | `Retriever.fused` |
| `RETRIEVAL_FIGURE_BOOST` | 1 | when the question names a figure ("Fig 27", "chart 3"), the chunks of that figure (within the active filters, at most 3) go first in the fused list. This is the fix for questions like EVO-23 where "Fig 2" is a weak search token | `figure_in`, `Retriever.retrieve` |
| `RERANKER` | none | `cohere` reranks the top `RERANK_CANDIDATES` (40) fused candidates with a Cohere Rerank model on a Foundry serverless endpoint (`RERANK_ENDPOINT`, `RERANK_API_KEY`, `RERANK_MODEL`); a failure degrades to the fused order and is recorded on the answer step. The reranker is a protocol, so another implementation is one class | `research_qa/lc/rerank.py` |

Every answer's retrieval step now says what shaped it: "figure boost put 1 figure chunk(s) first", "reranked 40
candidates (cohere)", or the reranker error.

**Azure engine.** `retrieve_body()` in `agent/single_pass.py` now builds the knowledge-base request as a pure function and
sends `filterAddOn: doc_id eq '<id>'` on the Markdown+ knowledge source when a document is named; the Word arm's
Blob-built index has no `doc_id` field, so no filter is sent for it. Agentic retrieval's own hybrid search and
semantic reranker are unchanged.

**Not verified live.** The Cohere client follows the published v2 rerank request and response shape (`model`,
`query`, `documents`, `top_n`; `results[].index`, `relevance_score`) with a bearer key. It has not been exercised
against a deployed endpoint here, and the model id to put in `RERANK_MODEL` must be read from the Foundry portal.
`filterAddOn` follows the Microsoft Learn retrieve reference and has not been sent live here either.

**Not changed.** The context line on each chunk (already present); the Chroma and Azure index schemas; `MAX_DOCS`
(12), which remains the one setting shared with the Azure engine.

**Tests.** `tests/test_retrieval.py` over fake stores: filters reach both legs and compose into the Chroma `$and`
shape; the figure boost orders the named figure first and is off when the flag is off; parallel and sequential
fusion are identical; a plugged reranker sees exactly the bounded candidate list and its order wins; a failing
reranker keeps the fused order and records the error; the flags land in a run's `config.json`; the Azure body
carries the filter only where the field exists. 29 tests pass.

**How to A/B on your side.** Same index, two runs:

```bash
RETRIEVAL_FIGURE_BOOST=0 uv run python -m research_qa eval --arm markdown-plus --mode direct --run boost-off
RETRIEVAL_FIGURE_BOOST=1 uv run python -m research_qa eval --arm markdown-plus --mode direct --run boost-on
uv run python -m research_qa report --run boost-on --k 10
```

(on PowerShell set the variable first, or put it in `.env` between runs). Single-pass mode is the clean comparison
because the agent can compensate for retrieval with extra searches.
