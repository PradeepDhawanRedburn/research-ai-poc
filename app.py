"""Streamlit UI that compares four ways of answering questions about the research reports:

- Agentic retrieval: Azure AI Search knowledge base + Foundry agent (`agentic/`)
- RAG: local Chroma index, hybrid search, one answer call (`rag/`)

each on Word (docx) and on MarkItDown output (A+ markdown). Pick any of the four in the sidebar. One question goes to
all of them and the answers appear side by side, with the passages each used, the file they came from, which of them
were cited, and the pictures the model looked at.

Needs both extras:  uv run --extra rag --extra agentic streamlit run app.py
"""
from __future__ import annotations

import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from agentic.config import SOURCES, settings
from rag.config import settings as rag_settings

APPROACHES = {"agentic": "Agentic", "rag": "RAG"}
FORMATS = {"docx": "Word (docx)", "markdown": "MarkItDown (A+)"}
SOLUTION_KEYS = [f"{a}-{s}" for s in SOURCES for a in APPROACHES]  # Word pair first, then the Markdown pair
LABELS = {f"{a}-{s}": f"{APPROACHES[a]} · {FORMATS[s]}" for s in SOURCES for a in APPROACHES}
FILE_KIND = {"docx": "Word file", "markdown": "MarkItDown output (A+)"}
FILE_PLURAL = {"docx": "Word files", "markdown": "MarkItDown outputs (A+)"}
ICONS = {"plan": "🧭", "retrieve": "🔎", "reason": "🤔", "look": "👁️", "answer": "✍️"}


@dataclass
class RagAnswer:
    question: str
    source: str  # docx or markdown
    text: str
    passages: list  # LangChain documents in rank order; [n] in the answer is passages[n - 1]
    images: list[str]
    seconds: float
    collection: str
    model: str


def rag_file(source: str, doc_id: str) -> str:
    """RAG chunks keep the report id, not the file path, so name the input file the id came from."""
    folder, ext = (rag_settings.docx_dir, ".docx") if source == "docx" else (rag_settings.markdown_dir, ".md")
    name = f"{doc_id}{ext}"
    return name if (folder / name).exists() else doc_id


def cited_numbers(text: str) -> set[int]:
    return {int(n) for n in re.findall(r"\[(\d+)\]", text)}


def run_rag(question: str, source: str, k: int, use_images: bool) -> RagAnswer:
    from rag.pipeline import answer as rag_answer

    t0 = time.perf_counter()
    a = rag_answer(question, source, k=k, hybrid=True, use_images=use_images)
    return RagAnswer(question, source, a.answer, a.sources, a.images, time.perf_counter() - t0,
                     rag_settings.collection(source), rag_settings.chat_deployment)

st.set_page_config(page_title="Research reports: agentic vs RAG", layout="wide")


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


@st.cache_data(ttl=60, show_spinner=False)
def rag_stats(source: str) -> dict:
    from rag.pipeline import retriever as rag_retriever

    try:
        docs = rag_retriever(source).docs
    except Exception as e:  # noqa: BLE001 - empty collection or missing .env
        return {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    return {"chunks": len(docs), "files": Counter(d.metadata.get("doc_id") for d in docs)}


with st.sidebar:
    st.header("Settings")
    selected = st.multiselect("Solutions to compare", SOLUTION_KEYS, default=SOLUTION_KEYS, format_func=LABELS.get,
                              help="Every question goes to all selected solutions, side by side.")
    agentic_sources = [s for s in SOURCES if f"agentic-{s}" in selected]
    rag_sources = [s for s in SOURCES if f"rag-{s}" in selected]
    infos = {s: stats(s) for s in agentic_sources}
    rag_infos = {s: rag_stats(s) for s in rag_sources}
    live_docs = next((i["kb"]["max_docs"] for i in infos.values() if "kb" in i and i["kb"]["max_docs"]),
                     settings.max_docs)
    max_docs = st.slider("Passages per search (agentic) or chunks retrieved (RAG)", 3, 30, int(live_docs),
                         help="The same number goes to both approaches. Agentic: stored on the knowledge base and "
                              "applied before the next question. Fewer passages means a shorter prompt and a faster "
                              "answer, more passages means more recall.")
    use_images = st.checkbox("Let the agent look at figure pictures", value=True,
                             help="Off: the agent only reads the text and the stored picture descriptions.")
    show_all_text = st.checkbox("Show the text of every retrieved passage", value=False)
    if st.button("Clear conversation"):
        st.session_state.history, st.session_state.chats = [], {}
        st.rerun()
    if st.button("Refresh index info"):
        stats.clear()
        rag_stats.clear()
        st.rerun()

    st.header("Where answers come from")
    st.caption(f"Search service `{settings.search_endpoint.split('//')[-1].split('.')[0]}` · agent model "
               f"`{settings.agent_model}` (reasoning {settings.reasoning_effort}) · pictures read by "
               f"`{settings.vision_deployment}`")
    for s in agentic_sources:
        n, info = settings.names(s), infos[s]
        st.markdown(f"**{LABELS[f'agentic-{s}']}**")
        st.caption(f"Reads the {FILE_PLURAL[s]}")
        if "error" in info:
            st.error(f"Index {n.index} is not available: {info['error']}\n\nRun `python -m agentic up --source {s}`.")
            continue
        kb = info["kb"]
        st.markdown(f"- Index `{n.index}`: {info['chunks']} chunks of about {kb['chunk_chars'] or '?'} characters\n"
                    f"- Knowledge base `{n.knowledge_base}` (max {kb['max_docs']} passages per search)\n"
                    f"- Agent `{n.agent}`")
        st.caption("Chunks per file: " + ", ".join(f"{f} {c}" for f, c in sorted(info["files"].items())))
    for s in rag_sources:
        st.markdown(f"**{LABELS[f'rag-{s}']}**")
        st.caption(f"Reads the {FILE_PLURAL[s]}; chunks and embeddings are stored locally in Chroma")
        info = rag_infos[s]
        if "error" in info:
            st.error(f"Collection {rag_settings.collection(s)} is not available: {info['error']}\n\n"
                     f"Run `python -m rag ingest --source {s}`.")
            continue
        st.markdown(f"- Collection `{rag_settings.collection(s)}`: {info['chunks']} chunks of about "
                    f"{rag_settings.chunk_chars} characters (`RAG_CHUNK_CHARS`)\n"
                    f"- Answer model `{rag_settings.chat_deployment}`, which also reads the pictures\n"
                    "- Hybrid search: vector + BM25, fused by reciprocal rank")
        st.caption("Chunks per file: " + ", ".join(f"{rag_file(s, d)} {c}" for d, c in sorted(info["files"].items())))
    with st.expander("What agentic adds over plain RAG"):
        st.markdown(
            "- **Plain RAG**: embed the question, fetch the top chunks (vector + BM25), answer from them.\n"
            "- **Agentic**: the agent first **rewrites the question into several search queries**, the search service runs "
            "them (hybrid + semantic reranking) and merges the results, the agent can **look at a picture** with a vision "
            "model when the text does not say, and it **links each claim to a passage**. The steps panel shows each of these.\n"
            "- Chunk size is fixed when the index is built (`python -m agentic index --chunk-chars N --reset`).")

st.title("Research reports Q&A: agentic vs RAG, Word vs MarkItDown")
st.session_state.setdefault("history", [])
if not selected:
    st.info("Pick at least one solution in the sidebar.")
    st.stop()


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


def show_agentic(a) -> None:
    st.markdown(a.text)
    n = a.names
    st.caption(f"{LABELS[f'agentic-{n.source}']} · index `{n.index}` · knowledge base `{n.knowledge_base}` · agent `{n.agent}` "
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


def show_rag(a: RagAnswer) -> None:
    st.markdown(a.text)
    st.caption(f"RAG · collection `{a.collection}` · answer model `{a.model}` · {a.seconds:.1f} s. "
               "Numbers in brackets are the passages below.")
    wrong = [p for p in a.passages if p.metadata.get("source") != a.source]
    if wrong:
        st.error(f"{len(wrong)} chunk(s) did not come from the {FILE_PLURAL[a.source]}: "
                 + ", ".join(p.id for p in wrong))
    elif a.passages:
        files = sorted({rag_file(a.source, p.metadata.get("doc_id", "")) for p in a.passages})
        st.success(f"All {len(a.passages)} retrieved chunks come from collection `{a.collection}`, "
                   f"{FILE_KIND[a.source]}: {', '.join(files)}")
    else:
        st.info("The search returned no chunks.")

    cited = cited_numbers(a.text)
    shown = [(n, p) for n, p in enumerate(a.passages, 1) if n in cited]
    with st.expander(f"Cited passages ({len(shown)})", expanded=True):
        if not shown:
            st.warning("The answer does not cite any passage.")
        for n, p in shown:
            m = p.metadata
            st.markdown(f"**[{n}] {m.get('doc_id')} · {m.get('kind')}** · {FILE_KIND[a.source]} "
                        f"`{rag_file(a.source, m.get('doc_id', ''))}` · chunk `{p.id}`")
            st.caption(" · ".join(x for x in (m.get("title"), m.get("date"), m.get("caption") or m.get("section"))
                                  if x))
            st.text(p.page_content[:2500])
    if a.images:
        with st.expander(f"Pictures sent to the model ({len(a.images)})", expanded=True):
            for img in a.images:
                st.image(img, width=420)
    with st.expander(f"Retrieved passages ({len(a.passages)}), in the order they were ranked"):
        st.dataframe(
            [{"#": n, "cited": "yes" if n in cited else "", "chunk": p.id,
              "file": rag_file(a.source, p.metadata.get("doc_id", "")), "kind": p.metadata.get("kind"),
              "section / caption": p.metadata.get("caption") or p.metadata.get("section")}
             for n, p in enumerate(a.passages, 1)],
            hide_index=True, width="stretch",
        )
        if show_all_text:
            for n, p in enumerate(a.passages, 1):
                st.markdown(f"**[{n}] {p.id}**" + (" · cited" if n in cited else ""))
                st.text(p.page_content[:1500])


def cited_count(a) -> int:
    if isinstance(a, RagAnswer):
        return len(cited_numbers(a.text) & set(range(1, len(a.passages) + 1)))
    return len(a.cited)


def show_turn(answers: dict, errors: dict, selected: list[str]) -> None:
    """One row per input file (Word, then Markdown), agentic and RAG side by side in each row."""
    keys = [k for k in SOLUTION_KEYS if k in selected]
    for fmt in SOURCES:
        row = [k for k in keys if k.endswith(f"-{fmt}")]
        if not row:
            continue
        for col, key in zip(st.columns(len(row)), row):
            with col:
                st.subheader(LABELS[key])
                if key in errors:
                    st.error(errors[key])
                elif key in answers:
                    (show_rag if isinstance(answers[key], RagAnswer) else show_agentic)(answers[key])
    rows = []
    for key in keys:
        if key in answers:
            a = answers[key]
            rows.append({"solution": LABELS[key], "seconds": round(a.seconds, 1), "passages": len(a.passages),
                         "cited": cited_count(a), "error": ""})
        else:
            rows.append({"solution": LABELS[key], "seconds": None, "passages": None, "cited": None,
                         "error": errors.get(key, "")})
    st.markdown("**Side by side**")
    st.dataframe(rows, hide_index=True, width="stretch")


def ask_all(question: str, agentic: list[str], rag: list[str], k: int, use_images: bool) -> tuple[dict, dict]:
    """Agentic calls run in background threads; RAG runs on the main thread because its Chroma client is shared."""
    answers, errors = {}, {}
    futures = {}
    with ThreadPoolExecutor(max_workers=max(1, len(agentic))) as pool:
        for s in agentic:  # chats are created here, on the main thread, because they live in session state
            key = f"agentic-{s}"
            try:
                futures[key] = pool.submit(chat_for(s).ask, question, use_images=use_images)
            except Exception as e:  # noqa: BLE001
                errors[key] = f"{type(e).__name__}: {e}"
        for s in rag:
            key = f"rag-{s}"
            try:
                answers[key] = run_rag(question, s, k, use_images)
            except Exception as e:  # noqa: BLE001
                errors[key] = f"{type(e).__name__}: {e}"
        for key, fut in futures.items():
            try:
                answers[key] = fut.result()
            except Exception as e:  # noqa: BLE001
                errors[key] = f"{type(e).__name__}: {e}"
    return answers, errors


def apply_max_docs() -> None:
    from agentic.search_setup import set_max_docs

    changed = False
    for s in agentic_sources:
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
        show_turn(turn["answers"], turn["errors"], turn["selected"])

question = st.chat_input("Ask about the reports, e.g. How many oranges are in the Fanta image?")
if question:
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        try:
            apply_max_docs()
        except Exception as e:  # noqa: BLE001
            st.warning(f"Could not change the passage limit: {type(e).__name__}: {e}")
        with st.spinner("Running the selected solutions: rewriting, searching, looking at pictures..."):
            answers, errors = ask_all(question, agentic_sources, rag_sources, max_docs, use_images)
        show_turn(answers, errors, selected)
    st.session_state.history.append(
        {"question": question, "answers": answers, "errors": errors, "selected": selected})
