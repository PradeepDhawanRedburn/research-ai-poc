"""Deleted source documents, handled explicitly.

A report is "in the system" when `raw/<doc>.docx` exists. Everything else about it is derived: Markdown, the
stock conversion, assets, facts, chunks, the manifest entry, and its chunks in whichever retrieval engine is
configured. When the raw file is removed, `reconcile()` removes all of that; nothing else does. Picture
descriptions (`descriptions/<sha>.*.json`) are keyed by picture content, shared across reports, and kept.
"""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Callable

from ..config import ARMS, settings
from ..storage import ASSETS, CHUNKS, FACTS, MANIFEST, MARKDOWN, Store

STOCK = "markdown-stock"
PER_DOC_FILES = (f"{MARKDOWN}/{{d}}.md", f"{STOCK}/{{d}}.md", f"{FACTS}/{{d}}.json", f"{CHUNKS}/{{d}}.jsonl")
PER_DOC_PREFIXES = (f"{ASSETS}/{{d}}/",)


def known_docs(store: Store) -> set[str]:
    """Every doc id that has derived data anywhere in the store."""
    ids: set[str] = set()
    for prefix, suffix in ((MARKDOWN, ".md"), (STOCK, ".md"), (FACTS, ".json"), (CHUNKS, ".jsonl")):
        ids.update(PurePosixPath(k).stem for k in store.keys(f"{prefix}/", suffix))
    ids.update(k[len(ASSETS) + 1:].split("/", 1)[0] for k in store.keys(f"{ASSETS}/") if k.count("/") >= 2)
    ids.update((store.json(MANIFEST, {}) if store.exists(MANIFEST) else {}).keys())
    return {i for i in ids if i}


def orphans(store: Store) -> list[str]:
    """Doc ids with derived data but no raw/<doc>.docx."""
    return sorted(known_docs(store) - set(store.doc_ids()))


def engine_deleter() -> Callable[[str], int]:
    """Deletes a document's chunks from the configured retrieval engine; returns the number removed."""
    if settings.retrieval_backend == "langchain":
        from ..lc.index import delete_doc

        return lambda doc_id: sum(delete_doc(arm, doc_id) for arm in ARMS)
    from ..search.index import delete_doc as azure_delete

    return azure_delete


def reconcile(store: Store, delete_index: Callable[[str], int] | None = None, dry_run: bool = False) -> dict:
    """Remove every trace of documents whose raw file is gone. Returns {doc_id: {"keys": [...], "index": n|error}}.
    `delete_index(doc_id)` defaults to the configured engine's deleter; pass one for tests or to skip the engine."""
    out: dict[str, dict] = {}
    manifest = store.json(MANIFEST, {}) if store.exists(MANIFEST) else {}
    for d in orphans(store):
        keys = [k.format(d=d) for k in PER_DOC_FILES if store.exists(k.format(d=d))]
        for prefix in PER_DOC_PREFIXES:
            keys.extend(store.keys(prefix.format(d=d)))
        rec: dict = {"keys": keys, "manifest": d in manifest, "index": None}
        if not dry_run:
            for k in keys:
                store.delete(k)
            if d in manifest:
                manifest.pop(d)
                store.put_json(MANIFEST, manifest)
            try:
                deleter = delete_index if delete_index is not None else engine_deleter()
                rec["index"] = deleter(d)
            except Exception as e:  # noqa: BLE001 - the store is clean; the engine failure is reported
                rec["index"] = f"{type(e).__name__}: {str(e)[:200]}"
        out[d] = rec
    return out
