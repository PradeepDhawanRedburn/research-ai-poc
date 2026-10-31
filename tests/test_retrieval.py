"""Hybrid retrieval with filters, figure boost, parallel legs and a pluggable reranker, over fake stores."""
import json
from dataclasses import replace

import pytest

from research_qa.config import settings
from research_qa.lc import index as ix
from research_qa.schema import Chunk


def set_flags(monkeypatch, **flags):
    """Settings is a frozen dataclass: tests swap the module-level object for a copy with the flags changed."""
    import research_qa.evaluate.run as runner

    s = replace(settings, **flags)
    monkeypatch.setattr(ix, "settings", s)
    monkeypatch.setattr(runner, "settings", s)
    return s


class FakeDoc:
    def __init__(self, id_, text, meta):
        self.id, self.page_content, self.metadata = id_, text, meta


class FakeVS:
    """Token-overlap 'vector' search that honours the Chroma where clause shape the retriever builds."""

    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    def similarity_search(self, query, k, filter=None):
        self.calls.append(filter)
        conds = [] if not filter else (filter["$and"] if "$and" in filter else [filter])
        q = set(ix.toks(query))

        def ok(c):
            return all(str(getattr(c, k_)) == str(v) for cond in conds for k_, v in cond.items())

        scored = sorted((c for c in self.chunks if ok(c)), key=lambda c: -len(q & set(ix.toks(c.text))))
        return [FakeDoc(c.id, c.text, ix.to_metadata(c)) for c in scored[:k]]


def chunk(i, doc, kind, text, figure=""):
    return Chunk(id=f"{doc}-{i:05d}", doc_id=doc, chunk=i, kind=kind, text=text, figure=figure, source_file=f"{doc}.md")


CHUNKS = [
    chunk(0, "RA01220", "text", "Report RA01220 April Money Map. The Fed will keep policy rates on hold in May."),
    chunk(1, "RA01220", "chart", "Report RA01220 Fig 27 cyclically adjusted balance Germany 2030: -4.7", figure="Fig 27"),
    chunk(2, "RA01220", "text", "Report RA01220 Germany Germany Germany growth outlook and fiscal balance discussion."),
    chunk(3, "RA01734", "text", "Report RA01734 July Money Map. The Bank of England is expected to cut to 4%."),
    chunk(4, "RA01734", "chart", "Report RA01734 Fig 27 something else entirely about wages", figure="Fig 27"),
    chunk(5, "RA02752", "figure", "Report RA02752 Fig 22 Flashbacks to Fanta: three cans Vive old Fanta new Fanta", figure="Fig 22"),
]


@pytest.fixture
def retriever(monkeypatch):
    set_flags(monkeypatch, retrieval_figure_boost=True, reranker="none")
    return ix.Retriever("markdown-plus", docs=list(CHUNKS), vs=FakeVS(CHUNKS))


def test_filters_apply_to_both_legs(retriever):
    got = retriever.retrieve("Germany balance", k=5, doc_id="RA01734")
    assert got == [] or all(c.doc_id == "RA01734" for c in got)
    got = retriever.retrieve("Money Map", k=5, filters={"kind": "text"})
    assert got and all(c.kind == "text" for c in got)
    assert retriever.vs.calls[-1] == {"kind": "text"}
    got = retriever.retrieve("Money Map", k=5, doc_id="RA01734", filters={"kind": "text"})
    assert [c.id for c in got] == ["RA01734-00003"]
    assert retriever.vs.calls[-1] == {"$and": [{"kind": "text"}, {"doc_id": "RA01734"}]}


def test_figure_boost_puts_the_named_figure_first(retriever, monkeypatch):
    info = {}
    got = retriever.retrieve("What is Germany's forecast for 2030 in Fig 27?", k=3, doc_id="RA01220", info=info)
    assert got[0].id == "RA01220-00001" and info["figure_boost"] == ["RA01220-00001"]
    # without a doc filter both reports' Fig 27 chunks are boosted, the question's report cannot be told apart
    got = retriever.retrieve("Fig 27", k=3, info=info)
    assert {c.id for c in got[:2]} == {"RA01220-00001", "RA01734-00004"}
    set_flags(monkeypatch, retrieval_figure_boost=False, reranker="none")
    info = {}
    retriever.retrieve("Germany Germany Germany Fig 27", k=3, doc_id="RA01220", info=info)
    assert info["figure_boost"] == []


def test_parallel_and_sequential_legs_fuse_identically(retriever):
    q = "Germany balance Money Map"
    assert retriever.fused(q, None, parallel=True) == retriever.fused(q, None, parallel=False)
    assert ix.where_clause(None) is None and ix.where_clause({"doc_id": "", "kind": None}) is None


class FakeReranker:
    def __init__(self):
        self.seen = []

    def rerank(self, query, docs, top_n):
        self.seen.append((query, [i for i, _ in docs], top_n))
        return [i for i, _ in reversed(docs)][:top_n]   # reverse order: the test can see it won


def test_reranker_is_pluggable_and_bounded(monkeypatch):
    set_flags(monkeypatch, retrieval_figure_boost=False, reranker="fake", rerank_candidates=2)
    rr = FakeReranker()
    r = ix.Retriever("markdown-plus", docs=list(CHUNKS), vs=FakeVS(CHUNKS), reranker=rr)
    plain = r.fused("Money Map Germany", None)
    info = {}
    got = r.retrieve("Money Map Germany", k=2, info=info)
    assert rr.seen[0][1] == plain[:2] and rr.seen[0][2] == 2, "the reranker sees the fused top candidates"
    assert [c.id for c in got] == list(reversed(plain[:2])) and info["reranked"]


def test_reranker_failure_keeps_the_fused_order(monkeypatch):
    class Broken:
        def rerank(self, *a, **k):
            raise RuntimeError("endpoint down")

    set_flags(monkeypatch, retrieval_figure_boost=False, reranker="fake")
    r = ix.Retriever("markdown-plus", docs=list(CHUNKS), vs=FakeVS(CHUNKS), reranker=Broken())
    info = {}
    got = r.retrieve("Money Map", k=2, info=info)
    assert len(got) == 2 and info["reranked"] is False and "endpoint down" in info["rerank_error"]


def test_flags_recorded_in_run_config(tmp_path, monkeypatch):
    from research_qa.evaluate import run as runner
    from research_qa.schema import Answer
    from research_qa.storage import LocalStore

    set_flags(monkeypatch, reranker="cohere", rerank_model="rerank-test")
    store = LocalStore(tmp_path / "s")
    key = runner.run(["markdown-plus"], ["direct"], ids=["MM04-01"], run_name="flags", store=store,
                     ask_fn=lambda q, a, m: Answer(arm=a, mode=m, question=q, text="x"),
                     judge_fn=lambda q, t: {"verdict": "incorrect", "reason": "fake"}, progress=None)
    cfg = store.json(f"{key}/config.json")
    assert cfg["retrieval"]["reranker"] == "cohere" and cfg["retrieval"]["rerank_model"] == "rerank-test"
    assert cfg["retrieval"]["figure_boost"] in (True, False) and cfg["retrieval"]["backend"] == settings.retrieval_backend


def test_azure_retrieve_body_filter():
    from research_qa.agent.direct import retrieve_body

    body = retrieve_body("markdown-plus", "q", doc_id="RA01220")
    assert body["knowledgeSourceParams"][0]["filterAddOn"] == "doc_id eq 'RA01220'"
    assert "filterAddOn" not in retrieve_body("word", "q", doc_id="RA01220")["knowledgeSourceParams"][0]
    assert "filterAddOn" not in retrieve_body("markdown-plus", "q")["knowledgeSourceParams"][0]
    assert json.dumps(body)  # serialisable
