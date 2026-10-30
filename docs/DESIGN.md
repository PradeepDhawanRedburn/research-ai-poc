# Research reports Q&A: the requirement, revisited from scratch

**Branch** `code-revisit-requirement` · **Date** 30 October 2026 · **Status** design for the rebuild; the Phase 0
code on `code-v1` is the evidence it draws on.

## 1. The requirement, in one paragraph

Analysts write research reports in Word: prose, financial tables, native Word charts, Excel charts pasted as
pictures, tick/cross matrices, photos and screenshots. People want to ask questions in plain language and get
exact, cited answers from these reports: a number from a chart, a state from a matrix, what a picture shows, how a
view changed between two editions. The house is on Azure (Foundry, AI Search, Blob). The executive question is
whether converting Word to Markdown first is the right foundation, what it costs, and whether something off the
shelf does the job. The deliverable is a working system that is accurate, cheap to run, easy to show.

## 2. What Phase 0 established (the evidence)

| Finding | Measured |
|---|---|
| Stock Markdown conversion loses the answers that matter | MarkItDown keeps evidence for 52 of 71 answerable questions; every loss is a chart, a matrix or a picture chart |
| Our Word extractor keeps them | 71 of 71; Excel-pasted charts digitised to within 1% of axis range; matrices read with colour |
| Charts read as numbers cost nothing at question time | 121 of 135 pasted charts digitised; 34 vision calls at ingest instead of 177 |
| Answer quality is equal on prose and tables | 91% on 88 questions through RAG, both inputs; 10 of 10 on the first agentic run |
| Per-question tokens are the same or slightly lower with Markdown+ | 1 to 11% fewer; vision calls at question time dominate the variance |
| The "Word" arm never measured raw Word | Both arms ran through our extractor; the honest baseline is still missing |

## 3. Every step questioned

### 3.1 Where do documents live? → Blob, and only Blob

*Was:* `data/raw/` on a laptop, outputs in `out/`, a Chroma store in `.rag_cache/`, pictures referenced by absolute
local paths inside the search index (which broke on the next machine).
*Decision:* one Blob container is the system of record for everything: raw Word, Markdown+, assets, facts, chunks,
manifests, evaluation runs. Code reads and writes through one `Store` interface with a Blob implementation and a
local-folder implementation for development and tests. No local path is ever stored in an index or a chunk;
pictures are referenced by store key and fetched when needed.

```
<container>/
  raw/<doc>.docx                 what analysts produce (upload here)
  markdown/<doc>.md              Markdown+ (MarkItDown + research plugin), front matter with metadata
  assets/<doc>/<sha>.png         every picture rasterised once, named by content hash
  facts/<doc>.json               chart series and matrix cells as numbers and states
  chunks/<doc>.jsonl             retrieval chunks with context header and metadata
  descriptions/<sha>.json        vision descriptions, cached per picture and prompt version
  manifest.json                  per report: what was extracted, counts, timings, warnings
  eval/<run>/rows.jsonl, report.md, summary.json
```

### 3.2 Word → Markdown: is MarkItDown still the right chassis? → Yes, with the plugin, as the only path

*Was:* three pipelines (stock MarkItDown "A", MarkItDown+plugin "A+", extractor direct "C") plus a Content
Understanding adapter "B" that was never run, and a second, hand-written Word reader inside `rag/loaders/docx_source.py`
that duplicated the extractor.
*Decision:* one path. MarkItDown is the public interface (`pip install`, one call, other formats later); the research
plugin does the Word step. The extractor stays as it is (`src/research_ingest`), it is the proven part. Pipelines A,
B, C and the duplicate loader are removed. The stock-MarkItDown baseline is no longer needed because the new Word arm
(3.5) is a stronger baseline.

*What Markdown+ gains in the rebuild:*
- **Picture descriptions are part of the conversion**, not of a downstream loader. The vision prompt is written for the
  questions people ask of pictures: every item with its printed text, counts per item and per form, colours, layout.
  Described once per picture, cached by content hash and prompt version in the store.
- **A facts file next to the Markdown**: every native chart series, every digitised series, every matrix cell as a
  row. Markdown is for reading and searching; facts are for exact lookups.
- **A metadata block that is also a chunk**: title, subtitle, date, analysts, ticker, rating, target price, sector.
  "Who wrote the July Money Map" is a lookup, not a search.
- **Noise removed**: anchors and image file names stay out of chunk text; the digitised series summary is compact
  (first, min, max, last six points) with the full series in the facts file.

### 3.3 Chunking → one structure-aware chunker, in the ingest step

*Was:* three chunkers (extractor `build_chunks`, `rag/chunking.py` with a LangChain splitter, and whatever the
Azure default does).
*Decision:* one chunker in ingest, written to `chunks/<doc>.jsonl` and pushed from there. Rules: prose grouped by
section up to 1,800 characters; a table is one chunk (split by row groups with the header repeated when long); a
figure is one chunk (caption, description, compact series summary, source); the metadata block is one chunk. Every
chunk starts with a context header: report id, title, date, section, figure number when there is one.

### 3.4 Retrieval → Azure AI Search knowledge bases (agentic retrieval), as today

*Was:* two retrieval stacks (LangChain + Chroma hybrid, and AI Search knowledge base + Foundry agent).
*Question:* does the second stack add information? It showed that direct retrieval is about 3 s a question against
8 s with the agent, and a little cheaper. That is a mode, not a stack.
*Decision:* AI Search knowledge bases only. Two **modes** over the same index: **agent** (Foundry agent plans
queries, calls the knowledge base over MCP, can call tools) and **direct** (one knowledge-base retrieve, one chat
completion). Chroma, LangChain and the local store go.

### 3.5 The baseline → raw Word through Azure's own pipeline

*Was:* "docx" meant our extractor reading the Word file, so Word and Markdown+ were the same text.
*Decision:* the **Word arm is an Azure Blob knowledge source over `raw/*.docx`**: Azure cracks, chunks, embeds and
verbalises images itself. Zero custom code. That is the commodity offer the executives are asking about, and the
fair comparison: what Microsoft gives you out of the box against what the plugin adds.

### 3.6 Answering → one agent definition, two arms, tools where the data exists

*Decision:* the same model, instructions and reasoning effort for both arms. The Markdown+ agent has two function
tools the Word agent cannot have because its data does not exist there: `lookup_facts` (exact chart values and matrix
states from `facts/`) and `inspect_figure` (a vision model looks at the picture behind a chunk, from `assets/`).
Citations are rendered as passage numbers; every answer carries the passages, the steps, the tool calls, the tokens.

### 3.7 Evaluation → one runner, both arms, both modes, cost in money

*Was:* `rag eval` and `agentic eval` with a shared judge, tokens but no cost, results in `reports/`.
*Decision:* one runner over the golden set for any arm×mode, same judge, evidence retrieval check, tokens split by
input/output/reasoning/vision, seconds by phase, and **cost from a price table** (`prices.yaml`, per deployment per
million tokens; the user fills it, the report shows it). Runs are stored in `eval/<run>/` in the store and read by the
app's scorecard. The no-LLM retention check stays: it is the cheapest proof that the conversion lost nothing.

### 3.8 The demo → an executive page first, the engineer's view one click deeper

*Was:* three Streamlit pages written at different times, each with its own layout; a wall of expanders.
*Decision:* one Streamlit app with four pages.
1. **Overview**: the pipeline in one figure, the latest scorecard in five numbers (accuracy, evidence found, seconds,
   tokens, cost per question) per arm, and what is in the store.
2. **Ask**: one question, tiles for the selected arm×mode combinations, run in parallel, each tile the same layout
   (answer, cost line, evidence, pictures, steps). Side-by-side table underneath with time, tokens, cost, grounding.
3. **Scorecard**: the evaluation runs, by question type, with per-question drill-down and disagreements.
4. **Reports**: per report, what the conversion found (figures digitised, pictures described, tables, matrices,
   warnings), with the Markdown and the facts browsable.

### 3.9 Alternatives considered

| Option | What it is | Verdict for this requirement |
|---|---|---|
| **Foundry IQ Blob knowledge source** | Azure indexes the Word files itself, with image verbalisation | Used, as the Word baseline arm. It cannot read a pasted Excel chart as numbers or a Wingdings matrix as states; that is exactly what the plugin adds |
| **Azure Document Intelligence / Content Understanding** | Layout and field extraction services | No better on DOCX than reading the OOXML directly; CU needs a budget we do not have. Not used |
| **RAGFlow (InfiniFlow)** | Self-hosted open-source RAG platform with its own parsers, Infinity engine | A different stack to run and secure; its DOCX parser is in the stock-MarkItDown class (no chart digitisation). Useful only as a second control arm; not as the product |
| **Graphiti / Zep** | Temporal knowledge graph for agent memory | Solves a different problem (evolving facts about entities across conversations). Cross-edition questions here are answered by the `date` and `doc_id` filters |
| **Microsoft GraphRAG / Graph in Fabric** | Entity graph and community summaries over a corpus | Good for "what are the themes across 500 reports", expensive to index, worse than a facts table for "what was the 2030 value in Fig 27". Revisit when the corpus is hundreds of reports |
| **Fabric IQ, NL2SQL** | Question to SQL over structured data | The facts file is the start of that structured layer; when it moves to a table, NL2SQL is the natural tool |

## 4. What is removed

`pipelines/` (A, B, C runners), `rag/` (LangChain, Chroma, duplicate Word loader, its app and eval), `agentic/` (ported
into the new package), root `app.py` and `comparison.py`, `out/` (regenerated into the store), the `A+ old` and `C`
copies, the dropped Content Understanding adapter. The extractor (`src/research_ingest`), the golden set and the
Phase 0 reports (as history in `docs/history/`) stay.

## 5. The new layout

```
research_qa/
  config.py        settings from .env, the arms and modes, prices
  storage.py       Store: Blob or local folder, one API
  ingest/          convert (MarkItDown + plugin) → describe pictures → facts → chunks → manifest
  search/          index schema and push; knowledge sources (Blob for Word, index for Markdown+); knowledge bases
  agent/           Foundry agent per arm; tools lookup_facts and inspect_figure; direct mode
  evaluate/        golden set, judge, runner, retention check, cost, report
  app/             Streamlit: Overview, Ask, Scorecard, Reports
  __main__.py      python -m research_qa  ingest | index | agents | ask | eval | status | down
src/research_ingest/   the Word extractor and MarkItDown plugin (unchanged)
golden/questions.yaml
docs/DESIGN.md (this file), docs/history/
```

## 6. What "done" looks like

- `python -m research_qa ingest` reads `raw/` from the store and writes markdown, assets, facts, chunks, manifest.
- `python -m research_qa index` builds the Markdown+ index and both knowledge sources and bases.
- `python -m research_qa agents` creates the two agents. `ask "…" --arm markdown-plus --mode agent` answers.
- `python -m research_qa eval --arm word,markdown-plus --mode agent,direct` writes a run with accuracy, tokens, cost.
- `streamlit run research_qa/app/Overview.py` shows the four pages from the store.
- No local file is required at run time except `.env`.
