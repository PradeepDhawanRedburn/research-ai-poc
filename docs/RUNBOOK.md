# Run sheet: bringing the rebuilt system up on Azure

For Pradeep. Every step that needs Azure credentials is here, in order, with the command, what it does, what you
should see, and what to do if you do not. Steps 0 to 2 were run offline on this branch and are only repeated to
confirm; steps 3 onward need `az login`.

**Shortcut:** `scripts/runbook.ps1` runs steps 1 to 6 below in order for the engine named in `.env` (step 4 and `word-sync` are Azure-engine only), stops at the first failure and writes a
`runbook-<timestamp>.log` to send back; `-Full` adds the full evaluation, `-From N` resumes at a step.

Run from the repo root in the environment that has the Azure packages (`uv sync --extra azure`). Where a command may
take more than a minute it says so.

## 0. One-time setup

1. `az logout; az login` and complete the MFA prompt (role assignments and Foundry writes need an MFA session).
2. Roles, once per environment. Pass the names of your resources; the script prints `STORAGE_RESOURCE_ID` at the end.
   ```powershell
   .\scripts\setup_access.ps1 -Subscription <sub-id> -ResourceGroup <rg> -SearchService <search> -FoundryAccount <foundry-resource> -FoundryProject <project> -StorageAccount <storage-account>
   ```
   Expect one line per role; "already exists" is fine. Wait a few minutes before step 3.
3. `.env`: copy `.env.example`, fill the store (`STORAGE_ACCOUNT_URL`, `STORAGE_CONTAINER`, `STORAGE_RESOURCE_ID`), the
   model deployments, `AZURE_SEARCH_ENDPOINT`, `PROJECT_ENDPOINT`, `PROJECT_RESOURCE_ID`. Set `JUDGE_DEPLOYMENT` to a
   different, stronger deployment than `ANSWER_MODEL` (gpt-4.1 or gpt-5.5 if deployed).
4. `prices.yaml`: fill the per-million-token prices for `ANSWER_MODEL` and the vision deployment from the Azure
   OpenAI pricing page (region and deployment type as deployed). Leave empty and the app says "not priced" instead
   of guessing.

## 1. The store

```bash
uv run python -m research_qa upload data/raw/RA01220.docx data/raw/RA01734.docx data/raw/RA02283.docx data/raw/RA02752.docx
```
Expect four `uploaded ... -> raw/...` lines. The container is created if missing.

```bash
uv run python -m research_qa status
```
Expect `Store: https://<account>.blob.core.windows.net/research`, four raw files, nothing else yet. Azure sections
report "not created" rather than errors.

## 2. Ingest (Word → Markdown+, pictures, facts, chunks)

```bash
uv run python -m research_qa ingest
```
About 3 to 5 minutes the first time: one vision call per picture (about 180 pictures across the four reports,
4 at a time), cached in `descriptions/` so a re-run costs nothing. Expect per report:

| report | chunks | figures | charts digitised (withheld) | pictures described |
|---|---|---|---|---|
| RA01220 | about 118 | 88 | 78 (5) | 5 |
| RA01734 | about 114 | 85 | 17 (1) | 1 |
| RA02283 | about 64 | 21 | 4 (0) | 12 |
| RA02752 | about 171 | 65 | 22 (8) | 28 |

The digitised counts must match exactly (121 ok, 14 withheld in total); they are deterministic. Picture counts are a
few higher than the offline run because EMF pictures the digitiser withheld are rasterised here (Pillow present) and
sent to the vision model. Any `warnings` line naming "pillow missing" means the environment lacks Pillow: `uv sync
--extra azure` again.

Regression check, no model involved:
```bash
uv run python -m research_qa retention
```
Expect `ALL 116 | 116/116` retained with zero lost evidence on the verified golden set (122 questions, 116 answerable;
see docs/golden_set.md). Retrieved@5 by plain BM25 was 108 of 116 offline; the misses are ranking, not loss, and the
agent's query planning is what closes them.

## 2c. Choosing the engine (and what a second machine needs)

`RETRIEVAL_BACKEND=azure` runs steps 3 and 4 below. `RETRIEVAL_BACKEND=langchain` replaces them with one command that
builds the two Chroma collections on this machine (the Word arm from the stock MarkItDown conversion, Markdown+ from
the store's chunks); `knowledge`, `agents` and `word-sync` are not used:
```bash
uv run python -m research_qa index --reset
```
On a second machine: the store already holds everything from steps 1 and 2, so do not repeat them. With the Azure
engine there is nothing to bring up (the index, bases and agents are shared). With the LangChain engine run the
`index --reset` above once; Chroma lives under `CHROMA_DIR` on each machine and is rebuilt from the store in about
5 minutes (the four Word files are downloaded for the stock conversion, about 750 chunks are embedded).

Known Azure-engine limit, seen on 31 October 2026: the Blob knowledge source skipped three of the four reports because
they exceed the Basic tier's per-file cap (16 MB; S1 allows 128 MB). `word-sync --wait 0` shows the skipped files in
the indexer history. Either raise the tier for the demo or report the cap as the first finding of the comparison.

## 3. Markdown+ index

```bash
uv run python -m research_qa index
```
About 2 minutes: embeds every chunk (about 470) and pushes them. Expect `index 'research-reports-markdown-plus' ready`
and the chunk count. `--reset` clears the documents first; it never deletes the index (a knowledge source references it).

## 4. Knowledge sources and bases, then agents

```bash
uv run python -m research_qa knowledge
```
Creates, per arm, a knowledge source and a knowledge base. The Word arm's source is an **Azure Blob knowledge
source** over `raw/` in the store: the service creates its own index, skillset and indexer and starts ingesting.
This is the one call built against SDK class names I could not exercise here (no Azure packages in this checkout).
If it fails with an import, attribute or type error, the function falls back to the REST body on its own; if the
REST call is rejected, send me the error text, it is a one-place fix in `research_qa/search/knowledge.py`.

```bash
uv run python -m research_qa status
```
Look at the Word arm's ingestion status: it should move from running to success within about 5 minutes for four
files and show a document count. If it reports an authorisation error, the search service identity lacks Storage Blob
Data Reader on the storage account (step 0.2), or `STORAGE_RESOURCE_ID` is wrong.

```bash
uv run python -m research_qa agents
```
Creates the project connection and the agent for each arm. The Markdown+ agent has three tools (knowledge base over
MCP, `lookup_facts`, `inspect_figure`); the Word agent has one. Expect `agent '...' version 1 ready` twice.

## 5. First answers

```bash
uv run python -m research_qa ask "In Fig 27 of the April Money Map, what is Germany's cyclically adjusted balance forecast for 2030?" --arm markdown-plus --mode agent
```
Expect `-4.7% of GDP`, a `lookup_facts` call in the steps, and a cost line with seconds and tokens. Then the same
question with `--arm word`: the answer comes from the verbalised picture, so it may be approximate or missing; that
difference is the point of the comparison.

```bash
uv run python -m research_qa ask "Which three cans are shown side by side in Fig 22, and what is printed on them?" --arm markdown-plus --mode agent
```
Expect Vive, Old Fanta and New Fanta with the printed text, answered from the description with no `inspect_figure`
call (the v2 description carries the printed text). If the agent still inspects the picture, send me the steps.

```bash
uv run python -m research_qa ask "What did the Money Map expect the ECB to do at its next meeting in April and in July 2025?" --arm markdown-plus --mode direct
```
Expect the cut to 2.0% (April, 5 June meeting) and the hold at 2.0% (July, 24 July meeting), with passages from both
reports. Direct mode should be about 3 seconds.

The first Word-arm answer tells us the field names Azure's pipeline stores for its chunks. If the Word tiles show
passages with empty text or file names, send me one raw passage from `ask --arm word -v`; the parser in
`research_qa/agent/foundry.py` (`add_passages`) and `direct.py` (`passages_from_retrieve`) reads a few likely names
and needs the real one.

## 6. The evaluation: does the rewrite hold up?

```bash
uv run python -m research_qa eval --arm markdown-plus --mode agent --limit 10 --run smoke-<engine>
```
(`<engine>` is azure or langchain; the runbook script names runs this way so the Scorecard shows both engines side by side.)
About 2 minutes. Expect 10 rows and a report in `eval/smoke-<engine>/report.md` in the store. This is the same shape as the
agentic run on `code-v1` that scored 10 of 10; anything below 9 of 10 is a regression to look at before the full run.

```bash
uv run python -m research_qa eval --arm word,markdown-plus --mode agent,direct --run baseline-<engine>
```
About 25 to 35 minutes for 4 combinations × the golden set (resume with `--resume --run baseline-<engine>` after a token
expiry). The report has, per arm and mode: accuracy by question type, evidence found, seconds, tokens by kind, cost
per question and per 100 questions, and the per-question disagreements between arms. Reference points from before
the rewrite: 91% on 88 questions (RAG, both inputs through our extractor), 10 of 10 agentic on the first 10, about
6,500 input tokens a question agentic, about 3,700 RAG. The Word arm has no earlier number: it is the new, honest
baseline.

## 7. The demo

```bash
uv run streamlit run research_qa/app/Overview.py
```
Overview shows the pipeline figure, the five numbers per arm from the latest run and what is in the store. Ask runs
the arms in parallel. Scorecard reads `eval/`. Reports shows each report's conversion. The pages were written against
Streamlit 1.66 and compiled but not rendered here; a screenshot of anything that looks wrong is enough for me to fix it.

## 8. Taking it down and up again

```bash
uv run python -m research_qa down
```
Deletes agents, knowledge bases, sources and the Markdown+ index. The store is never touched; `up` recreates
everything from it in a few minutes, with no vision calls thanks to the description cache.
