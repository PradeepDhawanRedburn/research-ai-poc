"""Streamlit UI: ask the reports a question with one solution at a time; switch solutions to compare answers.

    streamlit run rag/app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run as a script, import as the package

from rag.config import SOURCES, settings  # noqa: E402
from rag.pipeline import answer, retriever  # noqa: E402

LABELS = {"docx": "Solution 1 - Word (docx)", "markdown": "Solution 2 - MarkItDown (A+)"}

st.set_page_config(page_title="Research RAG", layout="wide")


def available_reports(sources: list[str]) -> tuple[dict[str, str], list[str]]:
    """Report id -> title across the chosen indexes, plus the sources whose index is empty or unreachable."""
    reports, missing = {}, []
    for s in sources:
        try:
            for d in retriever(s).docs:
                m = d.metadata
                reports.setdefault(m["doc_id"], " - ".join(x for x in (m.get("title"), m.get("subtitle")) if x))
        except Exception as e:  # empty collection, missing .env
            missing.append(f"{s}: {e}")
    return dict(sorted(reports.items())), missing


with st.sidebar:
    st.header("Settings")
    sources = [st.radio("Solution", SOURCES, format_func=LABELS.get)]
    reports, missing = available_reports(sources)
    doc = st.selectbox("Report", [None, *reports], format_func=lambda d: "All reports" if d is None
                       else f"{d} - {reports[d]}")
    k = st.slider("Chunks to retrieve", 3, 20, settings.top_k)
    hybrid = st.checkbox("Hybrid search (vector + BM25)", value=True)
    use_images = st.checkbox("Show figure images to the model", value=True)
    if st.button("Reload indexes"):
        retriever.cache_clear()
        st.rerun()
    if st.button("Clear conversation"):
        st.session_state.history = []
        st.rerun()

st.title("Research reports Q&A")
for m in missing:
    st.warning(f"Index not available ({m}). Run `python -m rag ingest --source {m.split(':')[0]}`.")

st.session_state.setdefault("history", [])


def show_answer(a) -> None:
    st.markdown(a.answer)
    if a.images:
        with st.expander(f"Images shown to the model ({len(a.images)})"):
            for p in a.images:
                st.image(p, width="stretch")
    with st.expander(f"Sources ({len(a.sources)})"):
        for n, d in enumerate(a.sources, 1):
            m = d.metadata
            label = m.get("caption") or m.get("section") or ""
            st.markdown(f"**[{n}] {m.get('doc_id')} · {m.get('kind')}** {label}")
            st.text(d.page_content[:1500])


for turn in st.session_state.history:
    with st.chat_message("user"):
        st.markdown(turn["question"])
    with st.chat_message("assistant"):
        cols = st.columns(len(turn["answers"]))
        for col, (source, a) in zip(cols, turn["answers"].items()):
            with col:
                st.subheader(LABELS[source])
                show_answer(a)

question = st.chat_input("Ask about the reports, e.g. What does the April Money Map expect the Fed to do in May?")
if question:
    with st.chat_message("user"):
        st.markdown(question)
    answers = {}
    with st.chat_message("assistant"):
        cols = st.columns(len(sources))
        for col, source in zip(cols, sources):
            with col:
                st.subheader(LABELS[source])
                with st.spinner("Searching and answering..."):
                    try:
                        answers[source] = answer(question, source, k=k, doc_id=doc, hybrid=hybrid,
                                                 use_images=use_images)
                    except Exception as e:
                        st.error(f"{type(e).__name__}: {e}")
                        continue
                show_answer(answers[source])
    if answers:
        st.session_state.history.append({"question": question, "answers": answers})
