# Audit PR 13: decision-grade evaluation

**Before.** The report emphasized aggregate accuracy and evidence found; it had no confidence intervals or paired
significance test. Extraction retention was presented without the provenance caveat, and there was no judge
calibration workflow.

**Now.** `report --run --k` separates extraction coverage (Markdown+ plus structured facts), retrieval recall@k and
answer-judge scores. Wilson 95% intervals accompany accuracy and recall; exact binomial McNemar comparisons report
strict and lenient results, marking p ≥ 0.05 as not significant. Results split by `source_of_truth`; current golden
questions explicitly use `extractor_output`. The golden schema supports the five new question types without adding
questions. Evaluation config records corpus size and optional distractor document IDs. `calibration-sample` exports a
type-stratified CSV, and `calibration-report` calculates percent agreement and Cohen's kappa. Retention uses each
question's `docs` list and warns that extractor-output retention is not independent verification.

**Measured offline.** `tests/test_eval_decision.py`, `tests/test_evaluation_expansion.py`, `tests/test_calibration.py`
and `tests/test_baseline.py` cover intervals, exact paired tests, facts-backed evidence coverage, cross-document
retention, corpus config, calibration, and backward-compatible report output.

**Measured live.** `report --run baseline-langchain --k 10` completed and wrote `eval/baseline-langchain/baseline-k10.md`;
it shows all three layers, Wilson intervals, and strict/lenient McNemar results. Matched configurations with p ≥ 0.05
were labelled not significant.

**To measure on the report store.** Use an existing full run and an indexed distractor report, then create and grade a
calibration sample:

```bash
uv run python -m research_qa report --run <run> --k 10
uv run python -m research_qa eval --arm markdown-plus --mode agentic --run <run-with-distractors> --distractor-docs <doc-id>
uv run python -m research_qa calibration-sample --run <run> --n 50 --out eval-calibration.csv
uv run python -m research_qa calibration-report --csv eval-calibration-graded.csv
```

**Not verified here.** No live evaluation run, human grading, Azure/Chroma distractor retrieval, or analyst-verified
golden items were available. The new source-of-truth split will therefore show the current 122-question extractor
cohort separately rather than presenting it as independent ground truth. No dependency was added.