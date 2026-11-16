# Audit PR 12: stable figure identity

**Before.** Figure lookup and chunk/fact association relied on printed labels, which repeat within a report.

**Now.** Every figure and table receives `doc_id:ordinal:sha1(normalized caption)[:8]` in reading order. Chunks and facts
store `figure_id` while retaining `figure_label` and the legacy `figure` label. `lookup_facts` accepts a stable ID or
printed label; a repeated label can be narrowed by caption text and reports when it remains ambiguous. The inspection
tool carries the stable ID and can resolve a label plus caption against stored chunks.

**Measured offline and live.** `tests/test_figure_identity.py` verifies distinct IDs for repeated `Fig 27` labels,
label/caption resolution, ambiguity reporting, and chunk serialization. The live RA01220 chunks contain distinct
IDs `RA01220:26:ee8e98cb` and `RA01220:28:39b9eb5e` for the two printed `Fig 27` figures.

**To measure live.** Re-ingest and rebuild the index, then look up either duplicate figure by label and caption:

```bash
uv run python -m research_qa ingest --force --vision none
uv run python -m research_qa index --reset
```

**Not verified here.** The updated Azure Search index schema has not been pushed live. Existing indexed chunks need
`index --reset` to expose `figure_id` and `figure_label` to the Azure retrieval source.