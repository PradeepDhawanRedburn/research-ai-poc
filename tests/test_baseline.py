"""Recall@k and latency from evaluation rows, against a local store with known chunk texts."""
import json

import pytest

from research_qa.evaluate import baseline
from research_qa.storage import LocalStore

GOLDEN = [
    {"id": "Q1", "doc": "D1", "type": "text_fact", "question": "q", "answer": "a", "evidence": ["keep rates on hold"]},
    {"id": "Q2", "doc": "D1", "type": "chart_native", "question": "q", "answer": "a", "evidence": ["Germany", "-4.7"]},
    {"id": "Q3", "doc": "D1", "type": "unanswerable", "question": "q", "answer": "The documents do not say.", "evidence": []},
]


@pytest.fixture
def store(tmp_path):
    s = LocalStore(tmp_path / "store")
    chunks = [{"id": "D1-00000", "text": "Report D1\n\nThe bank will keep rates on hold."},
              {"id": "D1-00001", "text": "Report D1\n\nFig 27: Germany: -4.7% of GDP in 2030"},
              {"id": "D1-00002", "text": "Report D1\n\nUnrelated prose about Germany only."}]
    s.put(f"chunks/D1.jsonl", "".join(json.dumps(c) + "\n" for c in chunks), "application/x-ndjson")
    return s


def row(qid, qtype, retrieved, secs, tokens, evidence_retrieved=None, arm="markdown-plus", mode="single-pass"):
    return {"id": qid, "type": qtype, "arm": arm, "mode": mode, "combo": f"{arm}/{mode}", "retrieved": retrieved,
            "total_s": secs, "total_tokens": tokens, "evidence_retrieved": evidence_retrieved,
            "verdict": "correct", "evidence_hits": "1/1"}


def test_recall_uses_only_the_first_k_passages(store):
    rows = [row("Q1", "text_fact", ["D1-00002", "D1-00000"], 2.0, 3000),
            row("Q2", "chart_native", ["D1-00002", "D1-00000", "D1-00001"], 4.0, 5000),
            row("Q3", "unanswerable", ["D1-00000"], 1.0, 1000)]
    recs = baseline.recall_rows(rows, store, k=2, questions=GOLDEN)
    by = {r["id"]: r for r in recs}
    assert by["Q1"]["recall"] == 1.0 and by["Q1"]["resolved"]
    assert by["Q2"]["recall"] == 0.0, "the evidence chunk is ranked third, outside k=2"
    assert by["Q3"]["recall"] is None, "no evidence: not scored"
    recs10 = baseline.recall_rows(rows, store, k=10, questions=GOLDEN)
    assert {r["id"]: r["recall"] for r in recs10}["Q2"] == 1.0


def test_unresolvable_ids_fall_back_to_the_run_flag(store):
    rows = [row("Q1", "text_fact", ["azure-opaque-id"], 2.0, 3000, evidence_retrieved=True, arm="word", mode="agentic")]
    recs = baseline.recall_rows(rows, store, k=10, questions=GOLDEN)
    assert recs[0]["recall"] == 1.0 and recs[0]["resolved"] is False
    s = baseline.summarise(recs)
    assert s["word/agentic"]["all"]["unresolved"] == 1
    md = baseline.markdown("r", s, 10)
    assert "could not be resolved" in md and "word/agentic: 1" in md


def test_summary_and_markdown(store):
    rows = [row("Q1", "text_fact", ["D1-00000"], 1.0, 1000), row("Q2", "chart_native", ["D1-00001"], 3.0, 2000),
            row("Q2", "chart_native", ["D1-00002"], 9.0, 6000, arm="word", mode="single-pass"),
            row("Q3", "unanswerable", [], 2.0, 500)]
    s = baseline.summarise(baseline.recall_rows(rows, store, k=10, questions=GOLDEN))
    mp = s["markdown-plus/single-pass"]
    assert mp["all"]["n"] == 3 and mp["all"]["scored"] == 2 and mp["all"]["recall"] == 1.0
    assert mp["chart_native"]["recall"] == 1.0 and s["word/single-pass"]["chart_native"]["recall"] == 0.0
    assert mp["all"]["p95_s"] == 3.0 and mp["all"]["p50_s"] == 2.0
    md = baseline.markdown("run-x", s, 10, {"answer_model": "m", "max_docs": 12})
    assert "# Baseline: recall@10" in md and "| chart (native) | 1 | 100% | 0% |" in md
    assert "| all | 3 |" in md


def test_report_writes_to_the_store(store):
    rows = [row("Q1", "text_fact", ["D1-00000"], 1.0, 1000)]
    store.put("eval/run-x/rows.jsonl", "".join(json.dumps(r) + "\n" for r in rows), "application/x-ndjson")
    store.put_json("eval/run-x/config.json", {"answer_model": "m", "max_docs": 12})
    import research_qa.evaluate.baseline as b

    b.load_questions = lambda *a, **k: GOLDEN  # the real golden set does not know Q1
    md, summary = baseline.report("run-x", store, k=10)
    assert store.exists("eval/run-x/baseline-k10.md") and "100%" in md


def test_legacy_mode_names_are_read_as_new_ones(tmp_path):
    from research_qa.config import canon_combo, canon_mode
    from research_qa.evaluate.run import load_run
    s = LocalStore(tmp_path / 'store')
    row = {'id': 'Q1', 'type': 'text_fact', 'doc': 'D1', 'arm': 'word', 'mode': 'direct', 'combo': 'word/direct', 'verdict': 'correct', 'evidence_retrieved': True, 'evidence_hits': '1/1', 'errors': [], 'answer': 'a'}
    s.put('eval/old/rows.jsonl', json.dumps(row) + '\n', 'application/x-ndjson')
    summary, rows, _ = load_run('old', s)
    assert rows[0]['mode'] == 'single-pass' and 'word/single-pass' in summary['per_combo']
    assert canon_mode('agent') == 'agentic' and canon_combo('word/agent') == 'word/agentic'
