"""What the four-way page (app.py) needs that is not Streamlit: one result shape for both approaches, a parallel
runner, and the side-by-side numbers.

Agentic answers (agentic.foundry.Answer) and RAG answers (rag.pipeline.Answer) carry different objects. Both are
normalised here into `Result`, so the page renders every tile with the same code and the comparison table puts the
same measures next to each other:

- time: retrieval, generation, vision and total seconds;
- tokens: input, output (of which reasoning), vision, total. RAG sends its pictures inside the one answer call, so
  they are in its input tokens; agentic looks at pictures in separate vision calls, counted in `vision_tokens`.
  `total_tokens` is comparable across the two.
- grounding: passages retrieved, passages cited, pictures looked at.

No Streamlit import here, so it can be tested without the UI.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Callable

APPROACHES = {"agentic": "Agentic", "rag": "RAG"}
FORMATS = {"docx": "Word (docx)", "markdown": "MarkItDown (A+)"}
SOURCES = list(FORMATS)
# Word and Markdown of one approach sit next to each other, so the format comparison is one glance
SOLUTION_KEYS = [f"{a}-{s}" for a in APPROACHES for s in SOURCES]
LABELS = {f"{a}-{s}": f"{APPROACHES[a]} · {FORMATS[s]}" for a in APPROACHES for s in SOURCES}
FILE_KIND = {"docx": "Word file", "markdown": "MarkItDown output (A+)"}
FILE_PLURAL = {"docx": "Word files", "markdown": "MarkItDown outputs (A+)"}

CITE_RE = re.compile(r"\[(\d+)\]")


@dataclass
class Passage:
    rank: int
    id: str
    file: str
    doc_id: str
    kind: str
    heading: str            # caption or section
    meta: str               # title · date
    text: str
    source: str             # docx or markdown as recorded on the chunk (to check nothing leaked across solutions)
    cited: bool = False
    inspected: bool = False
    images: list[str] = field(default_factory=list)


@dataclass
class Step:
    kind: str               # plan | retrieve | reason | look | answer
    title: str
    detail: str = ""
    items: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    seconds: float | None = None


@dataclass
class Result:
    key: str                # e.g. agentic-docx
    approach: str           # agentic | rag
    source: str             # docx | markdown
    text: str
    raw_text: str
    model: str
    store: str              # index or collection the passages came from
    passages: list[Passage]
    steps: list[Step]
    queries: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)      # pictures a model looked at
    errors: list[str] = field(default_factory=list)
    retrieval_s: float = 0.0
    generation_s: float = 0.0
    vision_s: float = 0.0
    total_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    vision_tokens: int = 0
    rounds: int = 1

    @property
    def label(self) -> str:
        return LABELS[self.key]

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.vision_tokens

    @property
    def cited(self) -> list[Passage]:
        return [p for p in self.passages if p.cited]

    @property
    def files(self) -> list[str]:
        return sorted({p.file for p in self.passages})

    @property
    def leaked(self) -> list[Passage]:
        """Passages recorded as coming from the other format: must be empty for a fair comparison."""
        return [p for p in self.passages if p.source and p.source != self.source]


def cited_numbers(text: str) -> set[int]:
    return {int(n) for n in CITE_RE.findall(text)}


def from_agentic(a, source: str) -> Result:
    """agentic.foundry.Answer -> Result."""
    steps = []
    looks = iter(a.looks)
    for s in a.steps:
        if s.kind == "timing":
            continue
        images = []
        if s.kind == "look":
            lk = next(looks, None)
            images = list(lk.paths) if lk else []
        steps.append(Step(s.kind, s.title, s.detail, list(s.items), images, s.seconds))
    passages = [Passage(p.rank, p.id, p.source_file, p.doc_id, p.kind, p.caption or p.section,
                        " · ".join(x for x in (p.title, p.date) if x), p.text, p.source, p.cited, p.inspected,
                        list(p.image_paths)) for p in a.passages]
    timing = [s for s in a.steps if s.kind == "timing"]
    retrieval_s = sum(s.seconds or 0 for s in timing if "knowledge base" in s.title)
    vision_s = sum(lk.seconds for lk in a.looks)
    return Result(
        key=f"agentic-{source}", approach="agentic", source=source, text=a.text, raw_text=a.raw_text, model=a.model,
        store=a.names.index, passages=passages, steps=steps, queries=list(a.queries),
        images=[p for lk in a.looks for p in lk.paths], errors=list(a.errors),
        retrieval_s=retrieval_s, generation_s=max(0.0, a.seconds - retrieval_s - vision_s), vision_s=vision_s,
        total_s=a.seconds, input_tokens=a.input_tokens, output_tokens=a.output_tokens,
        reasoning_tokens=a.reasoning_tokens,
        vision_tokens=getattr(a, "vision_input_tokens", 0) + getattr(a, "vision_output_tokens", 0),
        rounds=len(timing) or 1,
    )


def from_rag(a, source: str, collection: str, model: str, file_of: Callable[[str], str]) -> Result:
    """rag.pipeline.Answer -> Result. `file_of(doc_id)` names the input file a chunk's report id came from."""
    cited = cited_numbers(a.answer)
    passages = []
    for n, d in enumerate(a.sources, 1):
        m = d.metadata
        imgs = [p for p in (m.get("image_path") or "").split(";") if p]
        passages.append(Passage(n, d.id, file_of(m.get("doc_id", "")), m.get("doc_id", ""), m.get("kind", ""),
                                m.get("caption") or m.get("section") or "",
                                " · ".join(x for x in (m.get("title"), m.get("date")) if x), d.page_content,
                                m.get("source", ""), n in cited, any(p in a.images for p in imgs), imgs))
    kinds: dict[str, int] = {}
    for p in passages:
        kinds[p.kind] = kinds.get(p.kind, 0) + 1
    t = a.timings
    steps = [
        Step("retrieve", f"Hybrid search over collection {collection}: {len(passages)} chunks",
             "Vector search (Azure embeddings) and BM25 over the same chunks, fused by reciprocal rank. "
             + ", ".join(f"{v} {k}" for k, v in sorted(kinds.items())) + " from "
             + ", ".join(sorted({p.file for p in passages})) + ".", seconds=t.get("retrieval_s")),
    ]
    if a.images:
        steps.append(Step("look", f"{len(a.images)} picture(s) of the best figure chunks sent with the sources",
                          "The answer model reads them in the same call as the text, so their tokens are in the "
                          "input tokens.", images=list(a.images)))
    steps.append(Step("answer", f"Answer model ({model}) wrote the answer and cited {len(cited & set(range(1, len(passages) + 1)))} chunks",
                      seconds=t.get("generation_s")))
    u = a.usage
    return Result(
        key=f"rag-{source}", approach="rag", source=source, text=a.answer, raw_text=a.answer, model=model,
        store=collection, passages=passages, steps=steps, queries=[a.question], images=list(a.images),
        retrieval_s=t.get("retrieval_s", 0.0), generation_s=t.get("generation_s", 0.0), vision_s=0.0,
        total_s=t.get("total_s", 0.0), input_tokens=u.get("input_tokens", 0), output_tokens=u.get("output_tokens", 0),
        reasoning_tokens=u.get("reasoning_tokens", 0), vision_tokens=0, rounds=1,
    )


def run_parallel(tasks: dict[str, Callable[[], Result]], on_done: Callable[[str, Result | None, str | None], None],
                 on_tick: Callable[[dict[str, float]], None] | None = None, tick: float = 0.5) -> None:
    """Run every task in its own thread and report each one the moment it finishes.

    `on_done(key, result, error)` is called on the calling thread (Streamlit widgets may only be drawn there) with
    either a Result or an error string. `on_tick({key: seconds_running})` is called every `tick` seconds for the
    tasks still running, so the page can show that they are alive. No task waits for another.
    """
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, len(tasks))) as pool:
        futures: dict[Future, str] = {pool.submit(fn): key for key, fn in tasks.items()}
        pending = set(futures)
        while pending:
            done, pending = wait(pending, timeout=tick, return_when=FIRST_COMPLETED)
            for fut in done:
                key = futures[fut]
                try:
                    on_done(key, fut.result(), None)
                except Exception as e:  # noqa: BLE001 - the tile shows the error, the others carry on
                    on_done(key, None, f"{type(e).__name__}: {e}")
            if pending and on_tick:
                now = time.perf_counter() - started
                on_tick({futures[f]: now for f in pending})


def comparison_rows(results: dict[str, Result], errors: dict[str, str], keys: list[str]) -> list[dict]:
    """One row per solution with the same measures, plus how each compares with the cheapest and fastest."""
    ok = [results[k] for k in keys if k in results]
    least = min((r.total_tokens for r in ok if r.total_tokens), default=0)
    fastest = min((r.total_s for r in ok if r.total_s), default=0)

    def vs(value: float, base: float) -> str:
        if not base or not value:
            return ""
        return "lowest" if value == base else f"+{(value / base - 1):.0%}"

    rows = []
    for k in keys:
        if k in results:
            r = results[k]
            rows.append({"solution": LABELS[k], "answered": "yes" if r.text.strip() else "empty",
                         "total s": round(r.total_s, 1), "retrieval s": round(r.retrieval_s, 1),
                         "generation s": round(r.generation_s, 1), "vision s": round(r.vision_s, 1),
                         "vs fastest": vs(r.total_s, fastest),
                         "tokens in": r.input_tokens, "tokens out": r.output_tokens,
                         "of which reasoning": r.reasoning_tokens, "vision tokens": r.vision_tokens,
                         "total tokens": r.total_tokens, "vs lowest": vs(r.total_tokens, least),
                         "passages": len(r.passages), "cited": len(r.cited), "pictures": len(r.images),
                         "model rounds": r.rounds, "error": "; ".join(r.errors)})
        else:
            rows.append({"solution": LABELS[k], "answered": "failed", "error": errors.get(k, "")})
    return rows


def session_totals(history: list[dict], keys: list[str]) -> list[dict]:
    """Sum of tokens and seconds per solution over every question asked so far."""
    rows = []
    for k in keys:
        rs = [t["results"][k] for t in history if k in t.get("results", {})]
        failed = sum(1 for t in history if k in t.get("errors", {}))
        if not rs and not failed:
            continue
        n = len(rs)
        rows.append({"solution": LABELS[k], "questions": n, "failed": failed,
                     "total tokens": sum(r.total_tokens for r in rs),
                     "tokens per question": round(sum(r.total_tokens for r in rs) / n) if n else 0,
                     "seconds per question": round(sum(r.total_s for r in rs) / n, 1) if n else 0,
                     "pictures": sum(len(r.images) for r in rs)})
    return rows
