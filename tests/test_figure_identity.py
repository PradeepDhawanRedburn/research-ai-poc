from research_ingest.extract import assign_figure_ids
from research_qa.agent.tools import lookup_facts
from research_qa.schema import Chunk, FactFigure, FactSeries
from research_qa.storage import LocalStore


def test_repeated_figure_labels_get_stable_distinct_ids():
    elements = [
        {"type": "figure", "figure_no": "Fig 27", "caption": "Cyclically adjusted balance"},
        {"type": "figure", "figure_no": "Fig 27", "caption": "ECB deposit rate"},
    ]
    assign_figure_ids("RA01220", elements)
    assert elements[0]["figure_id"].startswith("RA01220:1:")
    assert elements[1]["figure_id"].startswith("RA01220:2:")
    assert elements[0]["figure_id"] != elements[1]["figure_id"]
    assert [el["figure_label"] for el in elements] == ["Fig 27", "Fig 27"]


def test_lookup_figure_label_requires_caption_to_resolve_repetition(tmp_path):
    store = LocalStore(tmp_path / "store")
    facts = [
        FactFigure("RA01220", "Fig 27", "Cyclically adjusted balance", "native_chart",
                   series=[FactSeries("Germany", [["2030", -3.7]])], figure_id="RA01220:1:11111111",
                   figure_label="Fig 27"),
        FactFigure("RA01220", "Fig 27", "ECB deposit rate", "digitised_chart",
                   series=[FactSeries("Deposit rate", [["2030", 2.0]])], figure_id="RA01220:2:22222222",
                   figure_label="Fig 27"),
    ]
    store.put_json("facts/RA01220.json", [fact.to_dict() for fact in facts])

    ambiguous = lookup_facts(store, doc_id="RA01220", figure="Fig 27")
    assert "ambiguous" in ambiguous["note"].lower()
    selected = lookup_facts(store, doc_id="RA01220", figure="Fig 27", caption="ECB deposit")
    assert {row["figure_id"] for row in selected["rows"]} == {"RA01220:2:22222222"}
    assert "resolved" in selected["note"].lower()


def test_figure_identity_is_persisted_on_chunks():
    chunk = Chunk(id="D1-00001", doc_id="D1", chunk=1, kind="figure", text="Fig 27",
                  figure="Fig 27", figure_id="D1:1:12345678", figure_label="Fig 27")
    restored = Chunk.from_dict(chunk.to_dict())
    assert restored.figure_id == "D1:1:12345678" and restored.figure_label == "Fig 27"


def test_inspect_figure_resolves_label_and_caption_without_vision(tmp_path):
    store = LocalStore(tmp_path / "store")
    rows = [
        {"id": "D1-00001", "doc_id": "D1", "kind": "figure", "figure": "Fig 27",
         "figure_label": "Fig 27", "figure_id": "D1:1:11111111", "caption": "Balance", "text": "balance"},
        {"id": "D1-00002", "doc_id": "D1", "kind": "figure", "figure": "Fig 27",
         "figure_label": "Fig 27", "figure_id": "D1:2:22222222", "caption": "ECB deposit rate", "text": "deposit"},
    ]
    store.put("chunks/D1.jsonl", "".join(__import__("json").dumps(row) + "\n" for row in rows))
    from research_qa.agent.tools import inspect_figure

    result = inspect_figure(store, "", [], "", "What is shown?", figure="Fig 27",
                            caption="ECB deposit", doc_id="D1")
    assert result.figure_id == "D1:2:22222222"
    assert "caption resolved" in result.answer