# Audit PR 7: access control on every chunk, enforced at query time

**Before.** None at chunk level. Anyone who could reach the system could retrieve from every report; access was
to the whole system through Azure roles on storage, search and Foundry.

**Now.**

- **Source of truth: `acl.json` in the store.** `{"default": ["public"], "docs": {"RA02283": ["group:gaming",
  "user:alice@example.com"]}}`. A principal is any string an identity carries; `public` means everyone; a document
  absent from `docs` gets `default`. No file means everything is public, which is today's behaviour, so nothing
  changes until someone writes the file.
- **Onto every chunk at index time.** `index` copies the document's list onto each chunk: Chroma metadata `acl`
  (LangChain) and a new filterable `Collection(Edm.String)` field `acl` on the Markdown+ Azure index. The index
  schema change is additive; existing documents carry null until pushed again. Chunks indexed before the file
  existed have no list and count as public until the next `index`; `status` warns when that is the case.
- **Who is asking.** `PRINCIPALS` in `.env` (comma-separated) is the identity of the process: CLI, evaluation and
  the Streamlit app, which shows it in the sidebar as "Acting as". Every answer entry point also takes `principals`
  explicitly, which is what a real sign-in would pass. No principals means anonymous: public reports only.
- **Enforced in every path that reads report data.** LangChain: both legs of the hybrid retriever (the BM25 leg by
  predicate, the vector leg by post-filtering an over-fetched candidate set, because a list-valued field cannot be
  matched exactly in a Chroma where clause), the figure boost, and the facts tool, which withholds rows of reports
  the caller may not read and says so. Azure: `filterAddOn` on the Markdown+ knowledge source combines
  `acl/any(a: search.in(a, 'public,<principals>', ','))` with the document filter. The picture tool already
  resolves only passages the caller retrieved.

**Boundaries, stated.** The Foundry agent calls its knowledge base itself over MCP and our agent definition carries
no per-user parameter, so under the Azure engine only direct mode enforces access; under LangChain both modes do.
The Word arm's Blob-built Azure index has no `acl` field; it is Azure's index, and access there is Azure's.
`PRINCIPALS` is a configuration value, not an authenticated identity; wiring a sign-in (Entra ID through Streamlit's
authentication, or the caller's token in an API) is the step after this one and lands in the same `principals`
argument.

**Tests (`tests/test_acl.py`).** Two users with different group memberships see different reports, a public report
is seen by both, anonymous sees public only; both legs and the boost respect access (the vector leg's post-filter is
tested against a store that returns everything); default principals come from settings; the ACL file's defaults and
lookup; the Chroma metadata round trip; the Azure filter string with quoting; the facts tool withholds hidden
reports' rows. 46 tests pass.

**Not verified live.** The Azure index update with the new field and the `filterAddOn` syntax over a collection
field follow the Azure AI Search OData reference and have not been sent to the service from here.

**To turn it on for the demo:** write `acl.json` to the container, set `PRINCIPALS` in `.env`, run `index` (Azure)
or `index --reset` (LangChain) once, then ask the same question as two different `PRINCIPALS` values.
