# research-ai-poc — Phase 0: making research reports AI-queryable

Proof of concept for turning analyst research (Word documents with text, tables, charts, images and
colour-coded symbols) into a form an AI system can search and answer questions from accurately.

Three pipelines are compared on the same four sample reports and the same golden question set:

| Pipeline | What it is | Where |
|---|---|---|
| **A** | Plain Markdown conversion with Microsoft MarkItDown, stock converters (the original proposal) | `pipelines/run_markitdown.py` → `out/A/` |
| **A+** | **The chosen direction.** MarkItDown as the chassis, with our DOCX converter registered as a MarkItDown plugin | `pipelines/run_markitdown_plus.py` → `out/A+/` |
| **C** | The same extractor run directly, with structure-aware chunks (used to study retrieval) | `src/research_ingest/` → `out/C/<doc>/{canonical.json, rendered.md, chunks.jsonl, assets/}` |
| B | Azure AI Content Understanding — **dropped** (the service needs an embeddings deployment we have no budget for). Adapter kept in `pipelines/run_azure_cu.py` | `out/B/` |

A+ and C produce the same text; A+ is how it ships (one Microsoft-supported tool, `pip install` + plugin), C is the
development harness.

## Using the plugin

```python
from markitdown import MarkItDown
md = MarkItDown(enable_plugins=True)           # finds research_ingest via the markitdown.plugin entry point
result = md.convert("report.docx")             # charts, tick/cross matrices and spanned tables survive
print(result.markdown)
```

Or `markitdown --use-plugins report.docx` from the command line once the package is installed (`uv sync` does this).

The evaluation (`eval/retention.py`) needs no LLM. For every golden question it checks whether the
evidence strings the answer rests on (a number, a name, a tick/cross state) survive in each pipeline's
output, and whether a plain BM25 search over that pipeline's chunks brings them into the top-k.

## Setup

```bash
uv sync
```

Corporate TLS inspection: set `UV_SYSTEM_CERTS=1` for `uv add`. tiktoken's encoding file is fetched once
with PowerShell (system certificate store) into `.tiktoken_cache/`; without it token counts fall back to characters/4.

Put the sample `.docx` files in `data/raw/` (ignored by git; the reports are confidential).

## Run

```bash
# Pipeline C: extract  (PowerShell: pass (Get-ChildItem data/raw/*.docx).FullName instead of the glob)
uv run python -m research_ingest.cli extract data/raw/*.docx --out out/C

# Pipeline A: MarkItDown baseline
uv run python pipelines/run_markitdown.py data/raw/*.docx

# Pipeline A+: MarkItDown with our plugin
uv run python pipelines/run_markitdown_plus.py data/raw/*.docx

# Compare
uv run python eval/retention.py --k 5
```

Results land in `reports/retention.md`.

## What pipeline C extracts

Everything is read from the OOXML package; no model is involved at this stage.

- Reading order, heading tree and section breadcrumbs; paragraph roles from the template's styles
  (thesis, body, bullet, caption, source, key data, disclosure boilerplate).
- Tables with row and column spans, including the tiny spacer columns authors use for layout: cells are
  mapped onto visible columns by x-extent, so multi-row headers such as "vs old / FY26E" line up.
- Figure grids (caption / picture / source laid out as a table) flattened into figure objects with a
  position-based ID, because figure numbers repeat within a report.
- Native Word charts: series names, categories (Excel date serials converted) and cached values.
- Symbol fonts: Wingdings ticks and crosses with their run colour, rendered as `✓ (green)` / `✗ (red)`.
- Images saved by content hash. **Excel charts pasted as EMF pictures are digitised** (`emf_chart.py`): the EMF
  is a vector drawing, so the series polyline, the axis tick marks, gridlines and labels are all there. Labels are
  paired with ticks and gridlines, pixels are mapped to values and months, legend swatches give series names,
  and a right-hand axis is honoured for series labelled "(rhs)". Validated against the same series published as
  native charts in the next edition: Atlanta Fed wage tracker max error 0.004 on a 0–8 axis, ISM surveys max
  0.04 on a 30–70 axis, all months within 1% of axis range. Output is labelled approximate. Covers line
  charts, bar and stacked-bar charts (rectangles grouped by brush fill, value = top − bottom through the axis fit)
  and stacked areas (upper minus lower edge). A y-axis must be monotone with regular steps and an area must span a
  quarter of the plot, which keeps maps, scatter markers and data-label boxes out. 135 of the 148 EMFs in the four
  sample reports are digitised; raster EMFs (screenshots) and scatter plots fall back to the text-record description.
  PNG/JPEG/SVG are marked "vision description pending".
  **Quality gate.** A digitisation is only written out as numbers when it passes checks a reader cannot make from
  the text: duplicate series (the same shape drawn again as a border) are dropped, series whose points have no x
  position or whose sign contradicts a non-negative axis are dropped, and a chart with several series is withheld
  entirely when one of them could not be matched to a legend entry. A withheld chart gets the line
  `Chart values were not read from the drawing (...)` plus `(vision description pending)`, so the RAG loaders send
  its picture to the vision model instead. On the four reports 121 digitisations pass and 14 are withheld
  (10 for unmatched legends, 3 for sign, 2 for missing x; Fig 4 of RA01220, which used to read as nine copies of a
  negative undated series, is now withheld). The positional notes ("y labels not matched to gridlines") stay in the
  precision note but do not withhold on their own: Fig 84 of RA01220 carries one and its values check out.
- Footnotes, hyperlinks, content controls, headers/footers, publication timestamps, ticker, price,
  target, market cap, analysts, an inferred rating.

Not yet done: EMF→PNG rendering (needs LibreOffice or Inkscape in the container), vision descriptions
of pictures, embedded-workbook range reads, the LLM answer/judge stage.

## Multimodal RAG (`rag/`)

Question answering over the reports, using Azure AI Foundry models (chat, vision and embeddings) through LangChain
and a local Chroma database. There are two solutions with the same chunking, retrieval and answering. Only the input differs:

| Solution | Input | Loader |
|---|---|---|
| `docx` | the original Word files in `data/raw/` | `rag/loaders/docx_source.py`. Reads the docx on its own: headings, tables, figure grids, native chart data, Wingdings ticks. Every picture, including EMF-pasted Excel charts, is rasterised and described by the vision model |
| `markdown` | the A+ output in `out/A+/` | `rag/loaders/markdown_source.py`. Uses the plugin's Markdown as is. The vision model only describes pictures that have no digitised values (the "vision description pending" ones) |

How it works:
- **Ingest.** Blocks are read in document order. Figures, charts and tables each get their own chunk, and every
  chunk starts with the report and section name. Chunks are embedded with the Azure embedding deployment into
  Chroma, with one collection per solution in `.chroma/`.
- **Retrieve.** Vector search and BM25 run over the same chunks and are combined with reciprocal rank fusion.
- **Answer.** The chat model gets the top chunks as text and the pictures behind the top figure chunks as
  images, so it can read a chart directly. Answers cite their sources, and the model says "The documents do not say" when the sources don't answer the question.
- Picture descriptions are cached by image hash in `.rag_cache/`, so the two solutions share them and re-runs cost nothing.
  EMF rendering uses Pillow's GDI backend, which only exists on Windows.

```powershell
uv sync --extra rag                      # or: pip install -e ".[rag]"
copy .env.example .env                   # fill in endpoint, key (or use az login), deployment names

uv run python -m rag inspect --vision none        # optional: write chunks to .rag_cache/ without calling any model
uv run python -m rag ingest --source docx         # or --source markdown, or both (the default)
uv run python -m rag ask "What does the April Money Map expect the Fed to do in May?"   # answers from both solutions
uv run python -m rag ask --source markdown --doc RA02752     # interactive, restricted to one report
uv run python -m rag eval --limit 10              # golden questions -> reports/rag_eval.md (retrieval + LLM judge)
```

The judge is `JUDGE_DEPLOYMENT` (`.env`), shared with `agentic eval` and defaulting to the chat deployment. Set it to a
different, stronger model than the one that answers: a model grading its own answers is lenient with them.

Without uv, create a venv and call its Python instead (`python -m venv .venv`, `.venv\Scripts\pip install -e ".[rag]"`,
then `.venv\Scripts\python -m rag ...`).

In `.env`, `AZURE_OPENAI_ENDPOINT` is the resource root only (`https://<name>.services.ai.azure.com/`), not a full
API URL such as `.../openai/v1/responses`. `AZURE_OPENAI_API_VERSION` is an API version (`2024-10-21`), not the model
version shown on the deployment (`2025-04-14`). Either mistake gives HTTP 404.

### Web UI

A Streamlit page for asking the reports questions. Run `ingest` first: the page reads the Chroma indexes and shows a
warning for a solution that has not been indexed yet.

```powershell
uv run streamlit run rag/app.py                   # or: .venv\Scripts\streamlit run rag/app.py
```

It opens at http://localhost:8501. In the sidebar:
- **Solution**: Word (docx) or MarkItDown (A+). Questions go to the selected one. To compare, ask a question, switch
  the solution and ask it again. Both answers stay in the conversation, each headed with its solution.
- **Report**: limit the search to one report. **Chunks to retrieve**, **Hybrid search** and **Show figure images to the
  model** are the same settings as `--k`, `--no-hybrid` and `--no-images` on `ask`.
- **Reload indexes**: press after re-running `ingest` while the page is open. **Clear conversation** empties the history.

Under each answer, **Sources** lists the retrieved chunks, numbered as in the answer's citations, and **Images shown
to the model** shows the charts that were attached to the question.

## Agentic retrieval (`agentic/`)

A second answering approach, built from the Azure tutorial
[Build an agentic retrieval solution](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-how-to-create-pipeline).
The reports are chunked by `rag/` (same loaders and chunking), embedded and pushed to an **Azure AI Search index**.
A **knowledge source** and **knowledge base** sit on top of it. A **Foundry agent** (`gpt-5-mini`) reaches the
knowledge base through an MCP tool over a project connection, and answers with citations or "I don't know".
Retrieval runs in the search service (hybrid vector + keyword + semantic ranking, query variants), not locally.

| Tutorial step | Command | Code |
|---|---|---|
| Index, upload documents | `index` | `agentic/search_setup.py` |
| Knowledge source, knowledge base | `kb` | `agentic/search_setup.py` |
| Project connection, agent | `agent` | `agentic/foundry.py` |
| Chat | `ask` | `agentic/foundry.py` |
| Clean up | `cleanup` | |

Configuration (`.env`, names as in the tutorial; see `.env.example`):

| Variable | Where to find it |
|---|---|
| `AZURE_SEARCH_ENDPOINT` | search service > Overview |
| `PROJECT_ENDPOINT` | Foundry project > Endpoints (`.../api/projects/<project>`) |
| `PROJECT_RESOURCE_ID` | Foundry project > Properties (resource ID) |
| `AGENT_MODEL` | a chat deployment in the same Foundry resource (`gpt-5-mini`) |
| embedding deployment | `AZURE_EMBEDDING_DEPLOYMENT` is reused; the search service reaches it at `https://<resource>.openai.azure.com`, derived from `AZURE_OPENAI_ENDPOINT` |

The tutorial's `AZURE_OPENAI_ENDPOINT` (`.openai.azure.com`) is derived, not set, because `rag/` already uses that name
for the `.services.ai.azure.com` endpoint; set `AZURE_SEARCH_OPENAI_ENDPOINT` to override.

Search service: `ais-research-agentic-poc`, created for this solution (Basic SKU, France Central, system-assigned identity,
semantic ranker on the free plan). Create it with `az search service create -g <rg> -n <name> -l francecentral --sku basic
--identity-type SystemAssigned --semantic-search free --auth-options aadOrApiKey`. Sweden Central, where the Foundry
resource is, had no capacity when it was created. Do not point `AZURE_SEARCH_ENDPOINT` at a service that other work uses:
`cleanup` deletes the indexes, knowledge bases and agents named here, and `setup_access.ps1` changes its roles and identity.

Access (once). Needs an **MFA** sign-in: a browser `az login` that prompts for MFA (a Windows integrated sign-in has no MFA
claim), because Azure policy refuses writes from a token without it. `agentic/setup_access.ps1` (parameters name the
search service and Foundry resource) enables a managed identity on the search service and assigns the tutorial's roles: you get Search Service Contributor, Search Index Data Contributor and
Reader on the search service plus Foundry User and Foundry Project Manager on the Foundry resource; the project's managed
identity gets Search Index Data Reader; the search service's identity gets Cognitive Services User on the Foundry resource.

```powershell
uv sync --extra rag --extra agentic
az login                                          # MFA, see above
./agentic/setup_access.ps1
uv run python -m agentic check                    # shows resolved settings, tests search and Foundry
uv run python -m agentic up                       # both solutions: index + knowledge base + connection + agent
uv run python -m agentic up --source docx         # or one solution only (docx, markdown or docx,markdown)
uv run python -m agentic ask "What does the April Money Map expect the Fed to do in May?"   # asks both agents
uv run python -m agentic ask --source markdown    # interactive; follow-ups keep context
uv run python -m agentic eval --limit 10          # golden questions through the agents -> reports/agentic_eval.md
uv run python -m agentic cleanup                  # delete agents, connections, knowledge bases, sources, indexes
uv run streamlit run agentic/app.py               # web UI, see below
uv run --extra rag --extra agentic streamlit run app.py   # four-way comparison, see below
```

Two solutions, as in `rag/`: `docx` reads the Word files, `markdown` reads the A+ output. Each has its own index
(`research-reports-docx`, `research-reports-markdown`), knowledge source, knowledge base, project connection and agent, so
an answer can be traced to one of them. Every chunk records `source` and `source_file`. The knowledge base uses extractive
output and minimal reasoning, as in the tutorial: the search service does not call an LLM, and the agent reasons over the
passages. The tutorial's SDK pins (`azure-ai-projects==2.2.0`, `azure-search-documents==12.1.0b2`) are preview releases and
the knowledge base API is `2026-08-01-preview`.

### Web UI

`agentic/app.py` is the Streamlit page for these agents. Pick **Word (docx)**, **MarkItDown (A+)** or **Both, side by
side**; each solution keeps its own conversation. The sidebar shows the index, knowledge base, agent and models behind the
selected solution, with live chunk counts per source file and the chunk size. Under every answer:
- **How the agent got this answer**: the steps in order, with timings. (1) the agent rewrites the question into several
  search queries, (2) the knowledge base runs them against the index and merges the hits, (3) if the answer depends on a
  picture, the agent calls `inspect_figure` and a vision model looks at it (the picture is shown), (4) the agent writes the
  answer with links to passages. A timing table gives seconds and tokens per round and for the picture calls. This is the
  extra work agentic does compared with `rag/`, which embeds the question, fetches the top chunks and answers.
- a check that all retrieved passages come from that solution's index and source (an error if one does not);
- **Cited or inspected passages**: the chunks the answer links to or whose picture was examined, with report, kind, source
  file, chunk id and the figure image. The agent's `【7:0†...】` markers are replaced by `[n]`, the number of the passage;
- **Retrieved passages**: everything the knowledge base returned, ranked, with the cited ones marked (tick the sidebar
  box to see their text).

Sidebar controls: **Passages the knowledge base returns per search** (stored on the knowledge base, applied before the next
question; fewer means a shorter prompt and a faster answer) and **Let the agent look at figure pictures** (off: the agent
reads only the text and the stored descriptions).

### Four-way comparison (`app.py`)

`app.py` at the root compares all four solutions on the same question: **Agentic** and **RAG**, each on **Word (docx)**
and on **MarkItDown (A+)**. It needs both extras (`--extra rag --extra agentic`), since it runs `rag/` and `agentic/`
side by side. `agentic/app.py` is unchanged and still compares the two agentic solutions only.

```powershell
uv run --extra rag --extra agentic streamlit run app.py
```

One question goes to every selected solution (sidebar: **Solutions to compare**). Answers appear in two rows, one per
input format, with agentic and RAG side by side in each. Under every answer you can see which file it came from:
- a banner names the index or collection and the files its passages came from (an error appears if a passage came from
  another index or format);
- the cited passages, with their report, file (`RA02752.docx` or `RA02752.md`) and chunk id;
- the pictures the model looked at, and for agentic the steps and timings;
- the full list of retrieved passages, in rank order, with the cited ones marked.

A summary table at the bottom gives the time, passage count and cited count for each solution. The sidebar shows the
indexes, chunk counts per file and chunk size for each solution. The passage limit applies to both approaches (agentic
updates the knowledge base before the next question). Each question takes about a minute or more, since the agentic
solutions run several model calls. Picture answers can differ between runs.

### Evaluation

`python -m agentic eval` runs the golden questions through the agents the way `rag eval` runs them through the local
RAG: same questions, same evidence matching, same judge prompt (`eval/judge.py`) and the same `JUDGE_DEPLOYMENT`.
Each question starts a fresh conversation. Rows go to `reports/agentic_eval.jsonl` as they are produced, with the
agent's query variants, the passages retrieved and cited, rounds, seconds per phase (retrieval, generation, vision)
and tokens (input, output, reasoning); `--resume` continues an interrupted run. `--source`, `--ids`, `--types`,
`--limit`, `--no-images` and `--judge` narrow or change a run. The report `reports/agentic_eval.md` comes from
`eval/scores.py`, which also merges runs into one table:

```powershell
uv run python eval/scores.py reports/rag_eval.jsonl reports/agentic_eval.jsonl reports/four_way.md
```

gives per question and per type the columns `docx | markdown | agentic-docx | agentic-markdown`. When comparing
agentic with RAG, run both with the same answer model (`AZURE_CHAT_DEPLOYMENT` for `rag/`, `AGENT_MODEL` for
`agentic/`): the committed `rag` run used gpt-4.1 and the agents default to gpt-5-mini.

### Pictures, speed and tuning

- **Pictures.** The knowledge base only returns text, so the agent has a second tool, `inspect_figure`. Figure chunks store
  the path of their rasterised picture (`image_path`, made by `rag/`); the tool looks it up in the index and asks a vision
  model (`AGENTIC_VISION_DEPLOYMENT`, default the agent model) the question. It runs on this machine, so the pictures must
  exist locally, as with `rag/`. Counting objects in a small picture is not exact: for the Fanta cans the answer varied
  between 3 and 5 across runs and models, which is why the answer gives a per-can breakdown.
- **Speed.** A text question takes about 7 s, about the same as `rag/`. A picture question adds the vision call (15 to 25 s
  at `AGENTIC_VISION_EFFORT=medium`; `low` is faster but its counts are unstable). What made it slow before: the agent used
  reasoning effort medium, the knowledge base returned 36 passages (17k tokens) per question, and the agent sometimes made
  a wasted tool call first. Now: `AGENT_REASONING_EFFORT=low`, 12 passages per search (`AGENTIC_MAX_DOCS`), and the first
  turn is forced to search the knowledge base.
- **Chunking and retrieval knobs.** Chunking is shared with `rag/`: `RAG_CHUNK_CHARS`, or
  `python -m agentic index --chunk-chars 1200 --reset`. Retrieval: `kb --max-docs N` (or the sidebar slider) for passages per
  search; `agent --reasoning minimal|low|medium|high`. `rag/` also has hybrid on/off and a chunk count per question; here
  the search service always does hybrid + semantic ranking and the agent chooses the queries.

## Golden questions

`golden/questions.yaml` holds 77 draft questions across eight types (text fact, table lookup, native chart,
picture chart, symbol matrix, relationship, cross-document, unanswerable). They were written by an engineer
from the extracted content and must be checked by analysts against the published PDFs before sign-off.

## Layout

```
src/research_ingest/   package.py (OOXML access), extract.py (canonical model), charts.py, emf.py,
                       render.py (compact text + chunks), cli.py
pipelines/             run_markitdown.py (A), run_azure_cu.py (B)
eval/                  retention.py (evidence retention + BM25 retrieval, token counts), judge.py (shared answer
                       judge), scores.py (jsonl -> scores table, merges rag and agentic runs)
agentic/               Azure AI Search knowledge base + Foundry agent over MCP (search_setup.py, foundry.py, app.py)
app.py                 Streamlit page comparing agentic and RAG, each on docx and markdown (needs both extras)
golden/                questions.yaml
reports/               generated comparison reports
data/raw/              sample reports (git-ignored)
out/                   pipeline outputs (git-ignored)
```
