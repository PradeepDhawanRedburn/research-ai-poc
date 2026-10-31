"""Shared pieces of the Streamlit app: page setup, store access, the cost line, pictures, one tile layout."""
from __future__ import annotations

import streamlit as st

from ..config import ARMS, MODES, combo_label, settings
from ..schema import Answer, Passage
from ..storage import MANIFEST, Store, open_store

NAVY = "#1F3A5F"
TEAL = "#2A7F8E"
GREY = "#6B7280"
LIGHT = "#F3F5F8"


def setup(title: str) -> None:
    st.set_page_config(page_title=f"Research reports · {title}", layout="wide", initial_sidebar_state="expanded")
    st.markdown(
        "<style>"
        ".block-container{padding-top:1.6rem;max-width:1500px}"
        "h1{font-weight:600;letter-spacing:-0.01em} h2,h3{font-weight:600}"
        "[data-testid='stMetricValue']{font-size:1.6rem}"
        ".tile-answer{font-size:1.02rem;line-height:1.5}"
        f".muted{{color:{GREY};font-size:0.9rem}}"
        "</style>", unsafe_allow_html=True)


@st.cache_resource(show_spinner=False)
def store() -> Store:
    return open_store()


@st.cache_data(ttl=30, show_spinner=False)
def manifest() -> dict:
    s = store()
    return s.json(MANIFEST, {}) if s.exists(MANIFEST) else {}


@st.cache_data(ttl=300, show_spinner=False)
def picture_bytes(key: str) -> bytes | None:
    try:
        return store().get(key)
    except Exception:  # noqa: BLE001 - asset missing: the panel says so
        return None


def store_note() -> None:
    try:
        st.caption(f"Store: {store().describe()}")
    except Exception as e:  # noqa: BLE001
        st.error(f"The store is not reachable: {type(e).__name__}: {str(e)[:200]}. Check STORAGE_* in .env.")
        st.stop()


# ----------------------------------------------------------------------------- numbers

def fmt_tokens(n: int) -> str:
    return f"{n:,}"


def cost_of(a: Answer) -> float | None:
    from ..evaluate.cost import prices

    return prices().cost_of(a)


def fmt_cost(value: float | None) -> str:
    from ..evaluate.cost import fmt_money

    return fmt_money(value)


def cost_line(a: Answer) -> str:
    parts = [f"**{a.total_s:.1f} s**",
             f"**{fmt_tokens(a.total_tokens)} tokens** ({fmt_tokens(a.input_tokens)} in, {fmt_tokens(a.output_tokens)} out"
             + (f", {fmt_tokens(a.reasoning_tokens)} reasoning" if a.reasoning_tokens else "")
             + (f", {fmt_tokens(a.vision_tokens)} vision" if a.vision_tokens else "") + ")",
             f"**{fmt_cost(cost_of(a))}**",
             f"{len(a.passages)} passages, {len(a.cited)} cited"]
    if a.tool_calls:
        names = {}
        for t in a.tool_calls:
            names[t.get("tool", "tool")] = names.get(t.get("tool", "tool"), 0) + 1
        parts.append(", ".join(f"{n} × {k}" for k, n in names.items()))
    return " · ".join(parts)


# ----------------------------------------------------------------------------- one tile

def show_picture(key: str, width: int = 420, caption: str = "") -> None:
    data = picture_bytes(key)
    if data:
        st.image(data, width=width, caption=caption or None)
    else:
        st.caption(f"picture not in the store: {key}")


def show_passage(p: Passage) -> None:
    tags = " · ".join(t for t, on in (("cited", p.cited), ("picture inspected", p.inspected)) if on)
    st.markdown(f"**[{p.rank}] {p.doc_id} · {p.kind}**" + (f" ({tags})" if tags else "")
                + f" · `{p.source_file}` · `{p.id}`")
    meta = " · ".join(x for x in (p.title, p.date, p.heading) if x)
    if meta:
        st.caption(meta)
    st.text(p.text[:2500])


def show_tile(a: Answer, show_all_text: bool = False) -> None:
    """The same layout for every arm and mode."""
    st.markdown(a.text or "*(empty answer)*")  # the model's own Markdown: bullets, bold, tables
    st.caption(f"{combo_label(a.arm, a.mode)} · {a.store} · model `{a.model}` · {a.rounds} model round"
               f"{'s' if a.rounds != 1 else ''}. Numbers in brackets are the passages below.")
    st.markdown(cost_line(a))
    for e in a.errors:
        st.error(e)

    expected = ARMS[a.arm]
    wrong = [p for p in a.passages if p.source_file and not p.source_file.lower().endswith(
        ".docx" if expected.source == "blob" else (".md", ".docx"))]
    if a.passages:
        files = sorted({p.source_file for p in a.passages if p.source_file})
        if wrong:
            st.error(f"{len(wrong)} passage(s) do not look like {expected.label} passages: "
                     + ", ".join(p.source_file for p in wrong))
        else:
            st.success(f"All {len(a.passages)} passages come from {expected.label}"
                       + (f": {', '.join(files)}" if files else ""))
    else:
        st.info("No passages were retrieved.")

    pics = [(p, k) for p in a.passages for k in p.assets]
    looked = [k for p, k in pics if p.inspected]
    label = (f"Pictures behind the retrieved passages ({len(pics)}), {len(looked)} looked at by a model"
             if expected.has_tools else "Pictures: none available for this arm (Azure keeps only its own description)")
    with st.expander(label, expanded=bool(pics)):
        if not pics:
            st.caption("No picture to show." if expected.has_tools else
                       "The Word arm's chunks are produced inside Azure AI Search; the pictures are described there "
                       "and the originals are not referenced.")
        for p, k in pics:
            st.markdown(f"**[{p.rank}] {p.heading or p.kind}** · " + ("**looked at**" if p.inspected else "not looked at")
                        + (" · cited" if p.cited else ""))
            show_picture(k)

    with st.expander(f"How it got the answer: {len(a.steps)} steps"):
        for i, s in enumerate(a.steps, 1):
            secs = f" · {s.seconds:.1f} s" if s.seconds is not None else ""
            st.markdown(f"**{i}. {s.title}**{secs}")
            if s.detail:
                st.caption(s.detail.replace("\n", "  \n"))
            for it in s.items:
                st.markdown(f"- {it}")
            for k in s.assets:
                show_picture(k, width=320)
        for t in a.tool_calls:
            st.markdown(f"Tool `{t.get('tool')}` · {t.get('seconds', 0):.1f} s")
            st.caption(f"{t.get('args')}")
            st.text(str(t.get("result", ""))[:1200])
        st.caption(f"Retrieval {a.retrieval_s:.1f} s · generation {a.generation_s:.1f} s · tools {a.tools_s:.1f} s "
                   f"· total {a.total_s:.1f} s")

    shown = [p for p in a.passages if p.cited or p.inspected]
    with st.expander(f"Cited passages ({len(shown)})", expanded=True):
        if not a.cited:
            st.warning("The answer does not cite any passage" + ("; the ones shown were inspected by the vision model." if shown else "."))
        for p in shown:
            show_passage(p)

    with st.expander(f"All retrieved passages ({len(a.passages)}), in rank order"):
        st.dataframe([{"#": p.rank, "cited": "yes" if p.cited else "", "picture": "looked at" if p.inspected
                       else ("yes" if p.assets else ""), "chunk": p.id, "file": p.source_file, "kind": p.kind,
                       "section / caption": p.heading} for p in a.passages], hide_index=True, width="stretch")
        if show_all_text:
            for p in a.passages:
                st.markdown(f"**[{p.rank}] {p.id}**" + (" · cited" if p.cited else ""))
                st.text(p.text[:1500])
    if a.raw_text and a.raw_text != a.text:
        with st.expander("Answer exactly as the model wrote it"):
            st.text(a.raw_text)


# ----------------------------------------------------------------------------- comparison table

def comparison_rows(results: dict[str, Answer], errors: dict[str, str], keys: list[str],
                    running: dict[str, float] | None = None) -> list[dict]:
    ok = [results[k] for k in keys if k in results]
    least = min((r.total_tokens for r in ok if r.total_tokens), default=0)
    fastest = min((r.total_s for r in ok if r.total_s), default=0)

    def vs(value: float, base: float) -> str:
        if not base or not value:
            return ""
        return "lowest" if value == base else f"+{(value / base - 1):.0%}"

    rows = []
    for k in keys:
        arm, mode = k.split("/")
        label = combo_label(arm, mode)
        if k in results:
            r = results[k]
            rows.append({"arm · mode": label, "status": "answered" if r.text.strip() else "empty",
                         "total s": round(r.total_s, 1), "retrieval s": round(r.retrieval_s, 1),
                         "generation s": round(r.generation_s, 1), "tools s": round(r.tools_s, 1),
                         "vs fastest": vs(r.total_s, fastest),
                         "tokens in": r.input_tokens, "tokens out": r.output_tokens,
                         "reasoning": r.reasoning_tokens, "vision": r.vision_tokens,
                         "total tokens": r.total_tokens, "vs lowest": vs(r.total_tokens, least),
                         f"cost ({settings.currency})": fmt_cost(cost_of(r)),
                         "passages": len(r.passages), "cited": len(r.cited), "tool calls": len(r.tool_calls),
                         "pictures": len(r.inspected_assets), "error": "; ".join(r.errors)})
        elif running and k in running:
            rows.append({"arm · mode": label, "status": f"running… {running[k]:.0f} s"})
        else:
            rows.append({"arm · mode": label, "status": "failed", "error": errors.get(k, "")})
    return rows


def session_totals(history: list[dict], keys: list[str]) -> list[dict]:
    rows = []
    for k in keys:
        rs = [t["results"][k] for t in history if k in t.get("results", {})]
        failed = sum(1 for t in history if k in t.get("errors", {}))
        if not rs and not failed:
            continue
        n = len(rs)
        costs = [cost_of(r) for r in rs]
        total_cost = sum(c for c in costs if c is not None) if costs and all(c is not None for c in costs) else None
        rows.append({"arm · mode": combo_label(*k.split("/")), "questions": n, "failed": failed,
                     "total tokens": sum(r.total_tokens for r in rs),
                     "tokens per question": round(sum(r.total_tokens for r in rs) / n) if n else 0,
                     "seconds per question": round(sum(r.total_s for r in rs) / n, 1) if n else 0,
                     f"cost ({settings.currency})": fmt_cost(total_cost)})
    return rows


def mode_help() -> str:
    return "\n".join(f"- **{m}**: {d}" for m, d in MODES.items())
