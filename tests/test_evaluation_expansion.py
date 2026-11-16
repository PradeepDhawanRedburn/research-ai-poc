import json

from research_qa.evaluate.extraction import check as extraction_check, markdown as extraction_markdown
from research_qa.evaluate.run import run
from research_qa.schema import Answer
from research_qa.storage import LocalStore


def test_extraction_coverage_reads_structured_chart_facts(tmp_path):
	store = LocalStore(tmp_path / "store")
	store.put("markdown/D1.md", "General government balance forecast.")
	store.put_json("facts/D1.json", [{"figure": "Fig 27", "caption": "Government balance", "kind": "native_chart",
									 "series": [{"name": "Germany", "points": [["2030", -4.7]]}], "cells": []}])
	questions = [{"id": "Q1", "doc": "D1", "type": "chart_native", "source_of_truth": "pdf_analyst",
				  "question": "Germany in 2030?", "evidence": ["Germany", "2030: -4.7"]}]
	result = extraction_check(store, questions)
	assert result["by_source_of_truth"]["pdf_analyst"]["extracted"] == 1
	assert "pdf_analyst" in extraction_markdown(result)


def test_distractor_documents_and_corpus_size_are_recorded(tmp_path):
	store = LocalStore(tmp_path / "store")
	store.put("raw/NOQ.docx", b"not opened by eval")
	key = run(["word"], ["agentic"], ids=["MM04-01"], run_name="distractor", store=store,
			  distractor_docs=["NOQ"], ask_fn=lambda q, arm, mode: Answer(arm=arm, mode=mode, question=q, text="x"),
			  judge_fn=lambda q, text: {"verdict": "incorrect", "reason": "test"}, progress=None)
	config = store.json(f"{key}/config.json")
	assert config["distractor_docs"] == ["NOQ"] and config["corpus_size"] == 1

