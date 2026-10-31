"""Reports: per report, what the conversion found: figures redrawn from the facts, pictures with their
descriptions, the Markdown+, the passages."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from research_qa.app import ui  # noqa: E402
from research_qa.config import settings  # noqa: E402
from research_qa.storage import CHUNKS, FACTS, MARKDOWN  # noqa: E402

ui.setup("Reports")
store = ui.require_store()
man = ui.manifest()
docs = store.doc_ids()

with st.sidebar:
    if docs:
        doc = st.radio("Report", docs, format_func=lambda d: ui.report_name(d, man.get(d, {})))
ui.sidebar_footer()

st.title("Reports")
if not docs:
    st.info("No Word files in the store. `python -m research_qa upload <files>` then `ingest`.")
    st.stop()
m = man.get(doc)
if not m:
    st.warning(f"{doc} is in the store but has not been converted. Run `python -m research_qa ingest --doc {doc}`.")
    st.stop()

# ----------------------------------------------------------------------------- cover

with st.container(border=True):
    st.markdown(f"### {ui.report_name(doc, m)}")
    line = [x for x in (m.get("title") if m.get("title") != ui.report_name(doc, m) else "", m.get("date"), m.get("source_file")) if x]
    st.markdown(f'<div class="muted">{" · ".join(line)}'
                + (f'<br>{", ".join(ui.person_name(a) for a in m.get("analysts", []))}' if m.get("analysts") else "") + "</div>",
                unsafe_allow_html=True)
    meta = m.get("metadata") or {}
    if meta:
        st.markdown(" · ".join(f"**{k}** {v}" for k, v in meta.items()))

ui.kpi_row([("Figures", str(m.get("figures", 0)), "charts, pictures, matrices"),
            ("Charts read", str(m.get("charts_native", 0) + m.get("charts_digitised_ok", 0)),
             f"{m.get('charts_native', 0)} native · {m.get('charts_digitised_ok', 0)} digitised"
             + (f" · {m.get('charts_digitised_withheld', 0)} withheld" if m.get("charts_digitised_withheld") else "")),
            ("Matrices", str(m.get("matrices", 0)), "tick/cross read with colour"),
            ("Pictures described", f"{m.get('pictures_described', 0)} / {m.get('pictures', 0)}",
             f"{m.get('vision_calls', 0)} vision calls at ingest"),
            ("Passages", str(m.get("chunks", 0)), ", ".join(f"{v} {k}" for k, v in sorted((m.get("chunk_kinds") or {}).items())))])
st.caption(f"Converted {str(m.get('ingested_at', ''))[:19]} in {m.get('seconds', 0):.0f} s · "
           f"{m.get('markdown_chars', 0):,} characters of Markdown+")
if m.get("warnings"):
    with st.expander(f"Conversion warnings ({len(m['warnings'])})"):
        for w in m["warnings"]:
            st.markdown(f"- {w}")

tab_figs, tab_pics, tab_md, tab_chunks, tab_word = st.tabs(
    ["Figures and exact values", "Pictures", "Markdown+", "Passages", "The Word arm"])

# ----------------------------------------------------------------------------- figures from the facts file

with tab_figs:
    key = f"{FACTS}/{doc}.json"
    facts = store.json(key) if store.exists(key) else []
    if not facts:
        st.caption("No facts file for this report.")
    else:
        st.markdown('<div class="muted">Every chart series and matrix cell the conversion read, as numbers and states. '
                    'These are what the Markdown+ agent looks up instead of reading a picture.</div>',
                    unsafe_allow_html=True)
        st.markdown("")
        names = [f"{f.get('figure')} · {(f.get('caption') or '')[:80]}" for f in facts]
        pick = st.selectbox("Figure", names, label_visibility="collapsed")
        f = facts[names.index(pick)]
        kind = {"native_chart": "native Word chart", "digitised_chart": "Excel chart pasted as a picture, digitised",
                "matrix": "tick/cross matrix", "table": "table"}.get(f.get("kind"), f.get("kind"))
        st.caption(f"{kind}" + (" · approximate (read from the drawing)" if f.get("approximate") else "")
                   + (f" · {f.get('method')}" if f.get("method") else "") + (f" · {f.get('note')}" if f.get("note") else ""))
        if f.get("series"):
            recs = []
            for s in f["series"]:
                for p in s.get("points", []):
                    recs.append({"series": s.get("name") or "series", "x": str(p[0]), "value": p[1],
                                 "axis": s.get("axis") or "left"})
            df = pd.DataFrame(recs)
            numeric_x = pd.to_numeric(df["x"], errors="coerce").notna().all()
            many = df.groupby("series")["x"].count().max() > 12
            base = alt.Chart(df).encode(
                x=alt.X("x:O" if not many else "x:N", title=None, sort=None,
                        axis=alt.Axis(labelAngle=0 if not many else -45, labelOverlap=True)),
                y=alt.Y("value:Q", title=(f["series"][0].get("unit") or None)),
                color=alt.Color("series:N", title=None, legend=alt.Legend(orient="top")),
                tooltip=["series:N", "x:N", alt.Tooltip("value:Q", format=",.2f")])
            chart = base.mark_line(point=not many, strokeWidth=2) if (many or numeric_x) else base.mark_bar()
            if not many and not numeric_x:
                chart = chart.encode(xOffset="series:N")
            st.altair_chart(chart.properties(height=320).configure_view(strokeWidth=0), width="stretch")
            with st.expander("The values"):
                for s in f["series"]:
                    st.markdown(f"**{s.get('name') or 'series'}**" + (f" ({s.get('unit')})" if s.get("unit") else "")
                                + f" · {len(s.get('points', []))} points")
                    st.dataframe([{"x": p[0], "value": p[1]} for p in s.get("points", [])], hide_index=True,
                                 height=min(38 + 35 * len(s.get("points", [])), 300))
        if f.get("cells"):
            cells = pd.DataFrame(f["cells"])
            sym = {"tick_green": "✓", "cross_red": "✗", "n/a": "n/a"}
            grid = cells.pivot_table(index="row", columns="col", values="state", aggfunc="first")
            st.dataframe(grid.map(lambda v: sym.get(v, v) if isinstance(v, str) else v), width="stretch")
            st.caption("✓ available (green tick in the report) · ✗ restricted (red cross) · n/a as printed")
        chunk_id = f.get("chunk_id")
        if chunk_id:
            ck = f"{CHUNKS}/{doc}.jsonl"
            c = next((c for c in store.jsonl(ck) if c.get("id") == chunk_id), None) if store.exists(ck) else None
            if c and c.get("assets"):
                with st.expander("The figure as it appears in the report"):
                    for k in c["assets"]:
                        ui.show_picture(k)

# ----------------------------------------------------------------------------- pictures

with tab_pics:
    ck = f"{CHUNKS}/{doc}.jsonl"
    chunks = store.jsonl(ck) if store.exists(ck) else []
    with_pics = [(c, k) for c in chunks for k in c.get("assets", [])]
    seen, gallery = set(), []
    for c, k in with_pics:
        if k not in seen:
            seen.add(k)
            gallery.append((c, k))
    if not gallery:
        st.caption("No pictures referenced by this report's passages.")
    else:
        only_described = st.toggle("Only pictures with a description", value=False)
        st.caption(f"{len(gallery)} pictures. A description means the vision model read the picture once at ingest; "
                   "charts read as numbers carry their values instead.")
        cols = st.columns(3)
        shown = 0
        for c, k in gallery:
            d = ui.description_of(k)
            if only_described and not d:
                continue
            with cols[shown % 3]:
                with st.container(border=True):
                    ui.show_picture(k, caption=f"{c.get('figure') or c.get('kind')} · {c.get('caption') or c.get('section') or ''}"[:120])
                    if d:
                        st.markdown(f'<div class="muted">{d[:700]}{"…" if len(d) > 700 else ""}</div>', unsafe_allow_html=True)
                    elif c.get("has_facts"):
                        st.caption("read as numbers (see Figures and exact values)")
            shown += 1
        if not shown:
            st.caption("No described pictures.")

# ----------------------------------------------------------------------------- markdown

with tab_md:
    key = f"{MARKDOWN}/{doc}.md"
    if store.exists(key):
        text = store.text(key)
        raw = st.toggle("Plain text", value=False)
        if raw:
            st.text(text)
        else:
            st.markdown(text)
    else:
        st.caption("No Markdown in the store for this report.")

# ----------------------------------------------------------------------------- passages

with tab_chunks:
    ck = f"{CHUNKS}/{doc}.jsonl"
    if store.exists(ck):
        chunks = store.jsonl(ck)
        kinds = sorted({c.get("kind", "") for c in chunks})
        sel = st.pills("Kinds", kinds, selection_mode="multi", default=kinds, label_visibility="collapsed")
        shown = [c for c in chunks if c.get("kind") in (sel or kinds)]
        st.dataframe([{"#": c.get("chunk"), "kind": c.get("kind"), "figure": c.get("figure"), "section": c.get("section"),
                       "caption": c.get("caption"), "chars": len(c.get("text", "")), "pictures": len(c.get("assets", [])),
                       "facts": "yes" if c.get("has_facts") else ""} for c in shown], hide_index=True, width="stretch",
                     height=320)
        n = st.number_input("Open passage #", min_value=0, max_value=max(len(chunks) - 1, 0), value=0)
        c = chunks[int(n)]
        st.text(c.get("text", ""))
        for k in c.get("assets", []):
            ui.show_picture(k)
    else:
        st.caption("No passages in the store for this report.")

# ----------------------------------------------------------------------------- the word arm

with tab_word:
    if settings.retrieval_backend == "azure":
        st.markdown('<div class="muted">The Word arm is Azure AI Search\'s own ingestion of the .docx files. This is '
                    'what the service reports about it.</div>', unsafe_allow_html=True)
        try:
            from research_qa.search.knowledge import blob_ingestion_status

            st.json(blob_ingestion_status("word"))
        except ImportError:
            st.caption("Azure packages are not installed in this environment.")
        except Exception as e:  # noqa: BLE001
            st.caption(f"Not available: {type(e).__name__}: {str(e)[:200]}")
    else:
        from research_qa.storage import Store  # noqa: F401

        key = f"markdown-stock/{doc}.md"
        st.markdown('<div class="muted">With the LangChain engine the Word arm is the stock MarkItDown conversion: '
                    'text and tables, no chart values, no matrix states, no picture descriptions. This is the text it '
                    'searches.</div>', unsafe_allow_html=True)
        if store.exists(key):
            text = store.text(key)
            st.caption(f"{len(text):,} characters against {m.get('markdown_chars', 0):,} for Markdown+")
            st.text(text[:20000] + ("\n…" if len(text) > 20000 else ""))
        else:
            st.caption("Not built yet: `python -m research_qa index` creates it.")
