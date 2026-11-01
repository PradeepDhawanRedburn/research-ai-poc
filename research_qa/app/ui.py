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
MODE_LABEL = {"agentic": "Agentic", "single-pass": "Single-pass"}
MODE_BLURB = {"agentic": "plans its searches, may use facts and pictures",
              "single-pass": "one retrieval, one answer"}
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
.gtag {{margin-left:auto; display:inline-flex; align-items:center; gap:5px; padding:3px 11px; border-radius:999px;
        color:#fff; font-weight:650; font-size:0.82rem; white-space:nowrap}}
.gtag .m {{font-size:1rem; line-height:1}}
.cites {{display:flex; flex-wrap:wrap; gap:6px; margin:4px 0 2px}}
.wins {{display:flex; flex-wrap:wrap; gap:4px 22px; font-size:0.9rem; margin:0 0 4px}}
.wins b {{color:{TEAL}}}
.cite {{display:inline-block; max-width:100%; padding:3px 10px; border-radius:6px; background:{LIGHT}; border:1px solid {LINE};
        color:{NAVY}; font-size:0.8rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap}}
.cite b {{color:{TEAL}}}
.board {{display:grid; align-items:center; border:1px solid {LINE}; border-radius:12px; overflow:hidden; margin-top:6px}}
.board > div {{padding:10px 14px; border-bottom:1px solid {LINE}}}
.board .hd {{background:{LIGHT}; color:{GREY}; font-size:0.74rem; text-transform:uppercase; letter-spacing:0.05em}}
.board .nm {{display:flex; align-items:center; flex-wrap:wrap; gap:4px}}
.board .award {{padding:1px 8px; border-radius:6px; background:{TEAL}1A; color:{TEAL}; font-size:0.74rem; font-weight:650}}
.board .bar {{position:relative; height:26px; background:{LIGHT}; border-radius:6px}}
.board .bar span {{position:absolute; top:0; bottom:0; left:0; border-radius:6px}}
.board .bar em {{position:absolute; left:10px; top:50%; transform:translateY(-50%); font-style:normal; font-size:0.85rem; font-weight:600; color:{INK}}}
.board .na {{color:{GREY}}}
.gtag.sm {{margin-left:0; font-size:0.78rem; padding:2px 9px}}
button[data-baseweb="tab"] p {{font-size:0.9rem}}
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


def pct(x: float | None) -> str:
    return "–" if x is None else f"{x:.0%}"


def arm_pill(arm: str) -> str:
    return f'<span class="pill" style="background:{ARM_COLOUR.get(arm, GREY)}">{ARMS[arm].short}</span>'


def mode_pill(mode: str) -> str:
    return f'<span class="pill ghost">{MODE_LABEL.get(mode, mode)}</span>'


def combo_title(arm: str, mode: str) -> str:
    return f"{ARMS[arm].short} · {MODE_LABEL.get(mode, mode)}"


def tile_header(arm: str, mode: str, extra: str = "", grade: dict | None = None) -> None:
    """Arm and mode pills, with the verdict tag at the right end of the same row."""
    st.markdown(f'<div class="tile-head">{arm_pill(arm)}{mode_pill(mode)}'
                + (f'<span class="muted">{extra}</span>' if extra else "") + f'{grade_tag(grade)}</div>',
                unsafe_allow_html=True)


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


VERDICT_MARK = {"correct": ("✓", "Correct"), "partial": ("◐", "Partially correct"), "incorrect": ("✗", "Incorrect"),
                "unknown": ("?", "Not graded")}


def grade_tag(g: dict | None) -> str:
    """A tick, a half mark or a cross with the verdict in words, as a tag."""
    if not g:
        return ""
    mark, word = VERDICT_MARK.get(g.get("verdict"), VERDICT_MARK["unknown"])
    colour = VERDICT_COLOUR.get(g.get("verdict"), GREY)
    return f'<span class="gtag" style="background:{colour}"><span class="m">{mark}</span>{word}</span>'


@st.cache_resource(show_spinner=False)
def _judge():
    from ..evaluate.judge import default_judge

    return default_judge()


def judge_fn():
    """The judge, built on the main thread (it is cached); None when it cannot be built."""
    try:
        return _judge()
    except Exception:  # noqa: BLE001 - no judge deployment or credentials: tiles simply show no verdict
        return None


def grade(judge, question: str, expected: str, qtype: str, answer: Answer) -> dict:
    """Judge one answer against the expected one with the same judge and scale as the evaluation runs."""
    if not answer.text.strip():
        return {"verdict": "incorrect", "reason": "no answer was produced"}
    try:
        return judge({"type": qtype, "question": question, "answer": expected}, answer.text)
    except Exception as e:  # noqa: BLE001
        return {"verdict": "unknown", "reason": f"judge failed: {type(e).__name__}: {str(e)[:120]}"}


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
             f"<span><b>{len(a.cited)}</b> of {len(a.passages)} passages cited</span>"]
    if tools.get("lookup_facts"):
        parts.append(f"<span><b>{tools['lookup_facts']}</b> exact-value lookup{'s' if tools['lookup_facts'] > 1 else ''}</span>")
    if a.inspected_assets or tools.get("inspect_figure"):
        n = len(a.inspected_assets) or tools.get("inspect_figure", 0)
        parts.append(f"<span><b>{n}</b> picture{'s' if n > 1 else ''} inspected</span>")
    return '<div class="strip">' + "".join(parts) + "</div>"


def show_tile(a: Answer, show_all_text: bool = False, compact: bool = False, grade: dict | None = None) -> None:
    """The same layout for every arm and mode: verdict, answer, numbers, pictures it looked at, then the detail."""
    tile_header(a.arm, a.mode, grade=grade)
    if grade and grade.get("reason"):
        st.caption(f"Judge: {grade['reason']}")
    cited = a.cited
    chips = "".join(f'<span class="cite"><b>[{p.rank}]</b> {_esc(p.heading or p.kind or p.source_file or p.id)}</span>'
                    for p in cited[:6])

    def answer_block() -> None:
        st.markdown(a.text or "*No answer was produced.*")
        if chips:
            st.markdown(f'<div class="cites">{chips}</div>', unsafe_allow_html=True)
        st.markdown(strip(a), unsafe_allow_html=True)
        for e in a.errors:
            st.error(e)

    if compact:
        answer_block()
        return

    expected = ARMS[a.arm]
    wrong = [p for p in a.passages if p.source_file and not p.source_file.lower().endswith(
        ".docx" if expected.source == "blob" else (".md", ".docx"))]
    looked = [(p, k) for p in a.passages if p.inspected for k in p.assets]
    pics = [(p, k) for p in a.passages for k in p.assets if not p.inspected]
    t_ans, t_src, t_how = st.tabs(["Answer", f"Sources · {len(cited)}/{len(a.passages)}",
                                   f"Thought process · {len(a.steps)}"])
    with t_ans:
        answer_block()
        if wrong:
            st.warning(f"{len(wrong)} passage(s) do not look like {expected.label} passages: "
                       + ", ".join(sorted({p.source_file for p in wrong})))
        if looked:
            cols = st.columns(min(len(looked), 2))
            for col, (p, k) in zip(cols * ((len(looked) + 1) // 2), looked[:4]):
                with col:
                    show_picture(k, caption=f"Looked at: {p.heading or p.kind}")
    with t_src:
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

    with t_how:
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


def tile_error(slot, arm: str, mode: str, message: str) -> None:
    with slot.container():
        tile_header(arm, mode)
        st.error(message)


def running_tile(slot, arm: str, mode: str, secs: float | None = None) -> None:
    with slot.container():
        tile_header(arm, mode)
        st.status(f"Working… {secs:.0f} s" if secs is not None else "Working…", state="running", expanded=False)
        st.caption(f"{MODE_LABEL[mode]} · {MODE_BLURB[mode]}")


# ----------------------------------------------------------------------------- comparison

def comparison_rows(results: dict[str, Answer], errors: dict[str, str], keys: list[str],
                    running: dict[str, float] | None = None, grades: dict[str, dict] | None = None) -> list[dict]:
    """Flat rows (the CSV export and the fallback table)."""
    rows = []
    for k in keys:
        arm, mode = k.split("/")
        label = combo_title(arm, mode)
        if k in results:
            r = results[k]
            verdict = {"verdict": VERDICT_MARK[grades[k]["verdict"]][1]} if grades and k in grades and grades[k].get("verdict") in VERDICT_MARK else {}
            rows.append({"arm · mode": label, **verdict, "status": "answered" if r.text.strip() else "empty",
                         "seconds": round(r.total_s, 1), "retrieval s": round(r.retrieval_s, 1),
                         "generation s": round(r.generation_s, 1), "tools s": round(r.tools_s, 1),
                         "tokens": r.total_tokens, "tokens in": r.input_tokens, "tokens out": r.output_tokens,
                         "reasoning": r.reasoning_tokens, "vision": r.vision_tokens,
                         "passages": len(r.passages), "cited": len(r.cited), "tool calls": len(r.tool_calls),
                         "pictures": len(r.inspected_assets), "error": "; ".join(r.errors)})
        elif running and k in running:
            rows.append({"arm · mode": label, "status": f"running… {running[k]:.0f} s"})
        else:
            rows.append({"arm · mode": label, "status": "failed", "error": errors.get(k, "")})
    return rows


SCORE = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}


def winners(results: dict[str, Answer], grades: dict[str, dict] | None, keys: list[str]) -> dict[str, tuple[str, str]]:
    """Overall, balanced (accuracy against tokens) and cheapest (tokens) winners among the answered tiles.

    Overall: 60% accuracy, 20% speed, 20% tokens (50/50 speed and tokens when nothing is graded), each as best/value.
    Balanced: accuracy divided by the square root of tokens relative to the leanest answer. Cheapest: fewest tokens,
    skipping wrong answers when the tiles are graded. Returns category -> (key, one-line reason).
    """
    score = {k: SCORE[grades[k]["verdict"]] for k in keys if grades and k in grades and grades[k].get("verdict") in SCORE}
    done = [k for k in keys if k in results and results[k].text.strip() and results[k].total_tokens > 0]
    out: dict[str, tuple[str, str]] = {}
    if not done:
        return out
    graded = bool(score) and all(k in score for k in done)
    min_s = min(max(results[k].total_s, 0.1) for k in done)
    min_t = min(results[k].total_tokens for k in done)

    def speed(k): return min_s / max(results[k].total_s, 0.1)
    def lean(k): return min_t / results[k].total_tokens
    def note(k): return f"{results[k].total_s:.1f} s · {results[k].total_tokens:,} tokens"

    pool = [k for k in done if not graded or score[k] > 0] or done
    cheapest = max(pool, key=lean)
    out["Cheapest"] = (cheapest, note(cheapest))
    if graded:
        overall = max(done, key=lambda k: 0.6 * score[k] + 0.2 * speed(k) + 0.2 * lean(k))
        balanced = max(done, key=lambda k: score[k] * lean(k) ** 0.5)
        out["Overall"] = (overall, note(overall))
        out["Balanced"] = (balanced, note(balanced))
    else:
        overall = max(done, key=lambda k: 0.5 * speed(k) + 0.5 * lean(k))
        out["Overall"] = (overall, note(overall))
    return {c: out[c] for c in ("Overall", "Balanced", "Cheapest") if c in out}


def comparison(results: dict[str, Answer], errors: dict[str, str], keys: list[str],
               running: dict[str, float] | None = None, grades: dict[str, dict] | None = None) -> None:
    """Side by side: three winners, then bar charts (accuracy when graded, seconds, tokens) with the best marked."""
    done = [k for k in keys if k in results]
    if not done:
        waiting = [combo_title(*k.split("/")) for k in keys if running and k in running]
        st.caption("Waiting for " + ", ".join(waiting) if waiting else "Nothing to compare yet.")
        return
    won = winners(results, grades, keys)
    badges: dict[str, list[str]] = {}
    for cat, (k, _) in (won if not running else {}).items():
        badges.setdefault(k, []).append(cat)
    secs = {k: results[k].total_s for k in done}
    toks = {k: results[k].total_tokens for k in done}
    scores = {k: (grades or {}).get(k, {}).get("verdict") for k in done}
    graded = any(v in SCORE for v in scores.values())

    def bar(v: float, top: float, best: bool, label: str) -> str:
        w = max(4, round(100 * v / top)) if top else 4
        return (f'<div class="bar"><span style="width:{w}%;background:{TEAL if best else "#C9D1DC"}"></span>'
                f'<em>{label}</em></div>')

    rows = []
    for k in keys:
        arm, mode = k.split("/")
        tags = "".join(f'<span class="award">{c}</span>' for c in badges.get(k, []))
        name = f'<div class="nm">{arm_pill(arm)}{mode_pill(mode)}{tags}</div>'
        if k not in results:
            state = f"running… {running[k]:.0f} s" if running and k in running else "failed"
            rows.append(f'{name}<div class="na" style="grid-column:2 / -1">{state}</div>')
            continue
        v = scores[k]
        acc = grade_tag({"verdict": v}).replace('class="gtag"', 'class="gtag sm"') if v in SCORE else ""
        cells = [f'<div>{acc}</div>'] if graded else []
        cells.append(bar(secs[k], max(secs.values()), secs[k] == min(secs.values()), f"{secs[k]:.1f} s"))
        cells.append(bar(toks[k], max(toks.values()), toks[k] == min(toks.values()), f"{toks[k]:,}"))
        rows.append(name + "".join(cells))
    head = "".join(f'<div class="hd">{h}</div>' for h in
                   (["Route"] + (["Accuracy"] if graded else []) + ["Time (lower is better)", "Tokens (lower is better)"]))
    cols = "minmax(260px,1.3fr) " + ("150px " if graded else "") + "1fr 1fr"
    st.markdown(f'<div class="board" style="grid-template-columns:{cols}">{head}{"".join(rows)}</div>',
                unsafe_allow_html=True)


def session_totals(history: list[dict], keys: list[str]) -> list[dict]:
    rows = []
    for k in keys:
        rs = [t["results"][k] for t in history if k in t.get("results", {})]
        failed = sum(1 for t in history if k in t.get("errors", {}))
        if not rs and not failed:
            continue
        n = len(rs)
        gs = [t["grades"][k]["verdict"] for t in history if k in t.get("grades", {})
              and t["grades"][k].get("verdict") in ("correct", "partial", "incorrect")]
        rows.append({"arm · mode": combo_title(*k.split("/")), "questions": n, "failed": failed,
                     **({"correct": f"{gs.count('correct')} of {len(gs)}"} if gs else {}),
                     "tokens per question": round(sum(r.total_tokens for r in rs) / n) if n else 0,
                     "seconds per question": round(sum(r.total_s for r in rs) / n, 1) if n else 0})
    return rows


ENGINE_NAME = {"azure": "Azure", "langchain": "LangChain"}


@st.cache_data(ttl=60, show_spinner=False)
def run_catalog() -> list[dict]:
    """Every stored run with its engine, size and completeness.

    kind is "full" for a run over the whole golden set and "smoke" for a subset; complete means every
    question was answered for every arm and mode the run asked for. Runs without a recorded engine predate it.
    """
    from ..evaluate.run import list_runs, load_run

    s = store()
    total = len(golden_questions())
    out = []
    for name in list_runs(s):
        try:
            _summary, rows, cfg = load_run(name, s)
        except Exception:  # noqa: BLE001 - an unreadable run is simply not offered
            continue
        retr = cfg.get("retrieval") or {}
        want = int(cfg.get("questions") or 0) * len(cfg.get("arms") or []) * len(cfg.get("modes") or [])
        have = len({(r.get("id"), r.get("combo")) for r in rows})
        arms = sorted(cfg.get("arms") or {r["arm"] for r in rows})
        out.append({"name": name, "backend": retr.get("backend"), "questions": cfg.get("questions") or 0,
                    "kind": "full" if total and (cfg.get("questions") or 0) >= total else "smoke",
                    "complete": bool(want) and have >= want, "arms": arms, "started": str(cfg.get("started", ""))})
    return out


def current_runs(catalog: list[dict] | None = None) -> list[dict]:
    """The runs worth looking at: complete, engine recorded, and the newest of each engine and kind
    (one Azure and one LangChain smoke run, one of each full run). Older ones stay in the store."""
    seen, keep = set(), []
    for r in sorted(catalog if catalog is not None else run_catalog(), key=lambda r: r["started"], reverse=True):
        key = (r["backend"], r["kind"])
        if not r["backend"] or not r["complete"] or key in seen:
            continue
        seen.add(key)
        keep.append(r)
    return keep


def run_title(r: dict) -> str:
    return (f"{r['name']} · {ENGINE_NAME.get(r['backend'], 'engine not recorded')} · "
            f"{'full' if r['kind'] == 'full' else 'smoke'}, {r['questions']} questions")


def mode_help() -> str:
    return "\n".join(f"- **{MODE_LABEL[m]}**: {d}" for m, d in MODES.items())


def arm_help() -> str:
    return "\n".join(f"- **{a.label}**: {a.description}" for a in ARMS.values())
