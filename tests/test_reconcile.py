"""Content-hash freshness and the removal of everything a deleted source document left behind."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from research_qa.ingest import pipeline, reconcile
from research_qa.lc import index as ix
from research_qa.storage import LocalStore


def seed(store: LocalStore, doc: str, raw: bytes | None = b"docx bytes"):
    if raw is not None:
        store.put(f"raw/{doc}.docx", raw, "application/octet-stream")
    store.put(f"markdown/{doc}.md", f"# {doc}", "text/markdown")
    store.put(f"markdown-stock/{doc}.md", f"# {doc} stock", "text/markdown")
    store.put(f"facts/{doc}.json", "[]", "application/json")
    store.put(f"chunks/{doc}.jsonl", json.dumps({"id": f"{doc}-00000", "text": "x"}) + "\n", "application/x-ndjson")
    store.put(f"assets/{doc}/abc.png", b"png", "image/png")
    store.put(f"assets/{doc}/def.png", b"png", "image/png")


def test_is_current_by_content_hash(tmp_path):
    store = LocalStore(tmp_path / "s")
    seed(store, "D1", b"version one")
    sha = hashlib.sha256(b"version one").hexdigest()
    man = {"D1": {"raw_sha": sha, "ingested_at": "2020-01-01T00:00:00+00:00"}}
    assert pipeline._is_current(store, "D1", man)          # same bytes: current, whatever the timestamps say
    store.put("raw/D1.docx", b"version two", "application/octet-stream")
    assert not pipeline._is_current(store, "D1", man)      # new bytes: not current
    assert not pipeline._is_current(store, "D2", man)      # unknown document
    assert pipeline.raw_sha(store, "D1") == hashlib.sha256(b"version two").hexdigest()


def test_entries_without_a_hash_fall_back_to_the_timestamp_rule(tmp_path):
    store = LocalStore(tmp_path / "s")
    seed(store, "D1")
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert pipeline._is_current(store, "D1", {"D1": {"ingested_at": future}})
    assert not pipeline._is_current(store, "D1", {"D1": {"ingested_at": past}})


def test_orphans_and_reconcile_remove_only_the_deleted_document(tmp_path):
    store = LocalStore(tmp_path / "s")
    seed(store, "KEEP")
    seed(store, "GONE", raw=None)
    store.put_json("manifest.json", {"KEEP": {"chunks": 1}, "GONE": {"chunks": 1}})
    store.put_json("descriptions/abc.v3.json", {"description": "shared by content hash"})
    assert reconcile.orphans(store) == ["GONE"]
    deleted = []
    dry = reconcile.reconcile(store, delete_index=lambda d: deleted.append(d) or 7, dry_run=True)
    assert set(dry) == {"GONE"} and len(dry["GONE"]["keys"]) == 6 and dry["GONE"]["manifest"]
    assert store.exists("chunks/GONE.jsonl") and deleted == [], "dry run removes nothing"
    res = reconcile.reconcile(store, delete_index=lambda d: deleted.append(d) or 7)
    assert deleted == ["GONE"] and res["GONE"]["index"] == 7
    for k in ("markdown/GONE.md", "markdown-stock/GONE.md", "facts/GONE.json", "chunks/GONE.jsonl",
              "assets/GONE/abc.png", "assets/GONE/def.png"):
        assert not store.exists(k), k
    for k in ("raw/KEEP.docx", "markdown/KEEP.md", "chunks/KEEP.jsonl", "assets/KEEP/abc.png",
              "descriptions/abc.v3.json"):
        assert store.exists(k), k
    assert store.json("manifest.json") == {"KEEP": {"chunks": 1}}
    assert reconcile.orphans(store) == [] and reconcile.reconcile(store, delete_index=lambda d: 0) == {}


def test_engine_failure_is_reported_not_raised(tmp_path):
    store = LocalStore(tmp_path / "s")
    seed(store, "GONE", raw=None)

    def boom(d):
        raise RuntimeError("search service unreachable")

    res = reconcile.reconcile(store, delete_index=boom)
    assert "search service unreachable" in res["GONE"]["index"] and not store.exists("chunks/GONE.jsonl")


class FakeVS:
    def __init__(self):
        self.rows = {"D1-00000": "D1", "D1-00001": "D1", "D2-00000": "D2"}
        self.deleted = []

    def get(self, where=None, include=None):
        return {"ids": [i for i, d in self.rows.items() if where is None or d == where.get("doc_id")]}

    def delete(self, ids):
        self.deleted.extend(ids)
        for i in ids:
            self.rows.pop(i, None)


def test_langchain_delete_doc(monkeypatch):
    vs = FakeVS()
    monkeypatch.setattr(ix, "vector_store", lambda arm: vs)
    monkeypatch.setattr(ix.Retriever, "reset", classmethod(lambda cls, arm=None: None))
    assert ix.delete_doc("markdown-plus", "D1") == 2 and sorted(vs.deleted) == ["D1-00000", "D1-00001"]
    assert ix.delete_doc("markdown-plus", "D1") == 0 and set(vs.rows) == {"D2-00000"}
