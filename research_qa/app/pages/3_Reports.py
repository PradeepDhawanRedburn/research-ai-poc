"""Reports: per report, what the conversion found, the Markdown, the facts, the chunks, and the Word arm's status."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import streamlit as st  # noqa: E402

from research_qa.app import ui  # noqa: E402
from research_qa.config import settings  # noqa: E402
from research_qa.storage import CHUNKS, FACTS, MARKDOWN  # noqa: E402

ui.setup("Reports")
st.title("Reports")
ui.store_note()
store = ui.store()
man = ui.manifest()
docs = store.doc_ids()
if not docs:
    st.info("No Word files in raw/. Upload with `python -m research_qa upload <files>` and run `ingest`.")
    st.stop()

with st.expander("Word arm: Azure's own ingestion of raw/"):
    try:
        from research_qa.search.knowledge import blob_ingestion_status

        st.json(blob_ingestion_status("word"))
    except ImportError:
        st.caption("Azure packages not installed in this environment.")
    except Exception as e:  # noqa: BLE001
        st.caption(f"Not available: {type(e).__name__}: {str(e)[:200]}")

doc = st.selectbox("Report", docs, format_func=lambda d: f"{d} · {man.get(d, {}).get('title', '')}".strip(" ·"))
m = man.get(doc)
if not m:
    st.warning(f"{doc} is in raw/ but has not been converted. Run `python -m research_qa ingest --doc {doc}`.")
    st.stop()

st.subheader(m.get("title") or doc)
st.caption(" · ".join(x for x in (m.get("subtitle"), m.get("date"), ", ".join(m.get("analysts", [])),
                                  m.get("source_file")) if x))
meta = m.get("metadata") or {}
if meta:
    st.dataframe([{"field": k, "value": v} for k, v in meta.items()], hide_index=True, width="stretch")

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Chunks", m.get("chunks", 0))
c2.metric("Figures", m.get("figures", 0))
c3.metric("Native charts", m.get("charts_native", 0), help="series read from the Word chart XML")
c4.metric("Charts digitised", m.get("charts_digitised_ok", 0),
          help=f"pasted Excel charts read from the drawing; {m.get('charts_digitised_withheld', 0)} withheld as low confidence")
c5.metric("Pictures described", f"{m.get('pictures_described', 0)} / {m.get('pictures', 0)}",
          help=f"{m.get('vision_calls', 0)} vision calls at ingest")
c6.metric("Matrices", m.get("matrices", 0), help="tick/cross tables read with colour")
st.caption(f"Converted {m.get('ingested_at', '')[:19]} in {m.get('seconds', 0):.0f} s · "
           f"{m.get('markdown_chars', 0):,} characters of Markdown · chunk kinds: "
           + ", ".join(f"{v} {k}" for k, v in sorted((m.get("chunk_kinds") or {}).items())))
if m.get("warnings"):
    with st.expander(f"Warnings ({len(m['warnings'])})"):
        for w in m["warnings"]:
            st.markdown(f"- {w}")

tab_facts, tab_md, tab_chunks = st.tabs(["Facts (exact values)", "Markdown+", "Chunks"])

with tab_facts:
    key = f"{FACTS}/{doc}.json"
    facts = store.json(key) if store.exists(key) else []
    if not facts:
        st.caption("No facts file for this report.")
    else:
        st.dataframe([{"figure": f.get("figure"), "caption": f.get("caption"), "kind": f.get("kind"),
                       "series": len(f.get("series", [])), "cells": len(f.get("cells", [])),
                       "approximate": "yes" if f.get("approximate") else "", "method": f.get("method", ""),
                       "note": f.get("note", "")} for f in facts], hide_index=True, width="stretch")
        pick = st.selectbox("Figure", [f"{f.get('figure')} · {f.get('caption', '')[:70]}" for f in facts])
        f = facts[[f"{x.get('figure')} · {x.get('caption', '')[:70]}" for x in facts].index(pick)]
        if f.get("series"):
            for s in f["series"]:
                st.markdown(f"**{s.get('name') or 'series'}**" + (f" ({s.get('unit')})" if s.get("unit") else "")
                            + (f" · {s.get('axis')} axis" if s.get("axis") else "") + f" · {len(s.get('points', []))} points")
                st.dataframe([{"x": p[0], "value": p[1]} for p in s.get("points", [])], hide_index=True, height=240)
        if f.get("cells"):
            st.dataframe(f["cells"], hide_index=True, width="stretch")

with tab_md:
    key = f"{MARKDOWN}/{doc}.md"
    if store.exists(key):
        text = store.text(key)
        raw = st.checkbox("Show as plain text", value=False)
        st.text(text) if raw else st.markdown(text)
    else:
        st.caption("No Markdown in the store for this report.")

with tab_chunks:
    key = f"{CHUNKS}/{doc}.jsonl"
    if store.exists(key):
        chunks = store.jsonl(key)
        kinds = sorted({c.get("kind", "") for c in chunks})
        sel = st.multiselect("Kinds", kinds, default=kinds)
        shown = [c for c in chunks if c.get("kind") in sel]
        st.dataframe([{"#": c.get("chunk"), "kind": c.get("kind"), "figure": c.get("figure"), "section": c.get("section"),
                       "caption": c.get("caption"), "chars": len(c.get("text", "")), "pictures": len(c.get("assets", [])),
                       "facts": "yes" if c.get("has_facts") else ""} for c in shown], hide_index=True, width="stretch")
        n = st.number_input("Open chunk #", min_value=0, max_value=max(len(chunks) - 1, 0), value=0)
        c = chunks[int(n)]
        st.text(c.get("text", ""))
        for k in c.get("assets", []):
            ui.show_picture(k)
    else:
        st.caption("No chunks in the store for this report.")

st.caption(f"Chunk size {settings.chunk_chars} characters · store {store.describe()}")
