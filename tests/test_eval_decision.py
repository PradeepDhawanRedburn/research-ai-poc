import json
from research_qa.evaluate import report
from research_qa.evaluate.golden import QUESTION_TYPES, validate_questions
from research_qa.evaluate.retention import check, report_markdown
from research_qa.storage import LocalStore


def test_wilson_interval_and_paired_mcnemar():
    interval = report.wilson_interval(60, 120)
    assert interval and 0.41 < interval[0] < 0.42 and 0.58 < interval[1] < 0.59
    rows = [{"id": f"Q{i}", "combo": "left", "verdict": "correct"} for i in range(6)]
    rows += [{"id": f"Q{i}", "combo": "right", "verdict": "incorrect"} for i in range(6)]
    comparison = report.paired_comparison(rows, "left", "right")
    assert comparison["strict"]["p"] == 0.03125
    assert comparison["strict"]["significance"] == "significant"
    assert comparison["lenient"]["p"] == 0.03125


def test_report_splits_source_of_truth_and_shows_all_layers():
    rows = [
        {"id": "Q1", "type": "text_fact", "doc": "D1", "arm": "word", "mode": "agentic",
         "combo": "word/agentic", "source_of_truth": "pdf_analyst", "verdict": "correct",
         "evidence_retrieved": True, "tools": [], "evidence_hits": "1/1"},
        {"id": "Q2", "type": "text_fact", "doc": "D1", "arm": "word", "mode": "agentic",
         "combo": "word/agentic", "source_of_truth": "extractor_output", "verdict": "incorrect",
         "evidence_retrieved": False, "tools": [], "evidence_hits": "0/1"},
    ]
    summary = report.summarise(rows, ["word"], ["agentic"])
    summary["run"] = "r"
    markdown = report.markdown(summary, rows)
    assert summary["per_source_of_truth"]["word/agentic"]["pdf_analyst"]["accuracy"] == 1
    assert "95% CI" in markdown and "Paired McNemar" not in markdown
    assert "pdf_analyst" in markdown and "extractor_output" in markdown


def test_retention_uses_docs_union_and_explains_its_limit(tmp_path):
    store = LocalStore(tmp_path / "store")
    for doc, evidence in (("D1", "Alpha finding"), ("D2", "Beta finding")):
        store.put(f"markdown/{doc}.md", evidence)
        store.put(f"chunks/{doc}.jsonl", json.dumps({"id": f"{doc}-0", "text": evidence}) + "\n")
    question = {"id": "X1", "doc": "D1", "docs": ["D1", "D2"], "type": "cross_document",
                "question": "Compare reports", "answer": "Alpha and beta", "evidence": ["Alpha finding", "Beta finding"],
                "source_of_truth": "extractor_output"}
    result = check(store, questions=[question])
    assert result["rows"][0]["retained_all"]
    assert "source_of_truth" in report_markdown(result)


def test_golden_schema_accepts_new_types_and_rejects_unknown_type():
    validate_questions([{"id": "N1", "type": "temporal_change", "source_of_truth": "pdf_analyst",
                         "verified_by": "Analyst", "verified_on": "2026-11-12"}])
    assert "wrong_edition_trap" in QUESTION_TYPES and "multi_doc_aggregate" in QUESTION_TYPES
    try:
        validate_questions([{"id": "bad", "type": "made_up"}])
    except ValueError as exc:
        assert "unsupported question type" in str(exc)
    else:
        raise AssertionError("unknown question type was accepted")
