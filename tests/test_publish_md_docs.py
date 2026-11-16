from pathlib import Path

import research_ingest.extract as extractor
from research_qa.ingest.publish_md_docs import publish_md_docs
from research_qa.storage import LocalStore


def test_publish_writes_legacy_layout_and_prunes_stale_report_blobs(tmp_path, monkeypatch):
    source = LocalStore(tmp_path / "source")
    target = LocalStore(tmp_path / "target")
    source.put("raw/RA01220.docx", b"source-docx")
    source.put("markdown/RA01220.md", "# Money Map")
    source.put("assets/RA01220/hash.png", b"raster-png")
    source.put_json("facts/RA01220.json", [{"figure_id": "RA01220:1:12345678", "figure": "Fig 1"}])
    source.put("chunks/RA01220.jsonl", '{"id":"RA01220-0","figure_id":"RA01220:1:12345678"}\n')
    source.put_json("descriptions/hash.v3.json", {"description": "picture summary"})
    source.put_json("manifest.json", {"RA01220": {"title": "Money Map", "seconds": 3.2,
                                                    "metadata": {"analysts": ["Analyst"],
                                                                 "metadata_sources": {"analysts": "sharepoint"}},
                                                    "chart_quality_failures": []}})
    target.put("RA09999.md", "old")
    target.put("RA09999/canonical.json", "{}")
    target.put("RA09999/assets/old.emf", b"old")

    def fake_extract(path, out_dir, doc_id=None):
        asset_dir = Path(out_dir) / "assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        (asset_dir / "original.emf").write_bytes(b"emf-bytes")
        return {"doc_id": doc_id, "metadata": {"title": "Money Map"}, "elements": [],
            "assets": {"hash": {"file": "original.emf", "ext": "emf"}}, "stats": {}}

    monkeypatch.setattr(extractor, "extract", fake_extract)
    preview = publish_md_docs(source, target, dry_run=True)
    assert "RA01220.md" in preview["written"]
    assert "RA01220/canonical.json" in preview["written"]
    assert "RA09999.md" in preview["removed"]
    assert target.exists("RA09999.md"), "dry-run must not mutate the target"

    result = publish_md_docs(source, target)
    assert (target.text("RA01220.md")) == "# Money Map"
    canonical = target.json("RA01220/canonical.json")
    assert canonical["metadata"]["title"] == "Money Map"
    assert target.json("RA01220/metadata.json")["metadata_sources"]["analysts"] == "sharepoint"
    assert target.json("RA01220/facts.json")[0]["figure_id"] == "RA01220:1:12345678"
    assert target.jsonl("RA01220/chunks.jsonl")[0]["figure_id"] == "RA01220:1:12345678"
    assert target.json("RA01220/manifest.json")["metadata"]["analysts"] == ["Analyst"]
    assert target.get("RA01220/assets/original.emf") == b"emf-bytes"
    assert target.get("RA01220/assets/rendered/hash.png") == b"raster-png"
    assert target.json("RA01220/assets/descriptions/hash.v3.json")["description"] == "picture summary"
    assert target.json("report.json") == [{"doc": "RA01220", "seconds": 3.2, "chars": 11, "title": "Money Map"}]
    assert not target.exists("RA09999.md")
    assert not target.exists("RA09999/canonical.json")
    assert result["removed"]
