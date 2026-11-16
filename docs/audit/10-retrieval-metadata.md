# Audit PR 10: metadata in retrieval

**Before.** Chunk headers and the two Markdown+ indexes did not expose publication analysts, companies, sectors or
publication type/date as metadata.

**Now.** Every chunk carries the five publication fields in its context header and typed fields. Azure AI Search and
Chroma include them as filterable metadata. Chroma list metadata is membership-filtered on both vector and BM25
results; Azure single-pass and direct retrieval accept additive `filters` arguments and compile escaped OData clauses.
Missing fields in older stored chunks deserialize to empty values. `RETRIEVAL_METADATA_CONTEXT` defaults on and can
disable the new context text for before/after evaluation; its value is recorded in run config.

**Measured offline.** `tests/test_fixtures.py` checks analyst/company context headers. `tests/test_retrieval.py` covers
company-only retrieval, Chroma metadata round-trip, old chunk loading and Azure filter escaping; fake stores are used.

**To measure live.** Re-ingest and rebuild the Markdown+ index, then issue a company-filtered retrieval through the
configured engine:

```bash
uv run python -m research_qa ingest --force --vision none
uv run python -m research_qa index --reset
```

**Not verified here.** Azure's preview knowledge-source filter execution and the updated index schema were not
exercised against a live service. No new dependency was added.