# Audit PR 11: EMF chart quality gate

**Before.** The digitiser withheld duplicate, positionless, sign-conflicting and unnamed-series readings, but the
manifest did not say which check failed for which figure.

**Now.** The gate itself is unchanged: a chart is withheld (`quality: low`) when a series has no x positions, reads
negative from a non-negative axis, is one of several series with an unnamed member, or no series survives. Each failure
is recorded by name in `digitised.failed_checks`, and the manifest carries `chart_quality_failures`
(`figure`, `caption`, `checks`) plus a warning per withheld figure. A withheld chart keeps its picture/vision path, and
its numbers are not written to facts or indexed chart text.

Positional "lower confidence" notes and dropped duplicate series do **not** withhold a chart: they stay in the
precision note. An earlier version gated on both and withheld 26 of 99 charts in RA01220 and RA01734 instead of 6, with
`lower_confidence` the only cause for 16 of them. That was reverted. There is no on/off switch.

**Measured offline.** `tests/test_fixtures.py` covers each named failure, the non-gating of duplicates, and that
withheld charts yield no facts. `tests/test_chart_quality.py` covers the native/digitised error distribution.

**Measured on the source documents.** Extracting `data/raw/RA01220.docx` and `RA01734.docx` withholds 6 charts in
total, the same count as before this work.

**Not verified here.** A live vision fallback call. The corpus has no comparable native/digitised point pairs, so
`chart-report` error percentiles are N/A.


**To measure on the report store.** Re-ingest, inspect manifest failures, and run the paired comparison:

```bash
uv run python -m research_qa ingest --force --vision none
uv run python -m research_qa chart-report
```

**Not verified here.** A live vision fallback call was not run. The report store has no comparable paired chart points
to calculate an empirical error distribution from.