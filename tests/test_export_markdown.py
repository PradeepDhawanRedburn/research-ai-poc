import json

import pytest

from research_qa.export_markdown import export_markdown
from research_qa.storage import LocalStore


def make_store(path):
    store = LocalStore(path)
    store.put("raw/RA1.docx", b"source")
    store.put("raw/RA2.docx", b"source")
    store.put("markdown/RA1.md", "# One")
    store.put("markdown/RA2.md", "# Two")
    store.put("markdown-stock/RA1.md", "# Stock one")
    store.put_json("facts/RA1.json", [{"figure": "Fig 1"}])
    store.put("chunks/RA1.jsonl", json.dumps({"id": "RA1-0", "text": "chunk"}) + "\n")
    store.put("assets/RA1/hash.png", b"png-bytes")
    store.put_json("descriptions/hash.v3.json", {"description": "picture"})
    store.put_json("manifest.json", {"RA1": {"doc_id": "RA1"}, "RA2": {"doc_id": "RA2"}})
    return store


def test_export_writes_current_documents_and_tracks_ownership(tmp_path):
    store = make_store(tmp_path / "store")
    output = tmp_path / "md-docs"
    result = export_markdown(store, output)
    assert result["doc_ids"] == ["RA1", "RA2"]
    assert (output / "markdown/RA1.md").read_text(encoding="utf-8") == "# One"
    assert json.loads((output / "facts/RA1.json").read_text(encoding="utf-8"))[0]["figure"] == "Fig 1"
    assert (output / "chunks/RA1.jsonl").exists()
    assert (output / "assets/RA1/hash.png").read_bytes() == b"png-bytes"
    assert (output / "descriptions/hash.v3.json").exists()
    assert not (output / "raw/RA1.docx").exists()
    assert not (output / "markdown-stock/RA1.md").exists()
    assert not (output / "source-assets/RA1/chart.emf").exists()
    assert set(json.loads((output / "manifest.json").read_text(encoding="utf-8"))) == {"RA1", "RA2"}
    assert (tmp_path / ".md-docs.export.json").exists()


def test_dry_run_does_not_write_and_clean_removes_only_owned_stale_files(tmp_path):
    store = make_store(tmp_path / "store")
    output = tmp_path / "md-docs"
    export_markdown(store, output)
    (output / "notes.md").write_text("keep", encoding="utf-8")
    store.delete("raw/RA2.docx")

    preview = export_markdown(store, output, dry_run=True)
    assert "markdown/RA2.md" in preview["removed"] and (output / "markdown/RA2.md").exists()
    export_markdown(store, output)
    assert not (output / "markdown/RA2.md").exists()
    assert not (output / "source-assets/RA2/chart.emf").exists()
    assert (output / "notes.md").read_text(encoding="utf-8") == "keep"


def test_export_refuses_to_overwrite_unowned_file(tmp_path):
    store = make_store(tmp_path / "store")
    output = tmp_path / "md-docs"
    output.mkdir()
    (output / "markdown").mkdir()
    (output / "markdown/RA1.md").write_text("user content", encoding="utf-8")
    with pytest.raises(FileExistsError, match="not owned"):
        export_markdown(store, output)