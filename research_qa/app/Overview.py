"""Overview: the pipeline in one figure, the latest scorecard in five numbers per arm, what is in the store.

Start with:  streamlit run research_qa/app/Overview.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root, so `research_qa` imports under streamlit

import streamlit as st  # noqa: E402

from research_qa.app import ui  # noqa: E402
from research_qa.config import ARMS, combo_key, settings  # noqa: E402
from research_qa.evaluate.report import headline_rows  # noqa: E402
from research_qa.evaluate.run import list_runs, load_run  # noqa: E402

ui.setup("Overview")
st.title("Research reports, asked in plain language")
st.markdown("Analysts' Word reports become something a model can search and cite: text, tables, charts, pictures "
            "and tick/cross matrices. Two routes are compared on the same questions, the same model and the same "
            "knowledge-base retrieval: **Azure's default pipeline over the Word files** and **Markdown+**, the "
            "conversion with the research plugin.")
ui.store_note()


def pipeline_figure(costs: dict[str, str]) -> str:
    """Word in Blob forks into two routes that meet again at one agent. Labels carry the measured costs."""
    c = {"navy": ui.NAVY, "teal": ui.TEAL, "grey": ui.GREY}
    w_cost, m_cost = costs.get("word", ""), costs.get("markdown-plus", "")
    return f"""
<figure style="margin:0">
<svg viewBox="0 0 1180 330" role="img" style="max-width:100%;height:auto;font-family:inherit"
     aria-label="Word reports in Blob storage flow through two routes, Azure default and Markdown+, into two knowledge bases answered by the same agent.">
  <defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
    <path d="M0,0 L10,5 L0,10 z" fill="{c['grey']}"/></marker></defs>
  <g font-size="13" fill="#1B2430">
    <rect x="20" y="125" width="150" height="80" rx="6" fill="{ui.LIGHT}" stroke="{c['grey']}"/>
    <text x="95" y="158" text-anchor="middle" font-weight="600">Word reports</text>
    <text x="95" y="178" text-anchor="middle" fill="{c['grey']}">raw/ in Blob</text>

    <line x1="170" y1="150" x2="300" y2="70" stroke="{c['grey']}" stroke-width="1.5" marker-end="url(#arr)"/>
    <line x1="170" y1="180" x2="300" y2="260" stroke="{c['grey']}" stroke-width="1.5" marker-end="url(#arr)"/>

    <rect x="300" y="30" width="230" height="80" rx="6" fill="#fff" stroke="{c['grey']}"/>
    <text x="415" y="58" text-anchor="middle" font-weight="600">Azure default pipeline</text>
    <text x="415" y="78" text-anchor="middle" fill="{c['grey']}">cracks, chunks, embeds, describes</text>
    <text x="415" y="96" text-anchor="middle" fill="{c['grey']}">every picture with a vision model</text>

    <rect x="300" y="220" width="230" height="80" rx="6" fill="#fff" stroke="{c['teal']}" stroke-width="2"/>
    <text x="415" y="248" text-anchor="middle" font-weight="600" fill="{c['teal']}">MarkItDown + research plugin</text>
    <text x="415" y="268" text-anchor="middle" fill="{c['grey']}">charts read as numbers, matrices as states,</text>
    <text x="415" y="286" text-anchor="middle" fill="{c['grey']}">pictures described once · facts file</text>

    <line x1="530" y1="70" x2="640" y2="70" stroke="{c['grey']}" stroke-width="1.5" marker-end="url(#arr)"/>
    <text x="585" y="60" text-anchor="middle" fill="{c['grey']}" font-size="11">service-side chunks</text>
    <line x1="530" y1="260" x2="640" y2="260" stroke="{c['teal']}" stroke-width="1.5" marker-end="url(#arr)"/>
    <text x="585" y="250" text-anchor="middle" fill="{c['teal']}" font-size="11">markdown · chunks · facts · assets</text>

    <rect x="640" y="30" width="170" height="80" rx="6" fill="#fff" stroke="{c['grey']}"/>
    <text x="725" y="62" text-anchor="middle" font-weight="600">Knowledge base</text>
    <text x="725" y="82" text-anchor="middle" fill="{c['grey']}">Word</text>
    <rect x="640" y="220" width="170" height="80" rx="6" fill="#fff" stroke="{c['teal']}" stroke-width="2"/>
    <text x="725" y="252" text-anchor="middle" font-weight="600" fill="{c['teal']}">Knowledge base</text>
    <text x="725" y="272" text-anchor="middle" fill="{c['grey']}">Markdown+ · facts · pictures</text>

    <line x1="810" y1="70" x2="900" y2="145" stroke="{c['grey']}" stroke-width="1.5" marker-end="url(#arr)"/>
    <line x1="810" y1="260" x2="900" y2="185" stroke="{c['teal']}" stroke-width="1.5" marker-end="url(#arr)"/>

    <rect x="900" y="125" width="150" height="80" rx="6" fill="{c['navy']}"/>
    <text x="975" y="158" text-anchor="middle" fill="#fff" font-weight="600">Same agent</text>
    <text x="975" y="178" text-anchor="middle" fill="#fff" opacity="0.85">{settings.answer_model}</text>
    <line x1="1050" y1="165" x2="1120" y2="165" stroke="{c['navy']}" stroke-width="1.5" marker-end="url(#arr)"/>
    <text x="1125" y="160" fill="#1B2430" font-weight="600">Cited</text>
    <text x="1125" y="178" fill="#1B2430" font-weight="600">answer</text>

    <text x="415" y="130" text-anchor="middle" fill="{c['grey']}" font-size="12">{w_cost}</text>
    <text x="415" y="318" text-anchor="middle" fill="{c['teal']}" font-size="12">{m_cost}</text>
  </g>
</svg>
<figcaption class="muted">One set of Word files, two ways in, one agent answering. The plugin route adds exact chart
values, matrix states and a facts lookup; the default route is what Azure gives without code.</figcaption>
</figure>"""


# ----------------------------------------------------------------------------- latest run

runs = []
try:
    runs = list_runs(ui.store())
except Exception as e:  # noqa: BLE001
    st.warning(f"Could not list evaluation runs: {type(e).__name__}: {str(e)[:160]}")

costs: dict[str, str] = {}
summary: dict = {}
if runs:
    summary, rows, config = load_run(runs[0], ui.store())
    mode = (config.get("modes") or ["agent"])[0]
    for arm in ARMS:
        s = summary.get("per_combo", {}).get(combo_key(arm, mode))
        if s and s["n"]:
            costs[arm] = (f"{s['tokens']['total_tokens']['mean']:,.0f} tokens · "
                          f"{ui.fmt_cost(s['cost_per_question'])} per question")

st.markdown(pipeline_figure(costs), unsafe_allow_html=True)

st.subheader("Latest scorecard")
if not runs:
    st.info("No evaluation run in the store yet. Run `python -m research_qa eval` to score the golden questions.")
else:
    st.caption(f"Run {runs[0]} · {summary.get('questions', 0)} questions · judge `{config.get('judge', '')}` · "
               f"model `{config.get('answer_model', '')}`")
    combos = summary.get("combos", [])
    cols = st.columns(len(combos) or 1)
    for col, c in zip(cols, combos):
        s = summary["per_combo"][c]
        with col:
            st.markdown(f"**{s['label']}**")
            m1, m2 = st.columns(2)
            m1.metric("Accuracy", f"{s['accuracy']:.0%}" if s["accuracy"] is not None else "-",
                      help="correct = 1, partial = 0.5, judged against the expected answer")
            m2.metric("Evidence found", f"{s['evidence_found']:.0%}" if s["evidence_found"] is not None else "-",
                      help="share of questions whose golden evidence was in the retrieved passages")
            m3, m4, m5 = st.columns(3)
            m3.metric("Seconds", f"{s['time']['total_s']['mean']:.1f}", help="mean per question")
            m4.metric("Tokens", f"{s['tokens']['total_tokens']['mean']:,.0f}", help="mean per question, answer model plus vision")
            m5.metric(f"Cost ({summary.get('currency')})", ui.fmt_cost(s["cost_per_question"]),
                      help="mean per question from prices.yaml")
    with st.expander("Headline table"):
        st.dataframe(headline_rows(summary), hide_index=True, width="stretch")
    st.page_link("pages/2_Scorecard.py", label="Open the scorecard for the breakdown by question type")

# ----------------------------------------------------------------------------- the store

st.subheader("What is in the store")
man = ui.manifest()
docs = ui.store().doc_ids()
if not docs:
    st.info("No Word files in raw/. Upload with `python -m research_qa upload <files>` and run `ingest`.")
else:
    ingested = [man[d] for d in docs if d in man]
    t1, t2, t3, t4, t5 = st.columns(5)
    t1.metric("Reports", len(docs), help=f"{len(ingested)} converted to Markdown+")
    t2.metric("Chunks", f"{sum(m.get('chunks', 0) for m in ingested):,}", help="Markdown+ retrieval units")
    t3.metric("Charts read as numbers", sum(m.get("charts_native", 0) + m.get("charts_digitised_ok", 0) for m in ingested),
              help="native Word charts plus pasted Excel charts digitised from the drawing")
    t4.metric("Pictures described", sum(m.get("pictures_described", 0) for m in ingested),
              help=f"of {sum(m.get('pictures', 0) for m in ingested)} pictures; the rest carry exact values instead")
    t5.metric("Fact figures", sum(m.get("facts_figures", 0) for m in ingested), help="figures with a facts lookup")
    st.dataframe([{"report": d, "title": man.get(d, {}).get("title", ""), "date": man.get(d, {}).get("date", ""),
                   "analysts": ", ".join(man.get(d, {}).get("analysts", [])),
                   "chunks": man.get(d, {}).get("chunks"), "figures": man.get(d, {}).get("figures"),
                   "converted": "yes" if d in man else "no"} for d in docs], hide_index=True, width="stretch")
    st.page_link("pages/3_Reports.py", label="Open a report to see what the conversion found")

st.page_link("pages/1_Ask.py", label="Ask a question and compare the routes side by side")
