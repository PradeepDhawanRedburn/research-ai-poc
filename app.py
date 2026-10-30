"""Streamlit page that compares four ways of answering questions about the research reports, like for like:

- Agentic retrieval: Azure AI Search knowledge base + Foundry agent (`agentic/`)
- RAG: local Chroma index, hybrid search, one answer call (`rag/`)

each on Word (docx) and on MarkItDown output (A+ markdown). One question goes to every selected solution at the
same time, each in its own thread; a tile fills in the moment its answer arrives, so a slow solution never holds
up the others. Every tile has the same layout (answer, cost line, how it got there, cited passages, all retrieved
passages) and the table underneath puts time, tokens and grounding side by side, with session totals in the
sidebar. The shapes and numbers come from comparison.py; this file only draws them.

Needs both extras:  uv run --extra rag --extra agentic streamlit run app.py
"""
from __future__ import annotations

import csv
import io
from collections import Counter
from pathlib import Path

import streamlit as st

from agentic.config import settings
from comparison import (FILE_KIND, FILE_PLURAL, LABELS, SOLUTION_KEYS, SOURCES, Result, comparison_rows,
                        from_agentic, from_rag, run_parallel, session_totals)
from rag.config import settings as rag_settings

ICONS = {"plan": "🧭", "retrieve": "🔎", "reason": "🤔", "look": "👁️", "answer": "✍️"}

st.set_page_config(page_title="Research reports: agentic vs RAG, Word vs MarkItDown", layout="wide")
st.session_state.setdefault("history", [])


# ----------------------------------------------------------------------------------------------- backends

def rag_file(source: str, doc_id: str) -> str:
    """RAG chunks keep the report id, not the file path, so name the input file the id came from."""
    folder, ext = (rag_settings.docx_dir, ".docx") if source == "docx" else (rag_settings.markdown_dir, ".md")
    name = f"{doc_id}{ext}"
    return name if (folder / name).exists() else doc_id


def chat_for(source: str):
    """One agent conversation per solution, kept in the session so follow-up questions have their context."""
    from agentic.foundry import Chat

    chats = st.session_state.setdefault("chats", {})
    if source not in chats:
        chats[source] = Chat(source)
    return chats[source]


def agentic_task(source: str, question: str, use_images: bool):
    chat = chat_for(source)  # created here, on the main thread, because it lives in session state
    return lambda: from_agentic(chat.ask(question, use_images=use_images), source)


def rag_task(source: str, question: str, k: int, use_images: bool):
    from rag.pipeline import answer, retriever

    retriever(source)  # load Chroma and build BM25 on the main thread, before the threads start
    return lambda: from_rag(answer(question, source, k=k, hybrid=True, use_images=use_images), source,
                            rag_settings.collection(source), rag_settings.chat_deployment,
                            lambda doc_id: rag_file(source, doc_id))


@st.cache_data(ttl=60, show_spinner=False)
def agentic_stats(source: str) -> dict:
    from agentic.search_setup import index_stats, knowledge_base_settings

    try:
        return {**index_stats(source), "kb": knowledge_base_settings(source)}
    except Exception as e:  # noqa: BLE001 - index missing or no access
        return {"error": f"{type(e).__name__}: {str(e)[:200]}"}


@st.cache_data(ttl=60, show_spinner=False)
def rag_stats(source: str) -> dict:
    from rag.pipeline import retriever

    try:
        docs = retriever(source).docs
    except Exception as e:  # noqa: BLE001 - empty collection or missing .env
        return {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    return {"chunks": len(docs), "files": Counter(d.metadata.get("doc_id") for d in docs)}


def apply_max_docs(sources: list[str], infos: dict, n: int) -> None:
    """The agentic passage limit lives on the knowledge base in Azure; RAG takes it per call."""
    from agentic.search_setup import set_max_docs

    changed = False
    for s in sources:
        kb = infos[s].get("kb")
        if kb and kb["max_docs"] != n:
            set_max_docs(s, n)
            changed = True
    if changed:
        agentic_stats.clear()


# ----------------------------------------------------------------------------------------------- sidebar

with st.sidebar:
    st.header("Settings")
    selected = st.multiselect("Solutions to compare", SOLUTION_KEYS, default=SOLUTION_KEYS, format_func=LABELS.get,
                              help="Every question goes to all selected solutions at the same time.")
    agentic_sources = [s for s in SOURCES if f"agentic-{s}" in selected]
    rag_sources = [s for s in SOURCES if f"rag-{s}" in selected]
    infos = {s: agentic_stats(s) for s in agentic_sources}
    rag_infos = {s: rag_stats(s) for s in rag_sources}
    live_docs = next((i["kb"]["max_docs"] for i in infos.values() if "kb" in i and i["kb"]["max_docs"]),
                     settings.max_docs)
    max_docs = st.slider("Passages per question", 3, 30, int(live_docs),
                         help="The same number for every solution: passages per knowledge base search (agentic) and "
                              "chunks retrieved (RAG). Fewer means a shorter prompt and fewer tokens, more means "
                              "more recall.")
    use_images = st.checkbox("Let the models look at figure pictures", value=True,
                             help="Off: every solution answers from text and stored picture descriptions only.")
    per_row = {"All in one row": 4, "Two per row": 2}[st.radio("Tiles", ["All in one row", "Two per row"],
                                                               horizontal=True)]
    show_all_text = st.checkbox("Show the text of every retrieved passage", value=False)
    c1, c2 = st.columns(2)
    if c1.button("Clear conversation"):
        st.session_state.history, st.session_state.chats = [], {}
        st.rerun()
    if c2.button("Refresh index info"):
        agentic_stats.clear()
        rag_stats.clear()
        st.rerun()

    totals = session_totals(st.session_state.history, SOLUTION_KEYS)
    if totals:
        st.header("This session so far")
        st.dataframe(totals, hide_index=True, width="stretch")
        buf = io.StringIO()
        rows = [{"question": t["question"], **row} for t in st.session_state.history
                for row in comparison_rows(t["results"], t["errors"], t["selected"])]
        w = csv.DictWriter(buf, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
        st.download_button("Download every answer's numbers (CSV)", buf.getvalue(), "comparison.csv", "text/csv")

    st.header("Where answers come from")
    st.caption(f"Search service `{settings.search_endpoint.split('//')[-1].split('.')[0]}` · agent model "
               f"`{settings.agent_model}` (reasoning {settings.reasoning_effort}) · pictures read by "
               f"`{settings.vision_deployment}` (agentic) and `{rag_settings.chat_deployment}` (RAG)")
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
    with st.expander("How the numbers compare"):
        st.markdown(
            "- **Time**: wall-clock for the whole question, split into retrieval, generation and vision. Agentic "
            "usually writes its answer in the same round as the search, so its generation time can be 0.\n"
            "- **Tokens**: input + output of the answering model. RAG sends pictures inside its one call, so they are "
            "in its input tokens; agentic looks at pictures in separate vision calls, shown as *vision tokens*. "
            "**Total tokens** is the like-for-like number.\n"
            "- **Grounding**: passages retrieved, how many the answer cites, pictures looked at. Each tile also "
            "checks that every passage came from its own format's index.\n"
            "- **Agentic** rewrites the question into several queries, lets the search service rerank, and can "
            "decide to look at a picture. **RAG** embeds the question once, fuses vector and keyword hits, and "
            "answers in one call. The steps panel in each tile shows what happened.")

st.title("Research reports Q&A: agentic vs RAG, Word vs MarkItDown")
if not selected:
    st.info("Pick at least one solution in the sidebar.")
    st.stop()


# ----------------------------------------------------------------------------------------------- one tile

def cost_line(r: Result) -> str:
    bits = [f"⏱ **{r.total_s:.1f} s**",
            f"🔤 **{r.total_tokens:,} tokens** ({r.input_tokens:,} in, {r.output_tokens:,} out"
            + (f", {r.reasoning_tokens:,} reasoning" if r.reasoning_tokens else "")
            + (f", {r.vision_tokens:,} vision" if r.vision_tokens else "") + ")",
            f"📄 **{len(r.passages)} passages**, {len(r.cited)} cited"]
    if r.images:
        bits.append(f"👁️ **{len(r.images)} picture{'s' if len(r.images) > 1 else ''}** looked at")
    return " · ".join(bits)


def show_passage(p, source: str) -> None:
    tags = " · ".join(t for t, on in (("cited", p.cited), ("picture inspected", p.inspected)) if on)
    st.markdown(f"**[{p.rank}] {p.doc_id} · {p.kind}**" + (f" ({tags})" if tags else "")
                + f" · {FILE_KIND.get(source, source)} `{p.file}` · chunk `{p.id}`")
    if p.meta or p.heading:
        st.caption(" · ".join(x for x in (p.meta, p.heading) if x))
    st.text(p.text[:2500])


def show_tile(r: Result) -> None:
    st.markdown(r.text or "*(empty answer)*")
    st.caption(f"{'Index' if r.approach == 'agentic' else 'Collection'} `{r.store}` · model `{r.model}` · "
               f"{r.rounds} model round{'s' if r.rounds != 1 else ''}. Numbers in brackets are the passages below.")
    st.markdown(cost_line(r))
    for e in r.errors:
        st.error(e)
    if r.leaked:
        st.error(f"{len(r.leaked)} passage(s) did not come from the {FILE_PLURAL[r.source]}: "
                 + ", ".join(f"{p.id} <- {p.source}" for p in r.leaked))
    elif r.passages:
        st.success(f"All {len(r.passages)} passages come from `{r.store}`, {FILE_KIND[r.source]}: "
                   + ", ".join(r.files))
    else:
        st.info("No passages were retrieved.")

    pics = r.pictures
    looked = [img for _, img, on in pics if on]
    with st.expander(f"Pictures behind the retrieved passages ({len(pics)}), {len(looked)} looked at by a model",
                     expanded=bool(pics)):
        if not pics:
            st.caption("None of the retrieved passages has a picture.")
        for p, img, on in pics:
            st.markdown(f"**[{p.rank}] {p.heading or p.kind}** · " + ("**looked at**" if on else "not looked at")
                        + (" · cited" if p.cited else ""))
            if Path(img).exists():
                st.image(img, width=420)
            else:
                st.caption(f"file not on this machine: {img}")
        st.caption("RAG sends the pictures of its best figure chunks with the question; the agent decides whether "
                   "to inspect one, with a separate vision call each.")

    with st.expander(f"How it got the answer: {len(r.steps)} steps"):
        for i, s in enumerate(r.steps, 1):
            secs = f" · {s.seconds:.1f} s" if s.seconds is not None else ""
            st.markdown(f"**{i}. {ICONS.get(s.kind, '')} {s.title}**{secs}")
            if s.detail:
                st.caption(s.detail.replace("\n", "  \n"))
            for it in s.items:
                st.markdown(f"- {it}")
            for img in s.images:
                if Path(img).exists():
                    st.image(img, width=320)
        st.caption(f"Retrieval {r.retrieval_s:.1f} s · generation {r.generation_s:.1f} s · vision {r.vision_s:.1f} s "
                   f"· total {r.total_s:.1f} s")

    shown = [p for p in r.passages if p.cited or p.inspected]
    with st.expander(f"Cited passages ({len(shown)})", expanded=True):
        if not r.cited:
            st.warning("The answer does not cite any passage.")
        for p in shown:
            show_passage(p, r.source)

    with st.expander(f"All retrieved passages ({len(r.passages)}), in rank order"):
        st.dataframe([{"#": p.rank, "cited": "yes" if p.cited else "",
                       "picture": ("looked at" if (p.inspected or any(i in r.images for i in p.images))
                                   else "yes" if p.images else ""),
                       "chunk": p.id, "file": p.file, "kind": p.kind, "section / caption": p.heading}
                      for p in r.passages], hide_index=True, width="stretch")
        if show_all_text:
            for p in r.passages:
                st.markdown(f"**[{p.rank}] {p.id}**" + (" · cited" if p.cited else ""))
                st.text(p.text[:1500])
    if r.raw_text != r.text:
        with st.expander("Answer exactly as the model wrote it"):
            st.text(r.raw_text)


# ----------------------------------------------------------------------------------------------- one turn

def tile_grid(keys: list[str]) -> dict[str, object]:
    """One placeholder per solution, `per_row` to a row, in a fixed order so a tile never moves."""
    slots = {}
    for i in range(0, len(keys), per_row):
        row = keys[i:i + per_row]
        for col, key in zip(st.columns(per_row), row):
            with col:
                st.subheader(LABELS[key])
                slots[key] = st.empty()
    return slots


def show_table(slot, results: dict, errors: dict, keys: list[str], running: dict[str, float] | None = None) -> None:
    rows = comparison_rows(results, errors, keys)
    for row in rows:
        key = next(k for k in keys if LABELS[k] == row["solution"])
        if running and key in running:
            row["answered"] = f"running… {running[key]:.0f} s"
    with slot.container():
        st.markdown("**Side by side**")
        st.dataframe(rows, hide_index=True, width="stretch")


def show_turn(results: dict, errors: dict, keys: list[str]) -> None:
    """A past turn: everything is known, draw it in one go."""
    slots = tile_grid(keys)
    for key, slot in slots.items():
        with slot.container():
            if key in results:
                show_tile(results[key])
            else:
                st.error(errors.get(key, "no answer"))
    show_table(st.empty(), results, errors, keys)


def ask_all(question: str, keys: list[str]) -> tuple[dict, dict]:
    """Start every solution at once and fill each tile as soon as its answer is in."""
    results: dict[str, Result] = {}
    errors: dict[str, str] = {}
    tasks = {}
    for key in keys:
        approach, source = key.split("-", 1)
        try:
            tasks[key] = (agentic_task(source, question, use_images) if approach == "agentic"
                          else rag_task(source, question, max_docs, use_images))
        except Exception as e:  # noqa: BLE001 - e.g. the index or the collection is missing
            errors[key] = f"{type(e).__name__}: {e}"
    slots = tile_grid(keys)
    for key in keys:
        if key in errors:
            slots[key].error(errors[key])
        else:
            slots[key].info("⏳ Running…")
    table = st.empty()
    show_table(table, results, errors, keys, {k: 0.0 for k in tasks})

    def on_done(key: str, r: Result | None, err: str | None) -> None:
        if r is not None:
            results[key] = r
            with slots[key].container():
                show_tile(r)
        else:
            errors[key] = err or "failed"
            slots[key].error(errors[key])
        show_table(table, results, errors, keys, {k: 0.0 for k in tasks if k not in results and k not in errors})

    def on_tick(running: dict[str, float]) -> None:
        for key, secs in running.items():
            slots[key].info(f"⏳ Running… {secs:.0f} s")
        show_table(table, results, errors, keys, running)

    run_parallel(tasks, on_done, on_tick)
    return results, errors


for turn in st.session_state.history:
    with st.chat_message("user"):
        st.markdown(turn["question"])
    with st.chat_message("assistant"):
        show_turn(turn["results"], turn["errors"], turn["selected"])

question = st.chat_input("Ask about the reports, e.g. How many oranges are in the Fanta image?")
if question:
    keys = [k for k in SOLUTION_KEYS if k in selected]
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        if agentic_sources:
            try:
                apply_max_docs(agentic_sources, infos, max_docs)
            except Exception as e:  # noqa: BLE001
                st.warning(f"Could not change the passage limit: {type(e).__name__}: {e}")
        results, errors = ask_all(question, keys)
    st.session_state.history.append({"question": question, "results": results, "errors": errors, "selected": keys})
    st.rerun()  # redraw from history, so the session totals in the sidebar include this question
