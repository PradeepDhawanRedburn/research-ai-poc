"""The ingest step: raw Word in the store → Markdown+, assets, facts, chunks, manifest (docs/DESIGN.md §3.2).

    convert (MarkItDown + plugin) → describe pictures → facts → chunks → markdown with descriptions → manifest

Idempotent: a report is skipped when its manifest entry is newer than the raw file, unless `force`. Picture
descriptions are cached per picture, so a re-run costs no vision calls.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from research_ingest.render import render_document

from ..schema import ReportManifest
from ..storage import CHUNKS, FACTS, MANIFEST, MARKDOWN, RAW, Store
from .chunks import build_chunks
from .convert import Converted, convert
from .describe import describe
from .elements import clean_text, pictures_of
from .facts import facts_for, picture_facts

log = logging.getLogger(__name__)


def _needs_description(conv: Converted, pic: dict, vision: str) -> bool:
    """"auto": pictures without digitised values that passed the gate; "all": every uploaded picture."""
    sha = pic.get("asset") or ""
    if sha not in conv.assets:
        return False
    if vision == "all":
        return True
    return conv.asset_info[sha].digitised_quality != "ok"


def _picture_items(conv: Converted, vision: str) -> list[tuple[str, str]]:
    """(asset key, context) for the pictures to describe: caption plus the nearest paragraph before the figure."""
    items: list[tuple[str, str]] = []
    elements = conv.doc.get("elements", [])
    last_text = ""
    for el in elements:
        if el.get("type") in ("paragraph", "list_item") and el.get("text"):
            last_text = clean_text(el["text"])
        if el.get("type") != "figure":
            continue
        cap = " ".join(x for x in (el.get("figure_no"), clean_text(el.get("caption"))) if x)
        src = f"Source: {el['source']}" if el.get("source") else ""
        for pic in pictures_of(el):
            if _needs_description(conv, pic, vision):
                ctx = "\n".join(x for x in (cap, src, last_text[-600:]) if x)
                items.append((conv.assets[pic["asset"]], ctx))
    return items


def _inject_descriptions(conv: Converted, descriptions: dict[str, dict]) -> int:
    """Put each description on its picture drawing so the Markdown renderer prints it."""
    n = 0
    for el in conv.doc.get("elements", []):
        if el.get("type") != "figure":
            continue
        for pic in pictures_of(el):
            key = conv.assets.get(pic.get("asset") or "")
            rec = descriptions.get(key) if key else None
            if rec and rec.get("description"):
                pic["description"] = rec["description"]
                n += 1
    return n


def _manifest_entry(conv: Converted, descriptions: dict, chunks, facts, seconds: float) -> ReportManifest:
    md = conv.doc.get("metadata", {})
    figures = [e for e in conv.doc.get("elements", []) if e.get("type") == "figure"]
    pics = [p for e in figures for p in pictures_of(e) if (p.get("asset") or "") in conv.assets]
    kinds: dict[str, int] = {}
    for c in chunks:
        kinds[c.kind] = kinds.get(c.kind, 0) + 1
    info = conv.asset_info
    return ReportManifest(
        doc_id=conv.doc_id, source_file=f"{conv.doc_id}.docx", title=md.get("title", "") or "",
        subtitle=md.get("subtitle", "") or "", date=md.get("date", "") or "",
        analysts=[f"{a.get('name') or ''} <{a['email']}>".strip() for a in md.get("analysts", [])],
        metadata={k: v for k, v in md.items() if isinstance(v, str) and k not in ("doc_id", "source_file")},
        ingested_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=round(seconds, 1),
        chunks=len(chunks), chunk_kinds=kinds, figures=len(figures),
        charts_native=sum(1 for e in figures for c in e.get("content", []) if c.get("kind") == "chart"),
        charts_digitised_ok=sum(1 for a in info.values() if a.digitised_quality == "ok"),
        charts_digitised_withheld=sum(1 for a in info.values() if a.digitised_quality == "low"),
        pictures=len(pics),
        pictures_described=sum(1 for p in pics if conv.assets[p["asset"]] in descriptions),
        vision_calls=sum(1 for d in descriptions.values() if d.get("called")),
        matrices=kinds.get("matrix", 0), tables=kinds.get("table", 0), facts_figures=len(facts),
        warnings=list(conv.warnings), markdown_chars=len(conv.markdown),
    )


def _is_current(store: Store, doc_id: str, manifest: dict) -> bool:
    entry = manifest.get(doc_id)
    if not entry or not store.exists(f"{MARKDOWN}/{doc_id}.md") or not store.exists(f"{CHUNKS}/{doc_id}.jsonl"):
        return False
    raw = next((e for e in store.list(f"{RAW}/{doc_id}.docx") if e.key == f"{RAW}/{doc_id}.docx"), None)
    if raw is None or raw.modified is None:
        return True
    try:
        return datetime.fromisoformat(entry["ingested_at"]) >= raw.modified
    except (KeyError, ValueError):
        return False


def ingest_one(doc_id: str, store: Store, vision: str = "auto") -> ReportManifest:
    t0 = time.perf_counter()
    conv = convert(doc_id, store)
    descriptions = describe(_picture_items(conv, vision), store, vision)
    _inject_descriptions(conv, descriptions)
    facts = facts_for(conv.doc, doc_id) + picture_facts(conv.doc, doc_id, descriptions, conv.assets)
    chunks = build_chunks(conv.doc, doc_id, f"{doc_id}.docx", descriptions, conv.assets, facts)
    markdown = render_document(conv.doc)
    conv.markdown = markdown
    store.put(f"{MARKDOWN}/{doc_id}.md", markdown, "text/markdown")
    store.put_json(f"{FACTS}/{doc_id}.json", [f.to_dict() for f in facts])
    store.put_jsonl(f"{CHUNKS}/{doc_id}.jsonl", (c.to_dict() for c in chunks))
    return _manifest_entry(conv, descriptions, chunks, facts, time.perf_counter() - t0)


def ingest(doc_ids: list[str] | None, store: Store, vision: str = "auto", force: bool = False) -> list[ReportManifest]:
    """Ingest the given reports (all `raw/*.docx` when None). Returns the manifest entries written this run."""
    manifest = store.json(MANIFEST, default={})
    ids = doc_ids or store.doc_ids()
    done: list[ReportManifest] = []
    for doc_id in ids:
        if not store.exists(f"{RAW}/{doc_id}.docx"):
            print(f"[{doc_id}] no raw/{doc_id}.docx in the store, skipped")
            continue
        if not force and _is_current(store, doc_id, manifest):
            print(f"[{doc_id}] up to date, skipped (use force to redo)")
            continue
        m = ingest_one(doc_id, store, vision)
        manifest[doc_id] = m.to_dict()
        store.put_json(MANIFEST, manifest)
        done.append(m)
        print(f"[{doc_id}] {m.title}: {m.chunks} chunks {m.chunk_kinds}; {m.figures} figures, "
              f"{m.charts_native} native charts, {m.charts_digitised_ok} digitised ({m.charts_digitised_withheld} "
              f"withheld), {m.pictures_described}/{m.pictures} pictures described ({m.vision_calls} vision calls), "
              f"{m.facts_figures} fact figures, {len(m.warnings)} warnings, {m.seconds}s")
    return done
