# Research reports Q&A

Ask plain-language questions of analyst research reports written in Word and get exact, cited answers: a number
from a chart, a state from a tick/cross matrix, what a picture shows, how a view changed between two editions.

Two ways of getting the Word files into Azure are compared on the same questions, through the same agent:

| Arm | What happens to the Word file | Custom code |
|---|---|---|
| **Word, Azure default** | Azure AI Search's own Blob pipeline: cracking, chunking, embeddings, image verbalisation | none |
| **Markdown+** | MarkItDown with the research plugin: charts read as numbers, matrices as states, pictures described once; facts and chunks pushed from the store | the plugin and this package |

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

## Setup

```bash
uv sync --extra azure
```

Corporate TLS inspection: `UV_SYSTEM_CERTS=1` for `uv`. Copy `.env.example` to `.env` and fill in the store, the
model deployments, the search service and the Foundry project. Sign in with `az login` (Entra ID is used everywhere
unless a key is given). Optional: fill `prices.yaml` so the app and the evaluation show cost per question.

## Run

```bash
uv run python -m research_qa upload data/raw/*.docx     # Word files into raw/ in the store
uv run python -m research_qa ingest                     # Word -> Markdown+, assets, facts, chunks, manifest
uv run python -m research_qa index                      # Markdown+ index (push chunks with embeddings)
uv run python -m research_qa up                         # knowledge sources and bases for both arms, agents
uv run python -m research_qa status                     # what is in the store and in Azure
uv run python -m research_qa ask "What is the 2030 value for Germany in Fig 27?" --arm markdown-plus --mode agent
uv run python -m research_qa eval --arm word,markdown-plus --mode agent,direct
uv run streamlit run research_qa/app/Overview.py        # the demo
```

The Word arm's knowledge source ingests `raw/` on the Azure side; `status` shows its progress. `down` removes
the Azure objects; it never touches the store.

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
golden/questions.yaml  88 questions with expected answers and evidence strings
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
