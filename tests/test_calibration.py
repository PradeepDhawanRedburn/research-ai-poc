import csv

from research_qa.evaluate.calibration import agreement, export_sample, stratified_sample
from research_qa.storage import LocalStore

QUESTIONS = [
    {"id": "Q1", "type": "text_fact", "question": "First?"},
    {"id": "Q2", "type": "text_fact", "question": "Second?"},
    {"id": "Q3", "type": "chart_native", "question": "Third?"},
]
ROWS = [
    {"id": "Q1", "type": "text_fact", "arm": "word", "mode": "agentic", "answer": "A", "verdict": "correct"},
    {"id": "Q2", "type": "text_fact", "arm": "word", "mode": "agentic", "answer": "B", "verdict": "partial"},
    {"id": "Q3", "type": "chart_native", "arm": "word", "mode": "agentic", "answer": "C", "verdict": "incorrect"},
]


def test_sample_is_stratified_and_skips_unanswered():
    rows = ROWS + [{"id": "Q4", "type": "chart_native", "answer": ""}]
    sample = stratified_sample(rows, QUESTIONS, n=2)
    assert {row["type"] for row in sample} == {"text_fact", "chart_native"}
    assert all(row["human_verdict"] == "" for row in sample)


def test_export_and_cohen_kappa(tmp_path):
    store = LocalStore(tmp_path / "store")
    store.put("eval/run/rows.jsonl", "".join(__import__("json").dumps(row) + "\n" for row in ROWS))
    output = tmp_path / "graded.csv"
    assert export_sample(store, "run", output, n=3, questions=QUESTIONS) == 3
    with output.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        row["human_verdict"] = row["judge_verdict"]
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    stats = agreement(output)
    assert stats == {"n": 3, "agreement": 1.0, "kappa": 1.0}


def test_kappa_reports_disagreement():
    from research_qa.evaluate.calibration import _agreement_from_rows

    assert _agreement_from_rows([
        {"judge_verdict": "correct", "human_verdict": "incorrect"},
        {"judge_verdict": "incorrect", "human_verdict": "correct"},
    ])["kappa"] == -1.0
