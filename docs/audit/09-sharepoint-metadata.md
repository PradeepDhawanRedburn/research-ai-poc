# Audit PR 9: SharePoint publication metadata

**Before.** The extractor inferred report metadata from body elements, headers and disclosure text; it did not read
the SharePoint custom XML part embedded in Word reports.

**Now.** The extractor locates a `documentManagement` element in any `customXml/item*.xml` part, normalizes analysts,
sectors and companies to string lists and the publication target date to ISO format, and records the source of each
metadata field. SharePoint values override corresponding inferred values. Missing and malformed blocks leave the
fallback metadata in place and add a manifest warning. The existing rating basis remains available.

**Measured offline and live.** `tests/test_fixtures.py` covers a complete block stored at `item42.xml`, `xsi:nil`
companies, an absent block and malformed XML. The four-report Azure store re-ingest completed with `--vision none`.
Manifest values matched: RA02752 Charlie Higgs / Coca-Cola Europacific Partners; RA02283 Andrew Tam / Evolution AB;
RA01220 and RA01734 Melissa Davies and Michael Taylor / empty companies. All four report `metadata_sources.analysts`
as `sharepoint`; publication dates were normalized to ISO form.

**To measure on the report store.** Re-ingest the four reports and inspect the manifest and facts/chunks:

```bash
uv run python -m research_qa ingest --force --vision none
uv run python -m research_qa status
```

Expected analysts and companies: RA02752, Charlie Higgs / Coca-Cola Europacific Partners; RA02283, Andrew Tam /
Evolution AB; RA01220 and RA01734, Melissa Davies and Michael Taylor / no company.

**Not verified here.** Azure AI Search index filters have not been pushed or exercised live. No new dependency was added.