"""Access control: two users with different permissions see different chunks, in both legs and in the facts tool."""
from dataclasses import replace

import pytest

from research_qa import acl
from research_qa.config import settings
from research_qa.lc import index as ix
from research_qa.schema import Chunk
from research_qa.storage import LocalStore
from tests.test_retrieval import FakeVS, chunk

ALICE = ["user:alice@example.com", "group:macro"]
BOB = ["user:bob@example.com", "group:gaming"]


def with_acl(c: Chunk, groups):
    c.acl = list(groups)
    return c


CHUNKS = [
    with_acl(chunk(0, "RA01220", "text", "Money Map Fed rates on hold Germany balance"), ["group:macro"]),
    with_acl(chunk(1, "RA01220", "chart", "Money Map Fig 27 Germany 2030 balance", figure="Fig 27"), ["group:macro"]),
    with_acl(chunk(2, "RA02283", "matrix", "Evolution Italy matrix Money Map mention Germany"), ["group:gaming"]),
    with_acl(chunk(3, "RA02752", "text", "CCEP Pop Britain public note Money Map Germany"), ["public"]),
    with_acl(chunk(4, "RA02752", "figure", "legacy chunk without an acl Money Map Germany"), []),
]


@pytest.fixture
def retriever(monkeypatch):
    monkeypatch.setattr(ix, "settings", replace(settings, retrieval_figure_boost=True, reranker="none", principals=()))
    return ix.Retriever("markdown-plus", docs=list(CHUNKS), vs=FakeVS(CHUNKS))


def docs_seen(retriever, principals, query="Money Map Germany"):
    return sorted({c.doc_id for c in retriever.retrieve(query, k=10, principals=principals)})


def test_two_users_see_different_reports(retriever):
    assert docs_seen(retriever, ALICE) == ["RA01220", "RA02752"]
    assert docs_seen(retriever, BOB) == ["RA02283", "RA02752"]
    assert docs_seen(retriever, []) == ["RA02752"], "anonymous: public and legacy chunks only"
    assert docs_seen(retriever, ["group:macro", "group:gaming"]) == ["RA01220", "RA02283", "RA02752"]


def test_both_legs_and_the_boost_respect_access(retriever):
    # the dense leg is post-filtered: the fake store returns everything, the retriever must drop what Bob may not see
    dense = retriever._dense("Germany balance", None, BOB)
    assert "RA01220-00000" not in dense and "RA01220-00001" not in dense
    sparse = retriever._sparse("Germany balance", None, BOB)
    assert all(not i.startswith("RA01220") for i in sparse)
    info = {}
    got = retriever.retrieve("Fig 27 Germany", k=3, principals=BOB, info=info)
    assert info["figure_boost"] == [] and all(c.doc_id != "RA01220" for c in got), "no boost into a hidden report"
    info = {}
    got = retriever.retrieve("Fig 27 Germany", k=3, principals=ALICE, info=info)
    assert got[0].id == "RA01220-00001" and info["principals"] == ALICE


def test_default_principals_come_from_settings(monkeypatch):
    monkeypatch.setattr(ix, "settings", replace(settings, retrieval_figure_boost=False, reranker="none"))
    monkeypatch.setattr(acl, "settings", replace(settings, principals=("group:gaming",)))
    r = ix.Retriever("markdown-plus", docs=list(CHUNKS), vs=FakeVS(CHUNKS))
    assert sorted({c.doc_id for c in r.retrieve("Money Map Germany", k=10)}) == ["RA02283", "RA02752"]


def test_acl_file_defaults_and_lookup(tmp_path):
    store = LocalStore(tmp_path / "s")
    a = acl.load(store)
    assert a == {"default": ["public"], "docs": {}} and acl.for_doc(a, "X") == ["public"]
    store.put_json("acl.json", {"default": [], "docs": {"RA02283": ["group:gaming"]}})
    a = acl.load(store)
    assert acl.for_doc(a, "RA02283") == ["group:gaming"] and acl.for_doc(a, "RA01220") == []
    assert acl.allowed(["group:gaming"], BOB) and not acl.allowed(["group:gaming"], ALICE)
    assert acl.allowed([], ALICE) and acl.allowed(["public"], None) and not acl.allowed(["group:x"], None)
    assert acl.unlisted(store, [["public"], []]) and not acl.unlisted(store, [["public"]])


def test_metadata_round_trip_carries_acl():
    from langchain_core.documents import Document

    c = with_acl(chunk(9, "RA02283", "text", "x"), ["group:gaming", "user:bob@example.com"])
    m = ix.to_metadata(c)
    assert m["acl"] == "group:gaming;user:bob@example.com"
    back = ix.from_document(Document(page_content=c.text, metadata=m, id=c.id))
    assert back.acl == ["group:gaming", "user:bob@example.com"]


def test_azure_filter_carries_access_and_doc():
    from research_qa.agent.direct import retrieve_body

    f = retrieve_body("markdown-plus", "q", doc_id="RA01220", principals=["group:macro", "o'neil"])
    assert f["knowledgeSourceParams"][0]["filterAddOn"] == \
        "acl/any(a: search.in(a, 'public,group:macro,o''neil', ',')) and doc_id eq 'RA01220'"
    anon = retrieve_body("markdown-plus", "q", principals=[])
    assert anon["knowledgeSourceParams"][0]["filterAddOn"] == "acl/any(a: search.in(a, 'public', ','))"
    assert "filterAddOn" not in retrieve_body("word", "q", principals=ALICE)["knowledgeSourceParams"][0]


def test_facts_tool_withholds_rows_of_hidden_reports(tmp_path, monkeypatch):
    from research_qa.lc import answer as A
    from research_qa.schema import FactFigure, FactSeries

    store = LocalStore(tmp_path / "s")
    store.put_json("acl.json", {"default": ["public"], "docs": {"RA02283": ["group:gaming"]}})
    for d, v in (("RA01220", -4.7), ("RA02283", 2240.0)):
        store.put_json(f"facts/{d}.json", [FactFigure(d, "Fig 1", "cap", "native_chart", "", "", False, "",
                                                     [FactSeries("s", [[2030, v]])]).to_dict()])
    chat = A.Chat("markdown-plus", store=store, model=lambda *a: None, retrieve=lambda q, k, d: [], principals=ALICE)
    from research_qa.schema import Answer

    ans = Answer(arm="markdown-plus", mode="agent", question="q", text="")
    out = chat._run_tool(ans, store, "lookup_facts", {"doc_id": None, "figure": "Fig 1", "series": None, "x": None, "query": None},
                         True, None)
    assert "-4.7" in out and "2240" not in out and "withheld" in out
