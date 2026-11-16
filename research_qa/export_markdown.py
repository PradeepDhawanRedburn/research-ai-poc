"""Export current Markdown+ artifacts from the configured store to a local folder."""
from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath

from .storage import ASSETS, CHUNKS, FACTS, MANIFEST, MARKDOWN, Store

DOC_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _output_path(root: Path, relative: str) -> Path:
    if "\\" in relative:
        raise ValueError(f"unsafe export path in ownership manifest: {relative!r}")
    parts = PurePosixPath(relative).parts
    if not parts or PurePosixPath(relative).is_absolute() or any(part in (".", "..") for part in parts):
        raise ValueError(f"unsafe export path in ownership manifest: {relative!r}")
    destination = root.joinpath(*parts).resolve()
    if root not in destination.parents:
        raise ValueError(f"export path escapes output directory: {relative!r}")
    return destination


def export_markdown(store: Store, output: Path = Path("md-docs"), dry_run: bool = False) -> dict:
    output = Path(output).resolve()
    marker = output.parent / f".{output.name}.export.json"
    current = set(store.doc_ids())
    missing = sorted(doc_id for doc_id in current if not store.exists(f"{MARKDOWN}/{doc_id}.md"))
    if missing:
        raise ValueError("Markdown is missing for source documents: " + ", ".join(missing))
    if any(not DOC_ID_RE.fullmatch(doc_id) for doc_id in current):
        raise ValueError("one or more document IDs cannot be represented safely as filenames")

    keys = []
    for doc_id in sorted(current):
        keys.extend((f"{MARKDOWN}/{doc_id}.md", f"{FACTS}/{doc_id}.json", f"{CHUNKS}/{doc_id}.jsonl"))
        keys.extend(store.keys(f"{ASSETS}/{doc_id}/"))
    asset_hashes = {PurePosixPath(key).stem for key in keys if key.startswith(f"{ASSETS}/")}
    keys.extend(key for key in store.keys("descriptions/")
                if PurePosixPath(key).stem.rsplit(".", 1)[0] in asset_hashes)
    keys.append(MANIFEST)
    keys = sorted(set(key for key in keys if store.exists(key)))
    files = set(keys)

    previous_manifest = json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else {}
    previous = set(previous_manifest.get("files", []))
    if not previous and previous_manifest.get("doc_ids"):
        previous = {f"{doc_id}.md" for doc_id in previous_manifest["doc_ids"]}
    collisions = sorted(path for path in files - previous if _output_path(output, path).exists())
    if collisions:
        raise FileExistsError("refusing to overwrite files not owned by this exporter: " + ", ".join(collisions))

    removed = sorted(previous - files)
    result = {"output": str(output), "doc_ids": sorted(current), "written": sorted(files),
             "removed": removed, "dry_run": dry_run}
    if dry_run:
        return result

    output.mkdir(parents=True, exist_ok=True)
    for key in keys:
        if key == MANIFEST:
            data = store.json(MANIFEST, {})
            data = {doc_id: data[doc_id] for doc_id in sorted(current) if doc_id in data}
            payload = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
        else:
            payload = store.get(key)
        destination = _output_path(output, key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
    for relative in removed:
        stale = _output_path(output, relative)
        if stale.exists():
            stale.unlink()
            parent = stale.parent
            while parent != output and parent.is_relative_to(output):
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
    tmp = marker.with_suffix(marker.suffix + ".tmp")
    tmp.write_text(json.dumps({"doc_ids": sorted(current), "files": sorted(files)}, indent=2) + "\n", encoding="utf-8")
    tmp.replace(marker)
    return result