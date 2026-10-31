# Audit PR 6: content-hash freshness and explicit deletion of removed documents

**Before.** Ingest decided "up to date" by comparing the manifest's ingest time with the raw blob's modified time,
so a re-upload of identical bytes re-ran the whole conversion and vision step, and a blob touched without change
did too. A raw document removed from the store left its Markdown, stock conversion, assets, facts, chunks, manifest
entry and its chunks in both retrieval engines behind, forever: nothing anywhere deleted them.

**Now.**

- **Content hash.** Each manifest entry records `raw_sha`, the SHA-256 of the raw `.docx` it was built from. Ingest
  skips a document when the bytes now in `raw/` hash to the same value, whatever the timestamps say, and re-runs
  when they differ. Entries written before this PR have no hash and fall back to the timestamp rule once; their
  next ingest records the hash. Picture descriptions were already cached by picture content hash and prompt
  version; nothing changed there.
- **Reconcile.** `python -m research_qa reconcile [--dry-run]` lists the documents that have derived data but no
  `raw/<doc>.docx`, and removes for each: `markdown/`, `markdown-stock/`, `facts/`, `chunks/`, `assets/<doc>/`, the
  manifest entry, and the document's chunks from the configured engine (Chroma collections of both arms under
  LangChain; the Markdown+ index under Azure, by `doc_id` filter). Descriptions are shared by picture content and
  are kept. An engine failure is reported per document, never raised, and the store is already clean when it is.
  Nothing runs implicitly: deleting from the index is a decision, so it is a command.

**Azure's own Word index** (the Blob knowledge source) is Azure's: its indexer tracks blob deletions itself only
when a deletion-detection policy is configured on the data source, which ours is not. Reconcile does not reach into
that index; the honest statement is that the Azure Word arm keeps deleted documents until the indexer is reset.
This is noted as a follow-up rather than silently handled.

**Tests.** `tests/test_reconcile.py`: same bytes are current regardless of timestamps and new bytes are not; the
timestamp fallback for hash-less entries; orphan detection; dry run removes nothing; reconcile removes every per-
document object and the manifest entry of the deleted document, keeps the other document and the shared
descriptions, and calls the engine deleter once; an engine failure is reported, not raised; the LangChain
`delete_doc` removes exactly one document's chunks. 39 tests pass.

**Measured end to end (local store, vision off):** a forced ingest of the four reports records four hashes; a
second plain ingest skips all four with "same content hash"; `reconcile --dry-run` finds nothing to remove.
