"""Ask: one question, every selected arm × mode at once, each tile filled the moment its answer lands."""
from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import streamlit as st  # noqa: E402

from research_qa.app import ui  # noqa: E402
from research_qa.config import ARMS, MODES, combo_key, settings  # noqa: E402
from research_qa.schema import Answer  # noqa: E402

ui.setup("Ask")
ui.require_store()
st.session_state.setdefault("history", [])
st.session_state.setdefault("chats", {})

ALL_KEYS = [combo_key(a, m) for a in ARMS for m in MODES]
LABELS = {k: ui.combo_title(*k.split("/")) for k in ALL_KEYS}

# questions from the verified golden set, one click each; the answers are known so the tiles can be judged live
DEMO = [("Text", "MM04-01", "What the April Money Map expects the Fed to do in May"),
        ("Text", "MM07-02", "Which central bank the July Money Map expects to cut, and to what"),
        ("Chart value", "MM04-06", "Germany's 2030 balance in Fig 27 of the April Money Map"),
        ("Matrix", "EVO-16", "Is Evolution content available on Stake in Italy (Fig 4)?"),
        ("Picture", "IMG-06", "The three cans in Fig 22 and what is printed on them"),
        ("Picture", "CCEP-22", "The promotion on the Pepsi Max sign in Fig 40"),
        ("Two editions", "MMX-01", "How the ECB view changed between April and July"),
        ("Table", "EVO-08", "Evolution's FY26E revenue estimate")]

# ----------------------------------------------------------------------------- sidebar

with st.sidebar:
    st.markdown("**Compare**")
    picked = st.pills("Arms and modes", ALL_KEYS, format_func=lambda k: LABELS[k], selection_mode="multi",
                      default=ALL_KEYS, label_visibility="collapsed")
    keys = [k for k in ALL_KEYS if k in (picked or [])]
    with st.expander("What the four are"):
        st.markdown(ui.arm_help())
        st.markdown(ui.mode_help())
    with st.expander("Settings"):
        max_docs = st.slider("Passages per retrieval", 3, 30, settings.max_docs,
                             help="The same for every arm. Fewer means fewer tokens; more means more recall.")
        use_tools = st.checkbox("Markdown+ may use facts and pictures", value=True,
                                help="Off: both arms answer from the retrieved text alone.")
        show_all_text = st.checkbox("Show every retrieved passage in Sources", value=False)
    if st.button("New conversation", width="stretch"):
        st.session_state.history, st.session_state.chats = [], {}
        st.rerun()
    totals = ui.session_totals(st.session_state.history, keys)
    if totals:
        with st.expander(f"This session · {len(st.session_state.history)} question"
                         f"{'s' if len(st.session_state.history) != 1 else ''}"):
            st.dataframe(totals, hide_index=True, width="stretch")
            buf = io.StringIO()
            rows = [{"question": t["question"], **row} for t in st.session_state.history
                    for row in ui.comparison_rows(t["results"], t["errors"], t["keys"])]
            if rows:
                fields = list(dict.fromkeys(k for r in rows for k in r))
                w = csv.DictWriter(buf, fieldnames=fields)
                w.writeheader()
                w.writerows(rows)
                st.download_button("Download as CSV", buf.getvalue(), "comparison.csv", "text/csv", width="stretch")
ui.sidebar_footer()

# ----------------------------------------------------------------------------- header

st.title("Ask the reports")
st.markdown('<p class="lede">One question, answered by every route at once from the same reports and the same '
            'model. Each tile shows the answer, what it cost, and what it read.</p>', unsafe_allow_html=True)

if not keys:
    st.info("Pick at least one arm and mode in the sidebar.")
    st.stop()

golden = {q["id"]: q for q in ui.golden_questions()}
demo = [(tag, golden[i], short) for tag, i, short in DEMO if i in golden]
if demo and not st.session_state.history:
    st.markdown("**Try one**")
    cols = st.columns(4)
    for i, (tag, q, short) in enumerate(demo):
        with cols[i % 4]:
            if st.button(f"**{tag}** · {short}", key=f"demo-{q['id']}", width="stretch",
                         help=f"{q['question']}  \nExpected: {q['answer']}"):
                st.session_state.pending = q["question"]
                st.rerun()
elif demo:
    with st.expander("Try one of the verified questions"):
        cols = st.columns(4)
        for i, (tag, q, short) in enumerate(demo):
            with cols[i % 4]:
                if st.button(f"**{tag}** · {short}", key=f"demo-{q['id']}", width="stretch",
                             help=f"{q['question']}  \nExpected: {q['answer']}"):
                    st.session_state.pending = q["question"]
                    st.rerun()


# ----------------------------------------------------------------------------- drawing

def tile_grid(ks: list[str]) -> dict[str, object]:
    slots = {}
    for col, k in zip(st.columns(len(ks)), ks):
        with col:
            with st.container(border=True):
                ui.tile_header(*k.split("/"))
                slots[k] = st.empty()
    return slots


def expected_note(question: str) -> None:
    q = next((q for q in golden.values() if q["question"] == question), None)
    if q:
        st.markdown(f'<div class="muted">Verified answer from the golden set: <b>{q["answer"]}</b></div>',
                    unsafe_allow_html=True)


def show_turn(turn: dict, compact: bool = False) -> None:
    """The latest turn in full; earlier turns compact (answer and numbers only, so the page stays light)."""
    expected_note(turn["question"])
    slots = tile_grid(turn["keys"])
    for k, slot in slots.items():
        with slot.container():
            if k in turn["results"]:
                ui.show_tile(turn["results"][k], show_all_text, compact=compact)
            else:
                st.error(turn["errors"].get(k, "no answer"))
    if not compact:
        st.markdown("##### Side by side")
        ui.comparison(turn["results"], turn["errors"], turn["keys"])


def ask_all(question: str, ks: list[str]) -> tuple[dict, dict]:
    from research_qa.agent.ask import ask, run_parallel

    results: dict[str, Answer] = {}
    errors: dict[str, str] = {}
    partials: dict[str, str] = {}   # streamed text so far, per direct tile; drawn on the main thread each tick
    tasks = {}
    for k in ks:
        arm, mode = k.split("/")
        chat = None
        if mode == "agent":  # one conversation per arm for follow-up questions; created on the main thread
            try:
                if arm not in st.session_state.chats:
                    if settings.retrieval_backend == "langchain":
                        from research_qa.lc.answer import Chat
                    else:
                        from research_qa.agent.foundry import Chat

                    st.session_state.chats[arm] = Chat(arm)
                chat = st.session_state.chats[arm]
            except Exception as e:  # noqa: BLE001
                errors[k] = f"{type(e).__name__}: {e}"
                continue
        tasks[k] = (lambda q=question, a=arm, m=mode, c=chat, key=k: ask(
            q, a, m, use_tools=use_tools, chat=c, max_docs=max_docs,
            on_token=(lambda text, key=key: partials.__setitem__(key, text)) if m == "direct" else None))
    expected_note(question)
    slots = tile_grid(ks)
    for k in ks:
        if k in errors:
            slots[k].error(errors[k])
        else:
            ui.running_tile(slots[k], *k.split("/"))
    st.markdown("##### Side by side")
    table = st.empty()

    def draw_table(running: dict | None):
        with table.container():
            ui.comparison(results, errors, ks, running)

    draw_table({k: 0.0 for k in tasks})

    def on_done(k: str, r, err):
        if r is not None:
            results[k] = r
            with slots[k].container():
                ui.show_tile(r, show_all_text)
        else:
            errors[k] = err or "failed"
            slots[k].error(errors[k])
        draw_table({x: 0.0 for x in tasks if x not in results and x not in errors})

    def on_tick(running: dict):
        for k, secs in running.items():
            if partials.get(k):
                with slots[k].container():
                    st.markdown(partials[k] + " ▌")
                    st.caption(f"Writing… {secs:.0f} s")
            else:
                ui.running_tile(slots[k], *k.split("/"), secs=secs)

    if tasks:
        run_parallel(tasks, on_done, on_tick, tick=1.0)
    draw_table(None)
    return results, errors


def apply_max_docs(n: int) -> None:
    if settings.retrieval_backend == "langchain":
        return  # the number goes straight into each call; nothing is stored in Azure
    from research_qa.search.knowledge import kb_settings, set_max_docs

    for arm in {k.split("/")[0] for k in keys}:
        try:
            if kb_settings(arm).get("max_docs") != n:
                set_max_docs(arm, n)
        except Exception as e:  # noqa: BLE001
            st.warning(f"Could not set the passage limit on {ARMS[arm].label}: {type(e).__name__}: {str(e)[:160]}")


# ----------------------------------------------------------------------------- conversation

history = st.session_state.history
for i, turn in enumerate(history):
    last = i == len(history) - 1
    with st.chat_message("user"):
        st.markdown(turn["question"])
    with st.chat_message("assistant"):
        show_turn(turn, compact=not last)

question = st.chat_input("Ask about the reports, for example: what is Germany's 2030 balance in Fig 27 of the April Money Map?")
pending = st.session_state.pop("pending", None)
question = question or pending
if question:
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        apply_max_docs(max_docs)
        results, errors = ask_all(question, keys)
    st.session_state.history.append({"question": question, "results": results, "errors": errors, "keys": keys})
    st.rerun()
