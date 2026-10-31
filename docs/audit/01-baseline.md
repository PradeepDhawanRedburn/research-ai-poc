# Audit PR 1: baseline measure, recall@k and latency by question type

**What it adds.** `python -m research_qa report --run <name> [--k 10]` reads a run's `rows.jsonl` from the store and
writes `eval/<run>/baseline-k<k>.md`: recall@k, p50 and p95 seconds, and mean tokens, per question type and per arm
and mode. No model call. Recall@k means every golden evidence string of the question is present in the text of the
first k retrieved passages, read back from the store by passage id. Rows whose ids cannot be resolved (Azure's own
Word chunks) fall back to the run's evidence flag and the report says how many.

**Why first.** Every later phase changes ingestion or retrieval. This is the same measurement, on the same rows and
questions, before and after each one.

**How to produce the "before" numbers.** On a machine with the store configured:

```bash
uv run python -m research_qa report --run baseline-langchain --k 10
```

and, when the Azure-engine run exists, the same for `baseline-azure`. Paste both tables into this file under
"Results" when they are available.

**Tests.** `tests/test_baseline.py`: recall respects k, unresolved ids fall back and are counted, the summary and the
Markdown table, the report is written to the store. Run with `uv run pytest`.

**Also in this PR.** A `dev` extra (`pytest`, `python-docx`, `python-pptx`) used by the tests and by the fixture
builder of PR 2; `tests/` as the home for repository tests, which did not exist before.

## Results

_To be filled from the real runs._
