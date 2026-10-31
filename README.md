# Research reports Q&A

Ask plain-language questions of analyst research reports written in Word and get exact, cited answers: a number
from a chart, a state from a tick/cross matrix, what a picture shows, how a view changed between two editions.

Two ways of getting the Word files into Azure are compared on the same questions, through the same agent:

| Arm | What happens to the Word file | Custom code |
|---|---|---|
| **Word, default conversion** | azure engine: Azure AI Search's own Blob pipeline (cracking, chunking, embeddings, image verbalisation); langchain engine: stock MarkItDown | none |
| **Markdown+** | MarkItDown with the research plugin: charts read as numbers, matrices as states, pictures described once; facts and chunks pushed from the store | the plugin and this package |

Presentation set: [executive overview](docs/EXECUTIVE_OVERVIEW.md) (non-technical: features, flows, diagrams),
[technical architecture](docs/TECHNICAL_ARCHITECTURE.md) (features, flows, configuration, limits) and
[next steps](docs/NEXT_STEPS.md) (roadmap, risks, success criteria, asks).

Design, decisions and the alternatives considered: [docs/DESIGN.md](docs/DESIGN.md). Bringing it up on Azure, step by
step with expected outputs: [docs/RUNBOOK.md](docs/RUNBOOK.md). Roles: `scripts/setup_access.ps1`. Phase 0 evidence:
[docs/history](docs/history).

## How it fits together

```
Blob container (the store)                       Azure AI Search                 Foundry
raw/*.docx ───────────────────────────────────▶ Blob knowledge source ─▶ KB ─┐
   │  python -m research_qa ingest                                           ├─▶ same agent, same model ─▶ answer
   ├─▶ markdown/*.md  assets/  facts/  chunks/ ─▶ Markdown+ index ──────▶ KB ─┘      + lookup_facts, inspect_figure
   └─▶ manifest.json, descriptions/, eval/                                              (Markdown+ only)
```

Everything is read from and written to one Blob container. Nothing on a laptop is needed at run time except `.env`.

Two retrieval engines, one flag: `RETRIEVAL_BACKEND=azure` (AI Search knowledge bases and Foundry agents; the Word
arm is Azure's own Blob ingestion, which skips files above the tier's size cap, 16 MB on Basic) or
`RETRIEVAL_BACKEND=langchain` (Chroma and LangChain on this machine, any tier; the Word arm is then the stock
MarkItDown conversion with no plugin). Same arms, modes, app and evaluation either way; `uv sync --extra azure
--extra langchain` installs both.

## Setup on any machine

The Blob container is the system of record and is shared. Whatever one machine ingested, every other machine reads;
nothing is re-converted. What is per machine is the checkout, `.env`, the sign-in, and, with the LangChain engine,
the Chroma collections (derived data, rebuilt in minutes from the store).

1. Clone, then install. Corporate TLS inspection needs `UV_SYSTEM_CERTS=1` in front of `uv`.
   ```bash
   uv sync --extra azure --extra langchain
   ```
2. Copy `.env.example` to `.env` and fill it in (or copy the `.env` from a machine that already works; it holds no
   per-machine paths). Pick the engine with `RETRIEVAL_BACKEND=azure` or `langchain`. Optional: `prices.yaml` for
   cost per question.
3. Sign in: `az login` (Entra ID everywhere unless `AZURE_OPENAI_API_KEY` is set). Roles are granted once per
   environment, not per machine: `scripts/setup_access.ps1`.
4. Check what is already there:
   ```bash
   uv run python -m research_qa status
   ```
   Four raw files, four manifests and about 470 chunks in the store mean ingest is done; skip it.
5. Bring up the engine you chose. Both read everything from the store.

| | `RETRIEVAL_BACKEND=azure` | `RETRIEVAL_BACKEND=langchain` |
|---|---|---|
| Where retrieval runs | Azure AI Search knowledge bases, Foundry agents | Chroma and LangChain in this process |
| Per-machine bring-up | nothing: the index, knowledge bases and agents are already in Azure if another machine created them (`status` shows them) | `uv run python -m research_qa index --reset` once: builds both collections under `CHROMA_DIR` (default `.cache/chroma`) |
| First time in an environment | `index`, then `up` (knowledge sources, bases, agents); about 5 minutes plus Azure's Word ingestion | the same `index --reset`; about 5 minutes: downloads the four Word files for the stock conversion and embeds about 750 chunks |
| Word arm | Azure's own Blob ingestion; files above the tier's cap (16 MB on Basic) are skipped | stock MarkItDown conversion, no size limit, no plugin |
| Commands not used | | `knowledge`, `agents`, `up`, `word-sync`, `down` (they print a note and exit) |
| Switching engines later | change the flag; the Azure objects stay | change the flag; Chroma stays on disk, no rebuild |

Then the same commands either way:
```bash
uv run python -m research_qa ask "What is the 2030 value for Germany in Fig 27?" --arm markdown-plus --mode agent
uv run python -m research_qa eval --arm word,markdown-plus --mode agent,direct --run <name>
uv run streamlit run research_qa/app/Overview.py
```
Evaluation runs are written to `eval/<run>/` in the store, so they too are shared: the Scorecard on any machine shows
every run. Name runs by engine (`baseline-azure`, `baseline-langchain`) to keep them apart.

`scripts/runbook.ps1` does all of this in order for whichever engine `.env` names, and skips the steps that are
already done in the store; [docs/RUNBOOK.md](docs/RUNBOOK.md) is the same sheet with expected outputs.

## The first time, from Word files

Only when the store is empty (a new container) or a report changes:
```bash
uv run python -m research_qa upload data/raw/*.docx     # Word files into raw/ in the store
uv run python -m research_qa ingest                     # Word -> Markdown+, assets, facts, chunks, manifest (3-5 min, vision calls cached)
uv run python -m research_qa retention                  # no-model check: every golden evidence string is in the Markdown
```
After an ingest, re-run `index` (azure: pushes the new chunks; langchain: `index --reset` rebuilds the collections).

## Adding or removing a report

Add one report; the others are not reconverted (`ingest` skips reports already in the manifest):
```bash
uv run python -m research_qa upload data/raw/<new doc>.docx
uv run python -m research_qa ingest                     # or --doc <report id>; --force re-runs a changed report
uv run python -m research_qa index                      # langchain engine: index --doc <report id>
```
Then add its questions to `golden/questions.yaml`; the evaluation and `retention` only cover documents named there.
On the azure engine the Word arm picks the file up from `raw/` by itself (`word-sync` shows the result). On the
langchain engine every other machine runs `index` once for its own Chroma.

Remove one report: delete `raw/<doc>.docx` from the store (Blob container, or `STORE_LOCAL_DIR/raw`), then
```bash
uv run python -m research_qa reconcile --dry-run        # lists what would go
uv run python -m research_qa reconcile                  # removes its markdown, assets, facts, chunks, manifest entry and index/Chroma chunks
```
and delete its questions from `golden/questions.yaml`. `ingest` never deletes anything; `reconcile` is the only removal path.

## The demo

`streamlit run research_qa/app/Overview.py`. Four pages, written for an executive audience with the engineer's view one
click deeper:

- **Overview**: the question in a paragraph, the latest scorecard as a few numbers per arm, the pipeline in one figure,
  the reports in the store.
- **Ask**: one question, the four routes answering at once in identical tiles (answer, seconds, tokens, cost, sources
  cited, pictures looked at), a side-by-side chart underneath with the best marked. Eight one-click questions from the
  verified golden set, each with its expected answer shown, so the audience can judge the tiles live. Follow-up
  questions continue the agents' conversations.
- **Scorecard**: any evaluation run, headline per arm and mode, by question type, run against run (for instance the
  two engines), question by question with the judge's reason.
- **Reports**: per report, the figures redrawn from the facts file, the pictures with their descriptions, the Markdown+,
  the passages, and what the Word arm sees.

## The pieces

```
research_qa/
  config.py        settings from .env, the two arms and two modes, prices
  storage.py       Store: Blob container or local folder, one API
  schema.py        Chunk, FactFigure, ReportManifest, Answer: what the parts exchange
  ingest/          convert (MarkItDown + plugin) -> describe pictures -> facts -> chunks -> manifest
  search/          Markdown+ index and push; knowledge sources (Blob for Word, index for Markdown+); bases
  agent/           Foundry agent per arm; tools lookup_facts and inspect_figure; direct mode; parallel runner
  evaluate/        golden set, judge, retention check, runner, cost, report
  app/             Streamlit: Overview, Ask, Scorecard, Reports
src/research_ingest/   the Word extractor and the MarkItDown plugin
golden/questions.yaml  122 verified questions with expected answers and evidence strings (docs/golden_set.md)
```

### Markdown+ in one paragraph

The extractor reads the OOXML package: reading order, heading tree, tables with spans, figure grids, native chart
series, Wingdings ticks and crosses with their colour, images by content hash. Excel charts pasted as EMF are vector
drawings, so their series are digitised from the drawing (validated to within 1% of axis range against the same series
published natively later); a quality gate withholds doubtful readings and sends the picture to the vision model instead.
Pictures are described once, with a prompt written for the questions people ask of pictures (items, printed text,
counts). The Markdown carries a metadata block; the facts file carries every series point and matrix cell; chunks are
structure-aware with a context header.

### Evaluation

`eval` runs the golden questions through any arm × mode, judges each answer against the expected one with a separate
model, checks whether the evidence strings were retrieved, and records seconds by phase, tokens by kind (input,
output, reasoning, vision) and cost from `prices.yaml`. Runs land in `eval/<run>/` in the store and appear on the
app's Scorecard page. The no-LLM retention check (`evaluate/retention.py`) is the cheapest proof that the conversion
lost nothing: every evidence string must be present in the Markdown.

## Development

A local folder can stand in for the Blob container: set `STORE_LOCAL_DIR=.cache/store`. The ingest pipeline then
runs fully offline with `--vision none`; the Azure parts report what is missing instead of failing.
