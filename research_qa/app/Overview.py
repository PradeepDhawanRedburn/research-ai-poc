"""Overview: the question in one paragraph, the answer in a few numbers, the pipeline in one figure, the corpus.

Start with:  streamlit run research_qa/app/Overview.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root, so `research_qa` imports under streamlit

import streamlit as st  # noqa: E402

from research_qa.app import ui  # noqa: E402
from research_qa.config import ARMS, combo_key, settings  # noqa: E402
from research_qa.evaluate.run import list_runs, load_run  # noqa: E402

ui.setup("Overview")
store = ui.require_store()
ui.sidebar_footer()

st.title("Research reports, asked in plain language")
st.markdown("---")
st.caption("Built by Christopher Penney, Klaudia Lemiec and Pradeep Dhawan.")
st.markdown('<p class="lede">Analysts write in Word: prose, tables, charts, pictures, tick-and-cross matrices. '
            'This system makes those reports answerable with exact, cited answers. It compares two ways in on the '
            'same questions and the same model: <b>the default conversion of the Word file</b>, and '
            '<b>Markdown+</b>, a conversion that reads charts as numbers, matrices as states and describes every '
            'picture once.</p>', unsafe_allow_html=True)

# ----------------------------------------------------------------------------- the answer in numbers

runs: list[str] = []
latest: dict | None = None
try:
    # the newest full run of the engine in use that covers every arm; else any such run; else the newest one
    cands = [r for r in ui.current_runs() if r["kind"] == "full" and set(ARMS) <= set(r["arms"])]
    latest = next((r for r in cands if r["backend"] == settings.retrieval_backend), cands[0] if cands else None)
    runs = [latest["name"]] if latest else list_runs(store)[:1]
except Exception as e:  # noqa: BLE001
    st.warning(f"Could not list evaluation runs: {type(e).__name__}: {str(e)[:160]}")

summary: dict = {}
config: dict = {}
if runs:
    summary, _rows, config = load_run(runs[0], store)

st.markdown("## The answer in numbers")
if not summary:
    st.info("No evaluation run in the store yet. `python -m research_qa eval` scores the verified questions and "
            "fills this section.")
else:
    mode = "agentic" if combo_key("markdown-plus", "agentic") in summary.get("per_combo", {}) else (config.get("modes") or ["agentic"])[0]
    cols = st.columns(len(ARMS))
    for col, arm in zip(cols, ARMS):
        s = summary.get("per_combo", {}).get(combo_key(arm, mode))
        with col:
            with st.container(border=True):
                ui.tile_header(arm, mode, ARMS[arm].label)
                if not s or not s["n"]:
                    st.caption("not in this run")
                    continue
                ui.kpi("Accuracy", ui.pct(s["accuracy"]), f"{s['correct']} correct, {s['partial']} partial of {s['n']} questions",
                       accent=arm == "markdown-plus")
                st.markdown("")
                ui.kpi_row([("Evidence found", ui.pct(s["evidence_found"]), "in what it read"),
                            ("Per question", f"{s['time']['total_s']['mean']:.1f} s",
                             f"{s['tokens']['total_tokens']['mean']:,.0f} tokens")])
    st.caption(f"Run {runs[0]} · {ui.ENGINE_NAME.get((config.get('retrieval') or {}).get('backend'), 'engine not recorded')} · "
               f"{summary.get('questions', 0)} verified questions · {ui.MODE_LABEL[mode]} mode · "
               f"model {config.get('answer_model', '')} · judged by {config.get('judge', '')}")
    st.page_link("pages/2_Scorecard.py", label="See the scorecard by question type", icon=":material/table_chart:")

# ----------------------------------------------------------------------------- the pipeline


def pipeline_figure() -> str:
    """Word in Blob forks into two routes that meet again at one model."""
    g, t, n = ui.SLATE, ui.TEAL, ui.NAVY
    word_route = ("Azure AI Search Blob ingestion" if settings.retrieval_backend == "azure" else "Stock MarkItDown")
    word_sub = ("cracks, chunks, embeds; describes pictures" if settings.retrieval_backend == "azure"
                else "text and tables; charts and matrices are lost")
    engine = ("knowledge bases" if settings.retrieval_backend == "azure" else "search collections")
    return f"""
<figure style="margin:0">
<svg viewBox="0 0 1180 320" role="img" style="max-width:100%;height:auto;font-family:inherit"
     aria-label="Word reports flow through two routes, the default conversion and Markdown+, into two {engine} answered by the same model.">
  <defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
    <path d="M0,0 L10,5 L0,10 z" fill="{g}"/></marker></defs>
  <g font-size="13" fill="{ui.INK}">
    <rect x="20" y="120" width="160" height="80" rx="10" fill="{ui.LIGHT}" stroke="{ui.LINE}"/>
    <text x="100" y="153" text-anchor="middle" font-weight="600">Word reports</text>
    <text x="100" y="173" text-anchor="middle" fill="{ui.GREY}">one shared store</text>

    <line x1="180" y1="145" x2="300" y2="70" stroke="{g}" stroke-width="1.5" marker-end="url(#arr)"/>
    <line x1="180" y1="175" x2="300" y2="250" stroke="{t}" stroke-width="1.5" marker-end="url(#arr)"/>

    <rect x="300" y="30" width="250" height="80" rx="10" fill="#fff" stroke="{g}" stroke-width="1.5"/>
    <text x="425" y="58" text-anchor="middle" font-weight="600" fill="{g}">Default conversion</text>
    <text x="425" y="78" text-anchor="middle" fill="{ui.GREY}">{word_route}</text>
    <text x="425" y="96" text-anchor="middle" fill="{ui.GREY}">{word_sub}</text>

    <rect x="300" y="210" width="250" height="80" rx="10" fill="#fff" stroke="{t}" stroke-width="2"/>
    <text x="425" y="238" text-anchor="middle" font-weight="600" fill="{t}">Markdown+</text>
    <text x="425" y="258" text-anchor="middle" fill="{ui.GREY}">charts read as numbers, matrices as states,</text>
    <text x="425" y="276" text-anchor="middle" fill="{ui.GREY}">pictures described once, facts file</text>

    <line x1="550" y1="70" x2="660" y2="70" stroke="{g}" stroke-width="1.5" marker-end="url(#arr)"/>
    <line x1="550" y1="250" x2="660" y2="250" stroke="{t}" stroke-width="1.5" marker-end="url(#arr)"/>

    <rect x="660" y="30" width="180" height="80" rx="10" fill="#fff" stroke="{g}" stroke-width="1.5"/>
    <text x="750" y="62" text-anchor="middle" font-weight="600">Search</text>
    <text x="750" y="82" text-anchor="middle" fill="{ui.GREY}">text chunks</text>
    <rect x="660" y="210" width="180" height="80" rx="10" fill="#fff" stroke="{t}" stroke-width="2"/>
    <text x="750" y="242" text-anchor="middle" font-weight="600">Search</text>
    <text x="750" y="262" text-anchor="middle" fill="{ui.GREY}">chunks · facts · pictures</text>

    <line x1="840" y1="70" x2="930" y2="140" stroke="{g}" stroke-width="1.5" marker-end="url(#arr)"/>
    <line x1="840" y1="250" x2="930" y2="180" stroke="{t}" stroke-width="1.5" marker-end="url(#arr)"/>

    <rect x="930" y="120" width="150" height="80" rx="10" fill="{n}"/>
    <text x="1005" y="153" text-anchor="middle" fill="#fff" font-weight="600">Same model</text>
    <text x="1005" y="173" text-anchor="middle" fill="#fff" opacity="0.85">{settings.answer_model}</text>
    <line x1="1080" y1="160" x2="1120" y2="160" stroke="{n}" stroke-width="1.5" marker-end="url(#arr)"/>
    <text x="1126" y="155" font-weight="600">Cited</text>
    <text x="1126" y="173" font-weight="600">answer</text>
  </g>
</svg>
<figcaption class="muted">One set of Word files, two ways in, one model answering. Only the Markdown+ route carries
exact chart values, matrix states and the pictures themselves; the default route is what you get without code.</figcaption>
</figure>"""


st.markdown("## How it works")
st.html(pipeline_figure())
with st.expander("The two modes"):
    st.markdown(ui.mode_help())
    st.caption("Every tile on the Ask page is one arm in one mode. The agentic mode plans and uses more tokens; single-pass is one "
               "retrieval and one answer. Both are measured the same way.")

# ----------------------------------------------------------------------------- the corpus

st.markdown("## The reports")
man = ui.manifest()
docs = store.doc_ids()
if not docs:
    st.info("No Word files in the store yet. `python -m research_qa upload <files>` then `ingest`.")
else:
    ingested = [man[d] for d in docs if d in man]
    ui.kpi_row([("Reports", str(len(docs)), f"{len(ingested)} converted"),
                ("Figures", f"{sum(m.get('figures', 0) for m in ingested):,}", "charts, pictures, matrices"),
                ("Charts read as numbers", f"{sum(m.get('charts_native', 0) + m.get('charts_digitised_ok', 0) for m in ingested):,}",
                 "exact values, no vision call"),
                ("Pictures described", f"{sum(m.get('pictures_described', 0) for m in ingested):,}",
                 f"of {sum(m.get('pictures', 0) for m in ingested)} pictures"),
                ("Passages", f"{sum(m.get('chunks', 0) for m in ingested):,}", "what the model searches")])
    st.markdown("")
    cols = st.columns(min(len(docs), 4))
    for i, d in enumerate(docs):
        m = man.get(d, {})
        with cols[i % 4]:
            with st.container(border=True):
                st.markdown(f"**{ui.report_name(d, m)}**")
                st.caption(" · ".join(x for x in (m.get("title") if m.get("title") != ui.report_name(d, m) else "", m.get("date")) if x) or d)
                if m:
                    st.markdown(f'<div class="muted">{m.get("figures", 0)} figures · '
                                f'{m.get("charts_native", 0) + m.get("charts_digitised_ok", 0)} charts as numbers · '
                                f'{m.get("matrices", 0)} matrices · {m.get("pictures_described", 0)} pictures described'
                                f'</div>', unsafe_allow_html=True)
                    if m.get("analysts"):
                        st.caption(", ".join(ui.person_name(a) for a in m["analysts"]))
                else:
                    st.caption("not converted yet")
    st.page_link("pages/3_Reports.py", label="Open a report to see what the conversion found", icon=":material/description:")

st.markdown("")
st.page_link("pages/1_Ask.py", label="Ask a question and watch the four routes answer side by side", icon=":material/forum:")
