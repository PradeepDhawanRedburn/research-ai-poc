from research_qa.evaluate.chart_quality import _paired, distribution, paired_errors


def test_error_distribution_pairs_series_and_uses_axis_range():
    facts = [
        {"doc_id": "D1", "figure": "Fig 1", "caption": "Revenue", "kind": "native_chart",
         "series": [{"name": "Sales", "points": [["2025-01", 10], ["2025-02", 20]]}]},
        {"doc_id": "D1", "figure": "Fig 1", "caption": "Revenue", "kind": "digitised_chart",
         "series": [{"name": "Sales", "axis_range": 100, "points": [["2025-01", 11], ["2025-02", 24]]}]},
    ]
    errors = paired_errors(facts)
    assert errors == [1.0, 4.0]
    assert distribution(errors) == {"n": 2, "median": 2.5, "p90": 4.0, "max": 4.0}
    assert len(_paired(facts)[1]) == 1


def test_distribution_empty_and_single_point():
    assert distribution([]) == {"n": 0, "median": None, "p90": None, "max": None}
    assert distribution([2.0]) == {"n": 1, "median": 2.0, "p90": 2.0, "max": 2.0}


def test_empty_live_distribution_is_explicit(tmp_path):
    from research_qa.evaluate.chart_quality import report
    from research_qa.storage import LocalStore

    text = report(LocalStore(tmp_path / "empty-store"))
    assert "Matched points: 0" in text and "Median: N/A" in text and "P90: N/A" in text and "Max: N/A" in text


def test_only_identical_native_and_digitised_series_are_paired():
    facts = [
        {"doc_id": "D1", "figure": "Fig 1", "caption": "Revenue", "kind": "native_chart",
         "series": [{"name": "Sales", "points": [["2025-01", 10]]}]},
        {"doc_id": "D1", "figure": "Fig 1", "caption": "Revenue", "kind": "digitised_chart",
         "series": [{"name": "Costs", "axis_range": 100, "points": [["2025-01", 11]]}]},
    ]
    assert paired_errors(facts) == []


def test_month_points_match_native_iso_dates():
    facts = [
        {"doc_id": "D1", "figure": "Fig 1", "caption": "Revenue", "kind": "native_chart",
         "series": [{"name": "Sales", "points": [["2025-01-15", 10]]}]},
        {"doc_id": "D1", "figure": "Fig 1", "caption": "Revenue", "kind": "digitised_chart",
         "series": [{"name": "Sales", "axis_range": 100, "points": [["2025-01", 12]]}]},
    ]
    assert paired_errors(facts) == [2.0]
