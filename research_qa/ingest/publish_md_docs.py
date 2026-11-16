"""Publish the legacy Markdown+ layout to a separate Blob container."""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path, PurePosixPath

from ..storage import ASSETS, CHUNKS, FACTS, MANIFEST, MARKDOWN, RAW, Store

REPORT_ID_RE = re.compile(r"^RA\d+$")


def _doc_id_for_legacy_key(key: str) -> str | None:
    parts = PurePosixPath(key).parts
    if len(parts) == 1 and parts[0].endswith(".md"):
        candidate = parts[0][:-3]
        return candidate if REPORT_ID_RE.fullmatch(candidate) else None
    if parts and REPORT_ID_RE.fullmatch(parts[0]) and len(parts) > 1:
        return parts[0]
    return None


def publish_md_docs(source: Store, target: Store, doc_ids: list[str] | None = None,
                    dry_run: bool = False) -> dict:
    """Publish root markdown and the Markdown+ canonical, metadata, facts, chunks, and assets per report.

    Source raw DOCX and Markdown+ outputs stay in the configured system-of-record store.
    Only blobs in the legacy report layout are pruned from the target.
    """
    from research_ingest.extract import extract

    all_doc_ids = set(source.doc_ids())
    selected = set(doc_ids or all_doc_ids)
    unknown = selected - all_doc_ids
    if unknown:
        raise ValueError(f"reports not present in the source store: {sorted(unknown)}")
    if not selected:
        return {"documents": [], "written": [], "removed": [], "dry_run": dry_run}

    existing_report = target.json("report.json", default=[]) if target.exists("report.json") else []
    existing_rows = {row.get("doc"): row for row in existing_report if isinstance(row, dict) and row.get("doc")}
    preserved_rows = [row for doc_id, row in existing_rows.items() if doc_id not in selected and doc_id in all_doc_ids]
    report_rows = list(preserved_rows)
    source_manifest = source.json(MANIFEST, {}) if source.exists(MANIFEST) else {}
    staged: dict[str, Path] = {}

    with tempfile.TemporaryDirectory(prefix="research_md_docs_") as temp_dir:
        stage = Path(temp_dir)
        for doc_id in sorted(selected):
            markdown_key = f"{MARKDOWN}/{doc_id}.md"
            raw_key = f"{RAW}/{doc_id}.docx"
            if not source.exists(markdown_key) or not source.exists(raw_key):
                raise ValueError(f"report {doc_id} is missing raw DOCX or Markdown+ output")

            report_markdown = source.text(markdown_key)
            staged_markdown = stage / f"{doc_id}.md"
            staged_markdown.write_text(report_markdown, encoding="utf-8")
            staged[f"{doc_id}.md"] = staged_markdown

            canonical = extract(str(source.local_path(raw_key)), str(stage / doc_id), doc_id=doc_id)
            entry = source_manifest.get(doc_id, {})
            canonical_path = stage / doc_id / "canonical.json"
            canonical_path.parent.mkdir(parents=True, exist_ok=True)
            canonical_path.write_text(json.dumps(canonical, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            staged[f"{doc_id}/canonical.json"] = canonical_path

            metadata_path = stage / doc_id / "metadata.json"
            metadata = {**entry.get("metadata", {}), **canonical.get("metadata", {})}
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")
            staged[f"{doc_id}/metadata.json"] = metadata_path

            for key, filename in ((f"{FACTS}/{doc_id}.json", "facts.json"),
                                  (f"{CHUNKS}/{doc_id}.jsonl", "chunks.jsonl")):
                if source.exists(key):
                    artifact_path = stage / doc_id / filename
                    artifact_path.parent.mkdir(parents=True, exist_ok=True)
                    artifact_path.write_bytes(source.get(key))
                    staged[f"{doc_id}/{filename}"] = artifact_path

            if doc_id in source_manifest:
                manifest_path = stage / doc_id / "manifest.json"
                manifest_path.write_text(json.dumps(source_manifest[doc_id], ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8")
                staged[f"{doc_id}/manifest.json"] = manifest_path

            for info in canonical.get("assets", {}).values():
                filename = info.get("file")
                if filename:
                    asset_path = stage / doc_id / "assets" / PurePosixPath(filename).name
                    if asset_path.is_file():
                        staged[f"{doc_id}/assets/{asset_path.name}"] = asset_path

            for key in source.keys(f"{ASSETS}/{doc_id}/"):
                filename = PurePosixPath(key).name
                asset_path = stage / doc_id / "assets" / "rendered" / filename
                asset_path.parent.mkdir(parents=True, exist_ok=True)
                asset_path.write_bytes(source.get(key))
                staged[f"{doc_id}/assets/rendered/{filename}"] = asset_path

            asset_hashes = set(canonical.get("assets", {}))
            for key in source.keys("descriptions/"):
                filename = PurePosixPath(key).name
                if any(filename.startswith(f"{asset_hash}.") for asset_hash in asset_hashes):
                    description_path = stage / doc_id / "assets" / "descriptions" / filename
                    description_path.parent.mkdir(parents=True, exist_ok=True)
                    description_path.write_bytes(source.get(key))
                    staged[f"{doc_id}/assets/descriptions/{filename}"] = description_path

            report_rows.append({"doc": doc_id, "seconds": entry.get("seconds", 0), "chars": len(report_markdown),
                                "title": metadata.get("title", "")})

        report_path = stage / "report.json"
        report_path.write_text(json.dumps(sorted(report_rows, key=lambda row: row["doc"]), ensure_ascii=False,
                                          indent=2) + "\n", encoding="utf-8")
        staged["report.json"] = report_path

        expected = set(staged)
        stale = []
        for key in target.keys():
            if key == "report.json" or _doc_id_for_legacy_key(key):
                doc_id = _doc_id_for_legacy_key(key)
                if key not in expected and (doc_id is None or doc_id in selected or doc_id not in all_doc_ids):
                    stale.append(key)
        result = {"documents": sorted(selected), "written": sorted(expected), "removed": sorted(stale),
                  "dry_run": dry_run}
        if dry_run:
            return result

        for key, path in staged.items():
            target.upload_file(path, key)
        for key in stale:
            target.delete(key)
    return result
