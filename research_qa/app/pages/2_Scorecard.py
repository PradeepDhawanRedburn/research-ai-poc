"""Scorecard: an evaluation run from the store, headline per arm × mode, by question type, run against run, per question."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from research_qa.app import ui  # noqa: E402
from research_qa.evaluate.report import headline_rows, paired_comparison  # noqa: E402
from research_qa.evaluate.run import load_run  # noqa: E402

ui.setup("Scorecard")
store = ui.require_store()

TYPE_LABEL = {"text_fact": "Text", "table_lookup": "Table", "chart_native": "Chart (native)",
              "chart_picture": "Chart (pasted picture)", "symbol_matrix": "Tick/cross matrix", "image": "Picture",
              "relationship": "Figure and source", "cross_document": "Two editions", "unanswerable": "Not in the reports",
              "temporal_change": "Change over time", "analyst_view": "Analyst view", "metadata_filter": "Metadata filter",
              "multi_doc_aggregate": "Multi-report aggregate", "wrong_edition_trap": "Edition selection"}
MEASURES = {"Accuracy": ("%", lambda s: (s["accuracy"] or 0) * 100, lambda s: s.get("accuracy_ci")),
            "Evidence found": ("%", lambda s: (s["evidence_found"] or 0) * 100, lambda s: s.get("evidence_ci")),
            "Tokens per question": ("tokens", lambda s: s["tokens"]["total_tokens"]["mean"], lambda s: None),
            "Seconds per question": ("s", lambda s: s["time"]["total_s"]["mean"], lambda s: None)}

catalog = ui.run_catalog()
with st.sidebar:
    show_all = st.toggle("Show older and partial runs", value=False,
                         help="By default: complete runs that record their engine, the newest Azure and the newest "
                              "LangChain one of each size (full golden set, smoke subset).")
    pool = catalog if show_all else (ui.current_runs(catalog) or catalog)
    pool = sorted(pool, key=lambda r: (r["kind"] != "full", r["backend"] or "", r["name"]))
    by_name = {r["name"]: r for r in pool}
    runs = list(by_name)
    if not runs:
        st.caption("No evaluation run in the store.")
    else:
        run = st.selectbox("Run", runs, format_func=lambda n: ui.run_title(by_name[n]),
                           help="A run is a folder eval/<run>/ in the store.")
        other = st.selectbox("Compare with", ["(none)"] + [r for r in runs if r != run],
                             format_func=lambda n: n if n == "(none)" else ui.run_title(by_name[n]),
                             help="A second run, for instance the other engine, shown next to this one.")
ui.sidebar_footer()

st.title("Scorecard")
if not runs:
    st.info("No evaluation run yet. `python -m research_qa eval` scores the verified questions.")
    st.stop()
summary, rows, config = load_run(run, store)
if not rows:
    st.warning("This run has no rows yet.")
    st.stop()
combos = summary["combos"]
labels = {c: ui.combo_title(*c.split("/")) for c in combos}
colour = {labels[c]: ui.ARM_COLOUR[c.split("/")[0]] for c in combos}
st.markdown(f'<p class="lede">{summary.get("questions")} verified questions, every arm and mode answering the same '
            f'ones, one judge for all. Accuracy counts a correct answer as 1 and a partial one as 0.5; evidence '
            f'found means the golden evidence was among the passages the system read.</p>', unsafe_allow_html=True)
st.caption(f"Run {run} · model {config.get('answer_model')} · judge {config.get('judge')} · "
           f"passages per retrieval {config.get('max_docs')} · started {str(config.get('started', ''))[:19]}"
           + (" · retrieval: " + ", ".join(f"{k} {v}" for k, v in config["retrieval"].items() if v not in (None, ""))
              if config.get("retrieval") else ""))

# ----------------------------------------------------------------------------- headline

st.markdown("## Headline")
cols = st.columns(len(combos))
best_acc = max((summary["per_combo"][c]["accuracy"] or 0) for c in combos)
for col, c in zip(cols, combos):
    s = summary["per_combo"][c]
    arm, mode = c.split("/")
    with col:
        with st.container(border=True):
            ui.tile_header(arm, mode, "best" if s["accuracy"] == best_acc and s["n"] else "")
            acc_ci = s.get("accuracy_ci")
            ui.kpi("Accuracy", ui.pct(s["accuracy"]), f"95% CI {acc_ci[0]:.0%}–{acc_ci[1]:.0%} · "
                   f"{s['correct']} correct · {s['partial']} partial · {s['incorrect']} incorrect"
                   if acc_ci else f"{s['correct']} correct · {s['partial']} partial · {s['incorrect']} incorrect",
                   accent=s["accuracy"] == best_acc and s["n"] > 0)
            ui.metric_line([("evidence found", ui.pct(s["evidence_found"])),
                            ("per question", f"{s['time']['total_s']['mean']:.1f} s"),
                            ("tokens", f"{s['tokens']['total_tokens']['mean']:,.0f}")])
            evidence_ci = s.get("evidence_ci")
            st.caption((f"Evidence 95% CI {evidence_ci[0]:.0%}–{evidence_ci[1]:.0%} · " if evidence_ci else "")
                       + f"p95 {s['time']['total_s']['p95']:.1f} s · {s['tokens']['vision_tokens']['sum']:,.0f} vision tokens"
                       f" in total · {s['tool_calls']} tool calls · {s['failed']} failed")
with st.expander("Headline table"):
    st.dataframe(headline_rows(summary), hide_index=True, width="stretch")

# ----------------------------------------------------------------------------- run against run

if other != "(none)":
    s2, r2, c2 = load_run(other, store)
    st.markdown(f"## This run against {other}")
    recs = []
    for name, sm in ((run, summary), (other, s2)):
        for c in sm.get("combos", []):
            s = sm["per_combo"][c]
            if not s["n"]:
                continue
            recs.append({"run": name, "arm · mode": ui.combo_title(*c.split("/")), "questions": s["n"],
                         "accuracy": ui.pct(s["accuracy"]),
                         "accuracy 95% CI": "-" if not s.get("accuracy_ci") else f"{s['accuracy_ci'][0]:.0%}–{s['accuracy_ci'][1]:.0%}",
                         "evidence found": ui.pct(s["evidence_found"]),
                         "evidence 95% CI": "-" if not s.get("evidence_ci") else f"{s['evidence_ci'][0]:.0%}–{s['evidence_ci'][1]:.0%}",
                         "seconds": round(s["time"]["total_s"]["mean"], 1),
                         "tokens": round(s["tokens"]["total_tokens"]["mean"])})
    st.dataframe(recs, hide_index=True, width="stretch")
    st.caption(f"{run}: model {config.get('answer_model')}, {len(rows)} rows · {other}: model {c2.get('answer_model')}")
    paired_rows = []
    for label, run_rows in ((run, rows), (other, r2)):
        for row in run_rows:
            paired_rows.append({**row, "combo": f"{label}::{row.get('combo') or row['arm'] + '/' + row['mode']}"})
    comparisons = []
    for combo in sorted(set(summary.get("combos", [])) & set(s2.get("combos", []))):
        pair = paired_comparison(paired_rows, f"{run}::{combo}", f"{other}::{combo}")
        comparisons.append({"arm · mode": ui.combo_title(*combo.split("/")), "paired questions": pair["n"],
                            "strict wins (this/other)": f"{pair['strict']['left_wins']}/{pair['strict']['right_wins']}",
                            "strict p": round(pair["strict"]["p"], 4), "strict result": pair["strict"]["significance"],
                            "lenient wins (this/other)": f"{pair['lenient']['left_wins']}/{pair['lenient']['right_wins']}",
                            "lenient p": round(pair["lenient"]["p"], 4), "lenient result": pair["lenient"]["significance"]})
    st.markdown("### Paired McNemar tests")
    st.dataframe(comparisons, hide_index=True, width="stretch")
    st.caption("Exact binomial test on discordant pairs. Strict treats only correct as success; lenient treats partial as success. p ≥ 0.05 is not significant.")

# ----------------------------------------------------------------------------- by type

st.markdown("## By question type")
measure = st.segmented_control("Measure", list(MEASURES), default="Accuracy", label_visibility="collapsed")
measure = measure or "Accuracy"
unit, fn, ci_fn = MEASURES[measure]
recs = []
for c in combos:
    for t in summary["types"]:
        s = summary["per_type"][c][t]
        if s["n"]:
            interval = ci_fn(s)
            recs.append({"type": TYPE_LABEL.get(t, t), "arm · mode": labels[c], "value": round(fn(s), 1), "n": s["n"],
                         "95% CI": "-" if interval is None else f"{interval[0]:.0%}–{interval[1]:.0%}"})
df = pd.DataFrame(recs)
type_order = [TYPE_LABEL.get(t, t) for t in summary["types"]]
chart = (alt.Chart(df).mark_bar(cornerRadiusEnd=2).encode(
    x=alt.X("arm · mode:N", title=None, axis=None, sort=list(labels.values())),
    y=alt.Y("value:Q", title=unit, scale=alt.Scale(domain=[0, 100]) if unit == "%" else alt.Undefined),
    color=alt.Color("arm · mode:N", sort=list(labels.values()), title=None,
                    scale=alt.Scale(domain=list(colour), range=list(colour.values())),
                    legend=alt.Legend(orient="top", direction="horizontal", titleFontSize=12, labelFontSize=12)),
    opacity=alt.condition(alt.FieldOneOfPredicate("arm · mode", [l for l in labels.values() if l.endswith("Single-pass")]),
                          alt.value(0.55), alt.value(1.0)),
    column=alt.Column("type:N", sort=type_order, title=None,
                      header=alt.Header(labelFontSize=12, labelOrient="bottom", labelAngle=0, labelLimit=120)),
    tooltip=["type:N", "arm · mode:N", alt.Tooltip("value:Q", title=measure), alt.Tooltip("95% CI:N"),
             alt.Tooltip("n:Q", title="questions")])
    .properties(width=max(70, 18 * len(combos) + 20), height=220)
    .configure_view(strokeWidth=0).configure_facet(spacing=8))
st.altair_chart(chart, width="stretch")
st.caption("Agentic bars are solid, single-pass bars lighter. Hover for the number of questions behind a bar.")
with st.expander("By type, as a table"):
    table = []
    for t in summary["types"]:
        row = {"type": TYPE_LABEL.get(t, t), "questions": max(summary["per_type"][c][t]["n"] for c in combos)}
        for c in combos:
            s = summary["per_type"][c][t]
            row[f"{labels[c]} · accuracy (95% CI)"] = (f"{ui.pct(s['accuracy'])} ({s['accuracy_ci'][0]:.0%}–"
                                                               f"{s['accuracy_ci'][1]:.0%})" if s.get("accuracy_ci") else "-")
            row[f"{labels[c]} · evidence found (95% CI)"] = (f"{ui.pct(s['evidence_found'])} ({s['evidence_ci'][0]:.0%}–"
                                                                    f"{s['evidence_ci'][1]:.0%})" if s.get("evidence_ci") else "-")
            row[f"{labels[c]} · tokens"] = round(s["tokens"]["total_tokens"]["mean"])
        table.append(row)
    st.dataframe(table, hide_index=True, width="stretch")

with st.expander("Accuracy by source of truth"):
    source_rows = []
    sources = sorted({source for combo in summary.get("per_source_of_truth", {}).values() for source in combo})
    for source in sources:
        row = {"source_of_truth": source}
        for combo in combos:
            score = summary["per_source_of_truth"].get(combo, {}).get(source)
            row[labels[combo]] = ("-" if not score else
                                  f"{ui.pct(score['accuracy'])} ({score['accuracy_ci'][0]:.0%}–"
                                  f"{score['accuracy_ci'][1]:.0%}), n={score['n']}")
        source_rows.append(row)
    st.dataframe(source_rows, hide_index=True, width="stretch")

# ----------------------------------------------------------------------------- time and tokens

with st.expander("Time and tokens in detail"):
    tt = []
    for c in combos:
        s = summary["per_combo"][c]
        for k, name in (("retrieval_s", "retrieval s"), ("generation_s", "generation s"), ("tools_s", "tools s"),
                        ("total_s", "total s")):
            x = s["time"][k]
            tt.append({"arm · mode": labels[c], "metric": name, "mean": round(x["mean"], 2),
                       "median": round(x["median"], 2), "p95": round(x["p95"], 2)})
        for k, name in (("input_tokens", "input tokens"), ("output_tokens", "output tokens"),
                        ("reasoning_tokens", "reasoning tokens"), ("vision_tokens", "vision tokens"),
                        ("total_tokens", "total tokens")):
            x = s["tokens"][k]
            tt.append({"arm · mode": labels[c], "metric": name, "mean": round(x["mean"]), "median": round(x["median"]),
                       "p95": round(x["p95"])})
    st.dataframe(tt, hide_index=True, width="stretch")

# ----------------------------------------------------------------------------- per question

st.markdown("## Question by question")
by_q: dict[str, dict] = {}
for r in rows:
    by_q.setdefault(r["id"], {"type": r["type"], "doc": r["doc"], "rows": {}})["rows"][r["combo"]] = r
diff_ids = {d["id"] for d in summary.get("disagreements", [])}
f1, f2 = st.columns([3, 1])
types = f1.multiselect("Question types", summary["types"], default=summary["types"],
                       format_func=lambda t: TYPE_LABEL.get(t, t), label_visibility="collapsed")
only_diff = f2.toggle(f"Only where they disagree ({len(diff_ids)})", value=False)
grid = []
for qid, q in by_q.items():
    if q["type"] not in types or (only_diff and qid not in diff_ids):
        continue
    first = next(iter(q["rows"].values()))
    row = {"id": qid, "type": TYPE_LABEL.get(q["type"], q["type"]), "question": first.get("expected") and
           next((x for x in ui.golden_questions() if x["id"] == qid), {}).get("question", "")}
    for c in combos:
        r = q["rows"].get(c)
        row[labels[c]] = r["verdict"] if r else ""
    grid.append(row)
st.dataframe(grid, hide_index=True, width="stretch", height=min(38 + 35 * len(grid), 420),
             column_config={"question": st.column_config.TextColumn(width="large")})

qid = st.selectbox("Open a question", [g["id"] for g in grid] or ["-"],
                   format_func=lambda i: next((f"{g['id']} · {g['question'][:90]}" for g in grid if g["id"] == i), i))
if qid in by_q:
    q = by_q[qid]
    first = next(iter(q["rows"].values()))
    gq = next((x for x in ui.golden_questions() if x["id"] == qid), {})
    with st.container(border=True):
        st.markdown(f"**{gq.get('question', qid)}**")
        st.markdown(f'<div class="muted">Expected: <b>{first.get("expected", "")}</b> · '
                    f'{TYPE_LABEL.get(q["type"], q["type"])} · {q["doc"]}</div>', unsafe_allow_html=True)
    cols = st.columns(len(combos))
    for col, c in zip(cols, combos):
        r = q["rows"].get(c)
        with col:
            with st.container(border=True):
                ui.tile_header(*c.split("/"))
                if not r:
                    st.caption("not run")
                    continue
                st.markdown(ui.verdict_badge(r["verdict"]), unsafe_allow_html=True)
                st.markdown(r["answer"] or "*No answer was produced.*")
                st.caption(f"Judge: {r['reason']}")
                st.markdown(f'<div class="strip"><span><b>{r["total_s"]:.1f} s</b></span>'
                            f'<span><b>{r["total_tokens"]:,}</b> tokens</span>'
                            f'<span>evidence <b>{r["evidence_hits"]}</b></span>'
                            + (f'<span>tools: {", ".join(r["tools"])}</span>' if r.get("tools") else "") + "</div>",
                            unsafe_allow_html=True)
                if r.get("errors"):
                    st.error("; ".join(r["errors"]))
