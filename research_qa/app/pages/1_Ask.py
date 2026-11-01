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
if st.session_state.pop("clear_expected", False):
    st.session_state["typed_expected"] = ""  # a typed expected answer belongs to one question only
st.session_state.setdefault("history", [])
st.session_state.setdefault("chats", {})

ALL_KEYS = [combo_key(a, m) for a in ARMS for m in MODES]
LABELS = {k: ui.combo_title(*k.split("/")) for k in ALL_KEYS}

# questions from the verified golden set, one click each; the answers are known so the tiles can be judged live
# Picked from the after-audit run: first row where every route agrees, second row where they differ or all fall short.
DEMO = [("Text", "MM04-01", "What the April Money Map expects the Fed to do in May"),
        ("Two editions", "MMX-01", "How the ECB view changed between April and July"),
        ("Not in the reports", "MM04-14", "The Bank of Japan rate the April Money Map forecasts"),
        ("Chart value", "MM04-06", "Germany's 2030 balance in Fig 27 of the April Money Map"),
        ("Report details", "MM04-13", "The publication date of the April Money Map"),
        ("Matrix", "EVO-16", "Is Evolution content available on Stake in Italy (Fig 4)?"),
        ("Picture", "CCEP-24", "The two Coca-Cola products with the Aldi Price Match badge"),
        ("Picture, hard", "IMG-08", "How many game tiles are greyed out in the Rainbet lobby (Fig 6)")]

# ----------------------------------------------------------------------------- sidebar

with st.sidebar:
    st.markdown("**Compare**")
    picked = st.pills("Arms and modes", ALL_KEYS, format_func=lambda k: LABELS[k], selection_mode="multi",
                      default=ALL_KEYS, label_visibility="collapsed")
    keys = [k for k in ALL_KEYS if k in (picked or [])]
    typed_expected = st.text_input("Expected answer (optional)", key="typed_expected",
                                   help="With one, every tile gets a tick, a half mark or a cross from the same judge "
                                        "as the scorecard. The verified questions below carry their own.")
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
                    for row in ui.comparison_rows(t["results"], t["errors"], t["keys"], grades=t.get("grades"))]
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
            'model. Each tile shows the answer, how long it took, and what it read.</p>', unsafe_allow_html=True)

if not keys:
    st.info("Pick at least one arm and mode in the sidebar.")
    st.stop()

golden = {q["id"]: q for q in ui.golden_questions()}
demo = [(tag, golden[i], short) for tag, i, short in DEMO if i in golden]
if demo and not st.session_state.history:
    st.markdown('<div style="text-align:center; margin:2.2rem 0 1rem"><div style="font-size:1.6rem; font-weight:650">'
                'What would you like to know?</div><div class="muted">Pick a verified question and every tile is '
                'graded automatically, or type your own below.</div></div>', unsafe_allow_html=True)
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
                slots[k] = st.empty()
    return slots


def expectation(question: str) -> dict | None:
    """What to grade against: the golden answer when the question is a verified one, else the typed one."""
    q = next((q for q in golden.values() if q["question"] == question), None)
    if q:
        return {"answer": q["answer"], "type": q["type"], "source": "golden set"}
    if typed_expected.strip():
        return {"answer": typed_expected.strip(), "type": "text_fact", "source": "typed"}
    return None


def expected_note(question: str, exp: dict | None = None) -> None:
    if exp:
        st.markdown(f'<div class="muted">Expected answer ({exp["source"]}): <b>{exp["answer"]}</b></div>',
                    unsafe_allow_html=True)


def show_turn(turn: dict, compact: bool = False) -> None:
    """The latest turn in full; earlier turns compact (answer and numbers only, so the page stays light)."""
    grades = turn.get("grades", {})
    expected_note(turn["question"], turn.get("expected"))
    slots = tile_grid(turn["keys"])
    for k, slot in slots.items():
        with slot.container():
            if k in turn["results"]:
                ui.show_tile(turn["results"][k], show_all_text, compact=compact, grade=grades.get(k))
            else:
                ui.tile_error(slot, *k.split("/"), turn["errors"].get(k, "no answer"))
    if not compact:
        st.markdown("##### Side by side")
        ui.comparison(turn["results"], turn["errors"], turn["keys"], grades=grades)


def ask_all(question: str, ks: list[str], exp: dict | None = None) -> tuple[dict, dict, dict]:
    from research_qa.agent.ask import ask, run_parallel

    results: dict[str, Answer] = {}
    errors: dict[str, str] = {}
    grades: dict[str, dict] = {}
    judge = ui.judge_fn() if exp else None  # built here: the answers are judged inside the worker threads
    if exp and judge is None:
        st.caption("No judge is configured (JUDGE_DEPLOYMENT), so the tiles are not graded.")
    partials: dict[str, str] = {}   # streamed text so far, per single-pass tile; drawn on the main thread each tick
    tasks = {}
    for k in ks:
        arm, mode = k.split("/")
        chat = None
        if mode == "agentic":  # one conversation per arm for follow-up questions; created on the main thread
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

        def work(q=question, a=arm, m=mode, c=chat, key=k):
            ans = ask(q, a, m, use_tools=use_tools, chat=c, max_docs=max_docs,
                      on_token=(lambda text, key=key: partials.__setitem__(key, text)) if m == "single-pass" else None)
            if judge:
                grades[key] = ui.grade(judge, q, exp["answer"], exp["type"], ans)
            return ans

        tasks[k] = work
    expected_note(question, exp)
    slots = tile_grid(ks)
    for k in ks:
        if k in errors:
            ui.tile_error(slots[k], *k.split("/"), errors[k])
        else:
            ui.running_tile(slots[k], *k.split("/"))
    st.markdown("##### Side by side")
    table = st.empty()

    def draw_table(running: dict | None):
        with table.container():
            ui.comparison(results, errors, ks, running, grades)

    draw_table({k: 0.0 for k in tasks})

    def on_done(k: str, r, err):
        if r is not None:
            results[k] = r
            with slots[k].container():
                ui.show_tile(r, show_all_text, grade=grades.get(k))
        else:
            errors[k] = err or "failed"
            ui.tile_error(slots[k], *k.split("/"), errors[k])
        draw_table({x: 0.0 for x in tasks if x not in results and x not in errors})

    def on_tick(running: dict):
        for k, secs in running.items():
            if partials.get(k):
                with slots[k].container():
                    ui.tile_header(*k.split("/"))
                    st.markdown(partials[k] + " ▌")
                    st.caption(f"Writing… {secs:.0f} s")
            else:
                ui.running_tile(slots[k], *k.split("/"), secs=secs)

    if tasks:
        run_parallel(tasks, on_done, on_tick, tick=1.0)
    draw_table(None)
    return results, errors, grades


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
        exp = expectation(question)
        results, errors, grades = ask_all(question, keys, exp)
    if exp and exp["source"] == "typed":
        st.session_state.clear_expected = True
    st.session_state.history.append({"question": question, "results": results, "errors": errors, "keys": keys,
                                     "grades": grades, "expected": exp})
    st.rerun()
