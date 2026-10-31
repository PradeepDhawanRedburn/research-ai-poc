"""Shared pieces of the Streamlit app: the visual system, store access, KPI cards, one tile layout, the comparison.

Written for an executive demo: the answer first, the numbers that matter in one line, everything an engineer wants
one click deeper. Every arm and mode renders through the same functions so the comparison is like for like.
"""
from __future__ import annotations

import streamlit as st

from ..config import ARMS, MODES, settings
from ..schema import Answer, Passage
from ..storage import DESCRIPTIONS, MANIFEST, Store, open_store

# ----------------------------------------------------------------------------- palette

NAVY = "#1F3A5F"
TEAL = "#1E7F86"
SLATE = "#5B6B7F"
GREY = "#6B7280"
LIGHT = "#F4F6F9"
LINE = "#E3E7EE"
INK = "#1B2430"
GREEN = "#2E7D4F"
AMBER = "#B7791F"
RED = "#B3362B"

ARM_COLOUR = {"word": SLATE, "markdown-plus": TEAL}
MODE_LABEL = {"agent": "Agent", "direct": "Direct"}
MODE_BLURB = {"agent": "plans its searches, may use facts and pictures",
              "direct": "one retrieval, one answer"}
VERDICT_COLOUR = {"correct": GREEN, "partial": AMBER, "incorrect": RED}

ENGINE_LABEL = ("LangChain + Chroma" if settings.retrieval_backend == "langchain" else "Azure AI Search + Foundry")

CSS = f"""
<style>
#MainMenu, footer, [data-testid="stDecoration"], [data-testid="stStatusWidget"] {{display:none}}
header[data-testid="stHeader"] {{background:transparent}}
.block-container {{padding-top:1.4rem; padding-bottom:3rem; max-width:1560px}}
h1 {{font-weight:650; letter-spacing:-0.02em; font-size:2.1rem; margin-bottom:0.2rem}}
h2 {{font-weight:620; font-size:1.35rem; margin-top:1.6rem}}
h3 {{font-weight:600; font-size:1.08rem}}
[data-testid="stSidebar"] {{background:{LIGHT}; border-right:1px solid {LINE}}}
[data-testid="stSidebar"] .block-container {{padding-top:1rem}}
.brand {{font-weight:700; font-size:1.05rem; color:{NAVY}; letter-spacing:-0.01em}}
.brand-sub {{color:{GREY}; font-size:0.82rem; margin-top:-2px}}
.lede {{font-size:1.08rem; line-height:1.55; color:{INK}; max-width:62rem}}
.muted {{color:{GREY}; font-size:0.9rem}}
.kpi {{border:1px solid {LINE}; border-radius:12px; padding:14px 16px 12px; background:#fff; height:100%}}
.kpi .l {{color:{GREY}; font-size:0.74rem; text-transform:uppercase; letter-spacing:0.05em; white-space:nowrap; overflow:hidden; text-overflow:ellipsis}}
.kpi .v {{font-size:1.9rem; font-weight:650; color:{INK}; line-height:1.15; margin-top:4px; white-space:nowrap}}
.kpi .v.small {{font-size:1.25rem}}
.kpi .s {{color:{GREY}; font-size:0.85rem; margin-top:2px}}
.kpi.accent {{border-left:4px solid {TEAL}}}
.pill {{display:inline-block; padding:3px 10px; border-radius:999px; font-size:0.78rem; font-weight:600;
        color:#fff; letter-spacing:0.02em; margin-right:6px; vertical-align:middle}}
.pill.ghost {{background:#fff; color:{INK}; border:1px solid {LINE}; font-weight:500}}
.tile-head {{display:flex; align-items:center; gap:4px; margin-bottom:6px}}
.tile-head .t {{font-weight:650; font-size:1.02rem; color:{INK}; margin-right:4px}}
.answer {{font-size:1.04rem; line-height:1.55}}
.answer p {{margin-bottom:0.5rem}}
.strip {{display:flex; flex-wrap:wrap; gap:6px 18px; color:{SLATE}; font-size:0.86rem; padding:8px 0 2px;
         border-top:1px solid {LINE}; margin-top:8px}}
.strip b {{color:{INK}; font-weight:650}}
.src {{border-left:3px solid {LINE}; padding:4px 10px; margin:6px 0 10px}}
.src .h {{font-weight:600; font-size:0.9rem; color:{INK}}}
.src .m {{color:{GREY}; font-size:0.8rem}}
.src .x {{font-size:0.88rem; color:{INK}; white-space:pre-wrap; margin-top:4px}}
.verdict {{display:inline-block; padding:2px 9px; border-radius:6px; color:#fff; font-size:0.78rem; font-weight:600}}
div.stButton > button {{height:auto; min-height:2.6rem; text-align:left; justify-content:flex-start; line-height:1.3; padding:8px 12px}}
div.stButton > button div, div.stButton > button p {{white-space:normal !important; overflow:visible !important; text-overflow:clip; text-align:left; font-size:0.88rem}}
[data-testid="stMetricValue"] {{font-size:1.5rem}}
div[data-testid="stExpander"] details summary p {{font-size:0.92rem}}
</style>
"""


def setup(title: str) -> None:
    st.set_page_config(page_title=f"Research Q&A · {title}", page_icon=":material/analytics:", layout="wide",
                       initial_sidebar_state="expanded")
    st.markdown(CSS, unsafe_allow_html=True)
    with st.sidebar:
        st.markdown('<div class="brand">Research reports Q&A</div>'
                    f'<div class="brand-sub">Word reports, asked in plain language · {ENGINE_LABEL}</div>',
                    unsafe_allow_html=True)
        st.markdown("")


# ----------------------------------------------------------------------------- store access

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


@st.cache_data(ttl=300, show_spinner=False)
def description_of(asset_key: str) -> str:
    """The cached vision description of a picture, or an empty string."""
    from ..ingest.describe import cache_key

    try:
        k = cache_key(asset_key)
        return (store().json(k) or {}).get("description", "") if store().exists(k) else ""
    except Exception:  # noqa: BLE001
        return ""


@st.cache_data(show_spinner=False)
def golden_questions() -> list[dict]:
    from ..evaluate.golden import load_questions

    try:
        return load_questions()
    except Exception:  # noqa: BLE001
        return []


def require_store() -> Store:
    """The store, or a clear stop with what to fix."""
    try:
        s = store()
        s.describe()
        return s
    except Exception as e:  # noqa: BLE001
        st.error(f"The store is not reachable: {type(e).__name__}: {str(e)[:200]}. Check STORAGE_* in .env.")
        st.stop()


def sidebar_footer() -> None:
    with st.sidebar:
        st.markdown("")
        try:
            d = store().describe()
            d = d if len(d) <= 56 else "…" + d[-52:]
            st.caption(f"Store · {d}")
        except Exception:  # noqa: BLE001
            st.caption("Store · not reachable")
        st.caption(f"Engine · {ENGINE_LABEL} · model {settings.answer_model}")
        st.caption("Acting as · " + (", ".join(settings.principals) if settings.principals else "anonymous (public reports only)"))


# ----------------------------------------------------------------------------- formatting

def fmt_tokens(n: int | float) -> str:
    return f"{n:,.0f}"


def fmt_secs(s: float) -> str:
    return f"{s:.1f} s"


def cost_of(a: Answer) -> float | None:
    from ..evaluate.cost import prices

    return prices().cost_of(a)


def fmt_cost(value: float | None) -> str:
    from ..evaluate.cost import fmt_money

    return fmt_money(value)


def pct(x: float | None) -> str:
    return "–" if x is None else f"{x:.0%}"


def arm_pill(arm: str) -> str:
    return f'<span class="pill" style="background:{ARM_COLOUR.get(arm, GREY)}">{ARMS[arm].short}</span>'


def mode_pill(mode: str) -> str:
    return f'<span class="pill ghost">{MODE_LABEL.get(mode, mode)}</span>'


def combo_title(arm: str, mode: str) -> str:
    return f"{ARMS[arm].short} · {MODE_LABEL.get(mode, mode)}"


def tile_header(arm: str, mode: str, extra: str = "") -> None:
    st.markdown(f'<div class="tile-head">{arm_pill(arm)}{mode_pill(mode)}'
                + (f'<span class="muted">{extra}</span>' if extra else "") + "</div>", unsafe_allow_html=True)


def kpi(label: str, value: str, sub: str = "", accent: bool = False) -> None:
    small = " small" if len(value) > 7 else ""
    st.markdown(f'<div class="kpi{" accent" if accent else ""}"><div class="l">{label}</div>'
                f'<div class="v{small}">{value}</div><div class="s">{sub}</div></div>', unsafe_allow_html=True)


def kpi_row(items: list[tuple[str, str, str]], accent_first: bool = False) -> None:
    for i, (col, (label, value, sub)) in enumerate(zip(st.columns(len(items)), items)):
        with col:
            kpi(label, value, sub, accent=accent_first and i == 0)


def metric_line(items: list[tuple[str, str]]) -> None:
    """Small label/value pairs in one wrapped line, for cards where KPI boxes would not fit."""
    st.markdown('<div class="strip">' + "".join(f"<span>{l} <b>{v}</b></span>" for l, v in items) + "</div>",
                unsafe_allow_html=True)


def report_name(doc_id: str, m: dict) -> str:
    """What to call a report: the subtitle when it is the real title (Money Map editions file under a sector)."""
    return m.get("subtitle") or m.get("title") or doc_id


def person_name(s: str) -> str:
    """'Melissa Davies melissa.davies@…' -> 'Melissa Davies': the manifest keeps the e-mail, the page does not."""
    return " ".join(t for t in str(s).split() if "@" not in t).strip(" ,;") or str(s)


def verdict_badge(v: str) -> str:
    return f'<span class="verdict" style="background:{VERDICT_COLOUR.get(v, GREY)}">{v}</span>'


# ----------------------------------------------------------------------------- one tile

def show_picture(key: str, width: int | str = "stretch", caption: str = "") -> None:
    """A picture from the store, fitted to its column by default (tiles and galleries are narrow)."""
    data = picture_bytes(key)
    if data:
        st.image(data, width=width, caption=caption or None)
    else:
        st.caption(f"picture not in the store: {key}")


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def show_passage(p: Passage, chars: int = 900) -> None:
    tags = [t for t, on in (("cited", p.cited), ("picture inspected", p.inspected)) if on]
    meta = " · ".join(x for x in (p.source_file, p.title, p.date, p.heading, p.kind) if x)
    text = p.text if len(p.text) <= chars else p.text[:chars].rstrip() + " …"
    st.markdown(f'<div class="src"><div class="h">[{p.rank}] {_esc(p.heading or p.kind or p.id)}'
                + (f' <span class="muted">· {", ".join(tags)}</span>' if tags else "")
                + f'</div><div class="m">{_esc(meta)}</div><div class="x">{_esc(text)}</div></div>',
                unsafe_allow_html=True)


def strip(a: Answer) -> str:
    """One line of the numbers that matter, the same for every arm and mode."""
    tools = {}
    for t in a.tool_calls:
        tools[t.get("tool", "tool")] = tools.get(t.get("tool", "tool"), 0) + 1
    parts = [f"<span><b>{fmt_secs(a.total_s)}</b></span>",
             f"<span><b>{fmt_tokens(a.total_tokens)}</b> tokens</span>",
             f"<span><b>{fmt_cost(cost_of(a))}</b></span>",
             f"<span><b>{len(a.cited)}</b> of {len(a.passages)} passages cited</span>"]
    if tools.get("lookup_facts"):
        parts.append(f"<span><b>{tools['lookup_facts']}</b> exact-value lookup{'s' if tools['lookup_facts'] > 1 else ''}</span>")
    if a.inspected_assets or tools.get("inspect_figure"):
        n = len(a.inspected_assets) or tools.get("inspect_figure", 0)
        parts.append(f"<span><b>{n}</b> picture{'s' if n > 1 else ''} inspected</span>")
    return '<div class="strip">' + "".join(parts) + "</div>"


def show_tile(a: Answer, show_all_text: bool = False, compact: bool = False) -> None:
    """The same layout for every arm and mode: answer, numbers, pictures it looked at, then the detail."""
    st.markdown(a.text or "*No answer was produced.*")
    st.markdown(strip(a), unsafe_allow_html=True)
    for e in a.errors:
        st.error(e)
    if compact:
        return

    expected = ARMS[a.arm]
    wrong = [p for p in a.passages if p.source_file and not p.source_file.lower().endswith(
        ".docx" if expected.source == "blob" else (".md", ".docx"))]
    if wrong:
        st.warning(f"{len(wrong)} passage(s) do not look like {expected.label} passages: "
                   + ", ".join(sorted({p.source_file for p in wrong})))

    looked = [(p, k) for p in a.passages if p.inspected for k in p.assets]
    if looked and not compact:
        cols = st.columns(min(len(looked), 2))
        for col, (p, k) in zip(cols * ((len(looked) + 1) // 2), looked[:4]):
            with col:
                show_picture(k, caption=f"Looked at: {p.heading or p.kind}")

    pics = [(p, k) for p in a.passages for k in p.assets if not p.inspected]
    cited = a.cited
    with st.expander(f"Sources · {len(cited)} cited, {len(a.passages)} retrieved"
                     + (f", {len(pics) + len(looked)} with pictures" if (pics or looked) else "")):
        if not a.passages:
            st.caption("No passages were retrieved.")
        elif not cited:
            st.caption("The answer does not cite a passage. Retrieved passages, in rank order:")
        for p in (cited or a.passages[:5]):
            show_passage(p)
        if pics:
            st.markdown("**Pictures behind the retrieved passages**")
            cols = st.columns(3)
            for i, (p, k) in enumerate(pics[:9]):
                with cols[i % 3]:
                    show_picture(k, caption=f"[{p.rank}] {p.heading or p.kind}")
        if show_all_text and cited:
            st.markdown("**Every retrieved passage**")
            for p in a.passages:
                if not p.cited:
                    show_passage(p, chars=600)
        elif cited and len(a.passages) > len(cited):
            st.dataframe([{"#": p.rank, "cited": "yes" if p.cited else "", "file": p.source_file, "kind": p.kind,
                           "section / caption": p.heading} for p in a.passages], hide_index=True, width="stretch",
                         height=min(38 + 35 * len(a.passages), 320))

    with st.expander(f"How it got there · {len(a.steps)} steps, {a.rounds} model round{'s' if a.rounds != 1 else ''}"):
        st.caption(f"{combo_title(a.arm, a.mode)} · {a.store or ENGINE_LABEL} · model {a.model or settings.answer_model}"
                   f" · retrieval {a.retrieval_s:.1f} s, generation {a.generation_s:.1f} s, tools {a.tools_s:.1f} s")
        for i, s in enumerate(a.steps, 1):
            secs = f" · {s.seconds:.1f} s" if s.seconds is not None else ""
            st.markdown(f"**{i}. {s.title}**{secs}")
            if s.detail:
                st.caption(s.detail.replace("\n", "  \n"))
            for it in s.items:
                st.markdown(f"- {it}")
            for k in s.assets:
                show_picture(k)
        if a.tool_calls:
            st.markdown("**Tool calls**")
            for t in a.tool_calls:
                st.markdown(f"`{t.get('tool')}` · {t.get('seconds', 0):.1f} s · {t.get('args')}")
                st.text(str(t.get("result", ""))[:1000])
        st.caption(f"Tokens: {fmt_tokens(a.input_tokens)} in, {fmt_tokens(a.output_tokens)} out"
                   + (f", {fmt_tokens(a.reasoning_tokens)} reasoning" if a.reasoning_tokens else "")
                   + (f", {fmt_tokens(a.vision_tokens)} vision" if a.vision_tokens else ""))
        if a.raw_text and a.raw_text != a.text:
            st.markdown("**Answer exactly as the model wrote it**")
            st.text(a.raw_text)


def running_tile(slot, arm: str, mode: str, secs: float | None = None) -> None:
    with slot.container():
        st.status(f"Working… {secs:.0f} s" if secs is not None else "Working…", state="running", expanded=False)
        st.caption(f"{MODE_LABEL[mode]} · {MODE_BLURB[mode]}")


# ----------------------------------------------------------------------------- comparison

def comparison_rows(results: dict[str, Answer], errors: dict[str, str], keys: list[str],
                    running: dict[str, float] | None = None) -> list[dict]:
    """Flat rows (the CSV export and the fallback table)."""
    rows = []
    for k in keys:
        arm, mode = k.split("/")
        label = combo_title(arm, mode)
        if k in results:
            r = results[k]
            rows.append({"arm · mode": label, "status": "answered" if r.text.strip() else "empty",
                         "seconds": round(r.total_s, 1), "retrieval s": round(r.retrieval_s, 1),
                         "generation s": round(r.generation_s, 1), "tools s": round(r.tools_s, 1),
                         "tokens": r.total_tokens, "tokens in": r.input_tokens, "tokens out": r.output_tokens,
                         "reasoning": r.reasoning_tokens, "vision": r.vision_tokens,
                         f"cost ({settings.currency})": fmt_cost(cost_of(r)),
                         "passages": len(r.passages), "cited": len(r.cited), "tool calls": len(r.tool_calls),
                         "pictures": len(r.inspected_assets), "error": "; ".join(r.errors)})
        elif running and k in running:
            rows.append({"arm · mode": label, "status": f"running… {running[k]:.0f} s"})
        else:
            rows.append({"arm · mode": label, "status": "failed", "error": errors.get(k, "")})
    return rows


def comparison(results: dict[str, Answer], errors: dict[str, str], keys: list[str],
               running: dict[str, float] | None = None) -> None:
    """Side by side: three small bar charts (seconds, tokens, cost) with the best marked, and a compact table."""
    import altair as alt
    import pandas as pd

    done = [k for k in keys if k in results]
    if not done:
        waiting = [combo_title(*k.split("/")) for k in keys if running and k in running]
        st.caption("Waiting for " + ", ".join(waiting) if waiting else "Nothing to compare yet.")
        return
    recs = []
    priced = all(cost_of(results[k]) is not None for k in done)
    for k in done:
        r = results[k]
        arm, mode = k.split("/")
        recs.append({"combo": combo_title(arm, mode), "arm": arm, "metric": "Seconds", "value": round(r.total_s, 1)})
        recs.append({"combo": combo_title(arm, mode), "arm": arm, "metric": "Tokens", "value": r.total_tokens})
        if priced:
            recs.append({"combo": combo_title(arm, mode), "arm": arm, "metric": f"Cost ({settings.currency})",
                         "value": round(cost_of(r), 5)})
    df = pd.DataFrame(recs)
    best = df.groupby("metric")["value"].transform("min")
    df["best"] = (df["value"] == best) & (df["value"] > 0)
    order = [combo_title(*k.split("/")) for k in keys]
    metrics = ["Seconds", "Tokens"] + ([f"Cost ({settings.currency})"] if priced else [])
    charts = []
    for m in metrics:
        d = df[df["metric"] == m]
        base = alt.Chart(d).encode(
            y=alt.Y("combo:N", sort=order, title=None, axis=alt.Axis(labelLimit=220, labelFontSize=12)),
            x=alt.X("value:Q", title=None, axis=alt.Axis(labels=False, ticks=False, grid=False, domain=False)),
            tooltip=[alt.Tooltip("combo:N", title="arm · mode"), alt.Tooltip("value:Q", title=m, format=",")])
        bars = base.mark_bar(cornerRadiusEnd=3, height=18).encode(
            color=alt.condition(alt.datum.best, alt.value(TEAL), alt.value("#C9D1DC")))
        text = base.mark_text(align="left", dx=4, fontSize=12, color=INK).encode(
            text=alt.Text("value:Q", format=",.1f" if m == "Seconds" else (",.4f" if m.startswith("Cost") else ",")))
        charts.append((bars + text).properties(title=alt.TitleParams(m, anchor="start", fontSize=13, color=SLATE),
                                               height=26 * len(order) + 10))
    cols = st.columns(len(charts) + (0 if priced else 1))
    for col, ch in zip(cols, charts):
        with col:
            st.altair_chart(ch.configure_view(strokeWidth=0), width="stretch")
    if not priced:
        with cols[-1]:
            kpi("Cost", "not priced", "fill prices.yaml to see cost per question")
    with st.expander("All the numbers"):
        st.dataframe(comparison_rows(results, errors, keys, running), hide_index=True, width="stretch")
    pending = [combo_title(*k.split("/")) for k in keys if k not in results and not (running and k in running)
               and k in errors]
    if pending:
        st.caption("Failed: " + ", ".join(pending))


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
        rows.append({"arm · mode": combo_title(*k.split("/")), "questions": n, "failed": failed,
                     "tokens per question": round(sum(r.total_tokens for r in rs) / n) if n else 0,
                     "seconds per question": round(sum(r.total_s for r in rs) / n, 1) if n else 0,
                     f"cost ({settings.currency})": fmt_cost(total_cost)})
    return rows


def mode_help() -> str:
    return "\n".join(f"- **{MODE_LABEL[m]}**: {d}" for m, d in MODES.items())


def arm_help() -> str:
    return "\n".join(f"- **{a.label}**: {a.description}" for a in ARMS.values())
