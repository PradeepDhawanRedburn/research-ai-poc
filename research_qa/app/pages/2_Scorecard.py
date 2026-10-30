"""Scorecard: evaluation runs from the store, by question type, with per-question drill-down."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import streamlit as st  # noqa: E402

from research_qa.app import ui  # noqa: E402
from research_qa.evaluate.report import headline_rows  # noqa: E402
from research_qa.evaluate.run import list_runs, load_run  # noqa: E402

ui.setup("Scorecard")
st.title("Scorecard")
ui.store_note()

runs = list_runs(ui.store())
if not runs:
    st.info("No evaluation run yet. Run `python -m research_qa eval`.")
    st.stop()
run = st.selectbox("Run", runs, help="Newest first. A run is a folder eval/<run>/ in the store.")
summary, rows, config = load_run(run, ui.store())
if not rows:
    st.warning("This run has no rows yet.")
    st.stop()

st.caption(f"{summary.get('questions')} questions · arms {', '.join(config.get('arms', []))} · modes "
           f"{', '.join(config.get('modes', []))} · model `{config.get('answer_model')}` · judge `{config.get('judge')}`"
           f" · passages per retrieval {config.get('max_docs')} · started {config.get('started', '')[:19]}")

combos = summary["combos"]
labels = {c: summary["per_combo"][c]["label"] for c in combos}

st.subheader("Headline")
st.dataframe(headline_rows(summary), hide_index=True, width="stretch")
st.caption("Accuracy: correct = 1, partial = 0.5, one judge for every arm and mode. Evidence found: the golden "
           "evidence strings were all in the retrieved passages. Cost from prices.yaml; 'not priced' when a "
           "deployment has no price.")

st.subheader("By question type")
measure = st.radio("Measure", ["accuracy", "evidence found", "tokens per question", "seconds per question"],
                   horizontal=True)
key = {"accuracy": lambda s: (s["accuracy"] or 0) * 100, "evidence found": lambda s: (s["evidence_found"] or 0) * 100,
       "tokens per question": lambda s: s["tokens"]["total_tokens"]["mean"],
       "seconds per question": lambda s: s["time"]["total_s"]["mean"]}[measure]
unit = {"accuracy": "%", "evidence found": "%", "tokens per question": "tokens", "seconds per question": "s"}[measure]
chart = {labels[c]: {t: round(key(summary["per_type"][c][t]), 1) for t in summary["types"]} for c in combos}
st.bar_chart(chart, horizontal=False, stack=False, y_label=unit, width="stretch")
table = []
for t in summary["types"]:
    row = {"type": t, "questions": max(summary["per_type"][c][t]["n"] for c in combos)}
    for c in combos:
        s = summary["per_type"][c][t]
        row[f"{labels[c]} accuracy"] = f"{s['accuracy']:.0%}" if s["accuracy"] is not None else "-"
        row[f"{labels[c]} evidence"] = f"{s['evidence_found']:.0%}" if s["evidence_found"] is not None else "-"
        row[f"{labels[c]} tokens"] = round(s["tokens"]["total_tokens"]["mean"])
    table.append(row)
st.dataframe(table, hide_index=True, width="stretch")

st.subheader("Time and tokens")
tt = []
for c in combos:
    s = summary["per_combo"][c]
    for k, name in (("retrieval_s", "retrieval s"), ("generation_s", "generation s"), ("tools_s", "tools s"),
                    ("total_s", "total s")):
        st_ = s["time"][k]
        tt.append({"arm · mode": labels[c], "metric": name, "mean": round(st_["mean"], 2),
                   "median": round(st_["median"], 2), "p95": round(st_["p95"], 2)})
    for k, name in (("input_tokens", "input tokens"), ("output_tokens", "output tokens"),
                    ("reasoning_tokens", "reasoning tokens"), ("vision_tokens", "vision tokens"),
                    ("total_tokens", "total tokens")):
        st_ = s["tokens"][k]
        tt.append({"arm · mode": labels[c], "metric": name, "mean": round(st_["mean"]), "median": round(st_["median"]),
                   "p95": round(st_["p95"])})
st.dataframe(tt, hide_index=True, width="stretch")

if summary.get("disagreements"):
    st.subheader(f"Where the arms disagree ({len(summary['disagreements'])})")
    st.dataframe([{"id": d["id"], "type": d["type"], **{labels[c]: d.get(c, "") for c in combos}}
                  for d in summary["disagreements"]], hide_index=True, width="stretch")

st.subheader("Per question")
by_q: dict[str, dict] = {}
for r in rows:
    by_q.setdefault(r["id"], {"type": r["type"], "doc": r["doc"], "rows": {}})["rows"][r["combo"]] = r
f1, f2 = st.columns(2)
types = f1.multiselect("Question types", summary["types"], default=summary["types"])
only_diff = f2.checkbox("Only where the arms disagree", value=False)
diff_ids = {d["id"] for d in summary.get("disagreements", [])}
grid = []
for qid, q in by_q.items():
    if q["type"] not in types or (only_diff and qid not in diff_ids):
        continue
    row = {"id": qid, "type": q["type"], "doc": q["doc"]}
    for c in combos:
        r = q["rows"].get(c)
        row[labels[c]] = f"{r['verdict']} · {r['evidence_hits']} · {r['total_tokens']:,} tok" if r else ""
    grid.append(row)
st.dataframe(grid, hide_index=True, width="stretch")

qid = st.selectbox("Open a question", [g["id"] for g in grid] or ["-"])
if qid in by_q:
    q = by_q[qid]
    first = next(iter(q["rows"].values()))
    st.markdown(f"**{first.get('id')}** · {q['type']} · {q['doc']}")
    st.markdown(f"Expected: {first.get('expected', '')}")
    cols = st.columns(len(combos))
    for col, c in zip(cols, combos):
        r = q["rows"].get(c)
        with col:
            st.markdown(f"**{labels[c]}**")
            if not r:
                st.caption("not run")
                continue
            st.markdown(r["answer"] or "*(empty)*")
            st.caption(f"{r['verdict']} · judge: {r['reason']}")
            st.caption(f"evidence {r['evidence_hits']} · {r['total_s']:.1f} s · {r['total_tokens']:,} tokens "
                       f"({r.get('vision_tokens', 0):,} vision) · {ui.fmt_cost(r.get('cost'))} · "
                       f"tools {', '.join(r.get('tools') or []) or 'none'}")
            if r.get("errors"):
                st.error("; ".join(r["errors"]))
