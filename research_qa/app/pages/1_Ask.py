"""Ask: one question, every selected arm × mode at once, each tile filled as its answer lands."""
from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import streamlit as st  # noqa: E402

from research_qa.app import ui  # noqa: E402
from research_qa.config import ARMS, DEFAULT_MODES, MODES, combo_key, combo_label, settings  # noqa: E402
from research_qa.schema import Answer  # noqa: E402

ui.setup("Ask")
st.title("Ask the reports")
st.session_state.setdefault("history", [])
st.session_state.setdefault("chats", {})

# ----------------------------------------------------------------------------- sidebar

with st.sidebar:
    st.header("Compare")
    keys = []
    for arm in ARMS:
        st.markdown(f"**{ARMS[arm].label}**")
        for mode in MODES:
            if st.checkbox(mode, value=mode in DEFAULT_MODES, key=f"sel-{arm}-{mode}", help=MODES[mode]):
                keys.append(combo_key(arm, mode))
    max_docs = st.slider("Passages per question", 3, 30, settings.max_docs,
                         help="Passages the knowledge base returns per retrieval, the same for both arms. Stored on "
                              "the knowledge bases in Azure; fewer means fewer tokens, more means more recall.")
    use_tools = st.checkbox("Let the model use facts and pictures", value=True,
                            help="Markdown+ only: exact chart values from the facts file and a vision model for "
                                 "pictures. Off: both arms answer from the retrieved text alone.")
    show_all_text = st.checkbox("Show the text of every retrieved passage", value=False)
    if st.button("Clear conversation"):
        st.session_state.history, st.session_state.chats = [], {}
        st.rerun()

    totals = ui.session_totals(st.session_state.history, keys)
    if totals:
        st.header("This session")
        st.dataframe(totals, hide_index=True, width="stretch")
        buf = io.StringIO()
        rows = [{"question": t["question"], **row} for t in st.session_state.history
                for row in ui.comparison_rows(t["results"], t["errors"], t["keys"])]
        if rows:
            fields = list(dict.fromkeys(k for r in rows for k in r))
            w = csv.DictWriter(buf, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
            st.download_button("Download every answer's numbers (CSV)", buf.getvalue(), "comparison.csv", "text/csv")
    with st.expander("How the numbers compare"):
        st.markdown(
            "- **Time**: wall clock for the whole question. Retrieval = knowledge-base calls; an agent usually writes "
            "its answer in the same round, so generation can be 0. Tools = facts lookups and picture inspections.\n"
            "- **Tokens**: everything the question cost: the answer model's input and output, plus vision calls. "
            "Total tokens is the like-for-like number; cost comes from prices.yaml.\n"
            "- **Grounding**: passages retrieved, how many the answer cites, tool calls, pictures looked at.\n"
            + ui.mode_help())

if not keys:
    st.info("Pick at least one arm and mode in the sidebar.")
    st.stop()


# ----------------------------------------------------------------------------- drawing

def tile_grid(ks: list[str]) -> dict[str, object]:
    slots = {}
    for col, k in zip(st.columns(len(ks)), ks):
        with col:
            st.subheader(combo_label(*k.split("/")))
            slots[k] = st.empty()
    return slots


def show_table(slot, results: dict, errors: dict, ks: list[str], running: dict | None = None) -> None:
    with slot.container():
        st.markdown("**Side by side**")
        st.dataframe(ui.comparison_rows(results, errors, ks, running), hide_index=True, width="stretch")


def show_turn(turn: dict) -> None:
    slots = tile_grid(turn["keys"])
    for k, slot in slots.items():
        with slot.container():
            if k in turn["results"]:
                ui.show_tile(turn["results"][k], show_all_text)
            else:
                st.error(turn["errors"].get(k, "no answer"))
    show_table(st.empty(), turn["results"], turn["errors"], turn["keys"])


def ask_all(question: str, ks: list[str]) -> tuple[dict, dict]:
    from research_qa.agent.ask import ask, run_parallel

    results: dict[str, Answer] = {}
    errors: dict[str, str] = {}
    tasks = {}
    for k in ks:
        arm, mode = k.split("/")
        chat = None
        if mode == "agent":  # one conversation per arm for follow-up questions; created on the main thread
            try:
                if arm not in st.session_state.chats:
                    from research_qa.agent.foundry import Chat

                    st.session_state.chats[arm] = Chat(arm)
                chat = st.session_state.chats[arm]
            except Exception as e:  # noqa: BLE001
                errors[k] = f"{type(e).__name__}: {e}"
                continue
        tasks[k] = (lambda q=question, a=arm, m=mode, c=chat: ask(q, a, m, use_tools=use_tools, chat=c))
    slots = tile_grid(ks)
    for k in ks:
        slots[k].error(errors[k]) if k in errors else slots[k].info("Running…")
    table = st.empty()
    show_table(table, results, errors, ks, {k: 0.0 for k in tasks})

    def on_done(k: str, r, err):
        if r is not None:
            results[k] = r
            with slots[k].container():
                ui.show_tile(r, show_all_text)
        else:
            errors[k] = err or "failed"
            slots[k].error(errors[k])
        show_table(table, results, errors, ks, {x: 0.0 for x in tasks if x not in results and x not in errors})

    def on_tick(running: dict):
        for k, secs in running.items():
            slots[k].info(f"Running… {secs:.0f} s")
        show_table(table, results, errors, ks, running)

    if tasks:
        run_parallel(tasks, on_done, on_tick)
    return results, errors


def apply_max_docs(n: int) -> None:
    from research_qa.search.knowledge import kb_settings, set_max_docs

    for arm in {k.split("/")[0] for k in keys}:
        try:
            if kb_settings(arm).get("max_docs") != n:
                set_max_docs(arm, n)
        except Exception as e:  # noqa: BLE001
            st.warning(f"Could not set the passage limit on {ARMS[arm].label}: {type(e).__name__}: {str(e)[:160]}")


# ----------------------------------------------------------------------------- conversation

for turn in st.session_state.history:
    with st.chat_message("user"):
        st.markdown(turn["question"])
    with st.chat_message("assistant"):
        show_turn(turn)

question = st.chat_input("Ask about the reports, for example: what is Germany's 2030 balance in Fig 27 of the April Money Map?")
if question:
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        apply_max_docs(max_docs)
        results, errors = ask_all(question, keys)
    st.session_state.history.append({"question": question, "results": results, "errors": errors, "keys": keys})
    st.rerun()
