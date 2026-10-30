"""Streamlit UI for the agentic retrieval solutions: ask the reports a question and see exactly how it was answered.

    streamlit run agentic/app.py

Each solution (Word docx or MarkItDown A+ markdown) has its own search index, knowledge base and agent. Under every
answer the page lists the steps the agent took (query rewriting, knowledge base retrieval, looking at pictures,
writing the answer) with timings, which index and source files the passages came from, and which ones are cited.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run as a script, import as the package

from agentic.config import SOURCES, settings  # noqa: E402

LABELS = {"docx": "Solution 1 - Word (docx)", "markdown": "Solution 2 - MarkItDown (A+)"}
FILE_KIND = {"docx": "Word file", "markdown": "MarkItDown output (A+)"}
BOTH = "Both, side by side"
ICONS = {"plan": "🧭", "retrieve": "🔎", "reason": "🤔", "look": "👁️", "answer": "✍️"}

st.set_page_config(page_title="Research agentic retrieval", layout="wide")


@st.cache_data(ttl=60, show_spinner=False)
def stats(source: str) -> dict:
    from agentic.search_setup import index_stats, knowledge_base_settings

    try:
        return {**index_stats(source), "kb": knowledge_base_settings(source)}
    except Exception as e:  # noqa: BLE001 - index missing or no access
        return {"error": f"{type(e).__name__}: {str(e)[:200]}"}


def chat_for(source: str):
    from agentic.foundry import Chat

    chats = st.session_state.setdefault("chats", {})
    if source not in chats:
        chats[source] = Chat(source)
    return chats[source]


with st.sidebar:
    st.header("Settings")
    choice = st.radio("Solution", [*SOURCES, BOTH], format_func=lambda s: s if s == BOTH else LABELS[s])
    sources = list(SOURCES) if choice == BOTH else [choice]
    infos = {s: stats(s) for s in sources}
    live_docs = next((i["kb"]["max_docs"] for i in infos.values() if "kb" in i and i["kb"]["max_docs"]),
                     settings.max_docs)
    max_docs = st.slider("Passages the knowledge base returns per search", 3, 30, int(live_docs),
                         help="Stored on the knowledge base; applied before the next question. Fewer passages "
                              "means a shorter prompt and a faster answer, more passages means more recall.")
    use_images = st.checkbox("Let the agent look at figure pictures", value=True,
                             help="Off: the agent only reads the text and the stored picture descriptions.")
    show_all_text = st.checkbox("Show the text of every retrieved passage", value=False)
    if st.button("Clear conversation"):
        st.session_state.history, st.session_state.chats = [], {}
        st.rerun()
    if st.button("Refresh index info"):
        stats.clear()
        st.rerun()

    st.header("Where answers come from")
    st.caption(f"Search service `{settings.search_endpoint.split('//')[-1].split('.')[0]}` · agent model "
               f"`{settings.agent_model}` (reasoning {settings.reasoning_effort}) · pictures read by "
               f"`{settings.vision_deployment}`")
    for s in sources:
        n, info = settings.names(s), infos[s]
        st.markdown(f"**{LABELS[s]}**")
        st.caption(f"Reads the {FILE_KIND[s]}s")
        if "error" in info:
            st.error(f"Index {n.index} is not available: {info['error']}\n\nRun `python -m agentic up --source {s}`.")
            continue
        kb = info["kb"]
        st.markdown(f"- Index `{n.index}`: {info['chunks']} chunks of about {kb['chunk_chars'] or '?'} characters\n"
                    f"- Knowledge base `{n.knowledge_base}` (max {kb['max_docs']} passages per search)\n"
                    f"- Agent `{n.agent}`")
        st.caption("Chunks per file: " + ", ".join(f"{f} {c}" for f, c in sorted(info["files"].items())))
    with st.expander("What agentic adds over plain RAG"):
        st.markdown(
            "- **Plain RAG**: embed the question, fetch the top chunks (vector + BM25), answer from them.\n"
            "- **Agentic**: the agent first **rewrites the question into several search queries**, the search service runs "
            "them (hybrid + semantic reranking) and merges the results, the agent can **look at a picture** with a vision "
            "model when the text does not say, and it **links each claim to a passage**. The steps panel shows each of these.\n"
            "- Chunk size is fixed when the index is built (`python -m agentic index --chunk-chars N --reset`).")

st.title("Research reports Q&A (agentic retrieval)")
st.session_state.setdefault("history", [])


def show_steps(a) -> None:
    visible = [s for s in a.steps if s.kind != "timing"]
    looks = iter(a.looks)
    with st.expander(f"How the agent got this answer: {len(visible)} steps, {a.seconds:.1f} s", expanded=True):
        for i, s in enumerate(visible, 1):
            secs = f" · {s.seconds:.1f} s" if s.seconds is not None else ""
            st.markdown(f"**{i}. {ICONS.get(s.kind, '')} {s.title}**{secs}")
            if s.detail:
                st.caption(s.detail.replace("\n", "  \n"))
            for it in (s.items if s.kind == "plan" else []):
                st.markdown(f"- {it}")
            if s.kind == "look":
                look = next(looks, None)
                for p in (look.paths if look else []):
                    st.image(p, width=320)
        rounds = [s for s in a.steps if s.kind == "timing"]
        st.markdown("**Timing** (each round is one call to the agent; a round with the knowledge base call includes the search)")
        st.dataframe([{"round": s.title.split(": ", 1)[0], "what": s.title.split(": ", 1)[-1],
                       "seconds": round(s.seconds or 0, 1), "tokens": s.detail} for s in rounds]
                     + [{"round": "Figure inspection", "what": f"{len(a.looks)} vision call(s), run on this machine",
                         "seconds": round(sum(lk.seconds for lk in a.looks), 1), "tokens": ""}] * bool(a.looks),
                     hide_index=True, width="stretch")
        st.caption(f"Total {a.seconds:.1f} s · {a.input_tokens} tokens in, {a.output_tokens} out "
                   f"({a.reasoning_tokens} reasoning)")


def show_answer(a) -> None:
    st.markdown(a.text)
    n = a.names
    st.caption(f"{LABELS[n.source]} · index `{n.index}` · knowledge base `{n.knowledge_base}` · agent `{n.agent}` "
               f"({a.model}). Numbers in brackets are the passages below.")
    for e in a.errors:
        st.error(f"The knowledge base call failed: {e}")
    show_steps(a)

    mismatched = [p for p in a.passages if p.source != n.source or p.index != n.index]
    if mismatched:
        st.error(f"{len(mismatched)} passage(s) did not come from {n.index} ({n.source}): "
                 + ", ".join(f"{p.id} <- {p.index}/{p.source}" for p in mismatched))
    elif a.passages:
        files = sorted({p.source_file for p in a.passages})
        st.success(f"All {len(a.passages)} retrieved passages come from index `{n.index}`, "
                   f"{FILE_KIND[n.source]}: {', '.join(files)}")
    else:
        st.info("The knowledge base returned no passages.")

    shown = [p for p in a.passages if p.cited or p.inspected]
    with st.expander(f"Cited or inspected passages ({len(shown)})", expanded=True):
        if not a.cited:
            st.warning("The answer does not link to any passage.")
        for p in shown:
            tags = " · ".join(t for t, on in (("cited", p.cited), ("picture inspected", p.inspected)) if on)
            st.markdown(f"**[{p.rank}] {p.doc_id} · {p.kind}** ({tags}) · {FILE_KIND.get(p.source, p.source)} "
                        f"`{p.source_file}` · chunk `{p.id}`")
            st.caption(f"{p.title} ({p.date}) · {p.caption or p.section}")
            st.text(p.text[:2500])
            for img in p.image_paths:
                if Path(img).exists():
                    st.image(img, width=420)

    with st.expander(f"Retrieved passages ({len(a.passages)}), in the order the knowledge base ranked them"):
        st.dataframe(
            [{"#": p.rank, "cited": "yes" if p.cited else "", "picture inspected": "yes" if p.inspected else "",
              "chunk": p.id, "source": p.source, "file": p.source_file, "kind": p.kind,
              "section / caption": (p.caption or p.section)}
             for p in a.passages],
            hide_index=True, width="stretch",
        )
        if show_all_text:
            for p in a.passages:
                st.markdown(f"**[{p.rank}] {p.id}**" + (" · cited" if p.cited else ""))
                st.text(p.text[:1500])
    with st.expander("Answer exactly as the agent wrote it"):
        st.text(a.raw_text)


def apply_max_docs() -> None:
    from agentic.search_setup import set_max_docs

    changed = False
    for s in sources:
        kb = infos[s].get("kb")
        if kb and kb["max_docs"] != max_docs:
            set_max_docs(s, max_docs)
            changed = True
    if changed:
        stats.clear()


for turn in st.session_state.history:
    with st.chat_message("user"):
        st.markdown(turn["question"])
    with st.chat_message("assistant"):
        cols = st.columns(len(turn["answers"]))
        for col, (source, a) in zip(cols, turn["answers"].items()):
            with col:
                st.subheader(LABELS[source])
                show_answer(a)

question = st.chat_input("Ask about the reports, e.g. How many oranges are in the Fanta image?")
if question:
    with st.chat_message("user"):
        st.markdown(question)
    answers = {}
    with st.chat_message("assistant"):
        try:
            apply_max_docs()
        except Exception as e:  # noqa: BLE001
            st.warning(f"Could not change the passage limit: {type(e).__name__}: {e}")
        cols = st.columns(len(sources))
        for col, source in zip(cols, sources):
            with col:
                st.subheader(LABELS[source])
                with st.spinner("Agent is working: rewriting the question, searching, looking at pictures..."):
                    try:
                        answers[source] = chat_for(source).ask(question, use_images=use_images)
                    except Exception as e:  # noqa: BLE001
                        st.error(f"{type(e).__name__}: {e}")
                        continue
                show_answer(answers[source])
    if answers:
        st.session_state.history.append({"question": question, "answers": answers})
