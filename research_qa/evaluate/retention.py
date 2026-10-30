"""Evidence retention and retrieval without any model, over the store.

For every golden question we know which strings the answer rests on (numbers, names, tick/cross states). The
Markdown+ conversion "retains" the evidence when all of it survives in markdown/<doc>.md, and "retrieves" it when a
plain BM25 search over chunks/<doc>.jsonl puts all of it in the top-k chunks. Cheap, deterministic, and the direct
test of "does the conversion lose the information".

The Word arm has no retention measure: Azure chunks the .docx inside the service and the chunks are not ours to
read back as a file. Its retrieval quality shows up in the evaluation run (evidence found in the passages returned).
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

from rank_bm25 import BM25Okapi

from ..storage import CHUNKS, MARKDOWN, Store, open_store
from .golden import load_questions

QUOTES = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "−": "-",
          " ": " ", " ": " ", " ": " "}
TICKS = [(re.compile(r"✓\s*\(green\)"), " tick_green "), (re.compile(r"✗\s*\(red\)"), " cross_red "),
         (re.compile(r"✓"), " tick "), (re.compile(r"✗"), " cross ")]
THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")
MD_NOISE = re.compile(r"[|*_`#>\[\]]")
TOKEN_RE = re.compile(r"[a-z0-9]+(?:[./%-][a-z0-9]+)*")
FIG_RE = re.compile(r"\b(?:fig(?:ure)?\.?)\s*(\d+)", re.I)


def normalise(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    for a, b in QUOTES.items():
        s = s.replace(a, b)
    for pat, rep in TICKS:
        s = pat.sub(rep, s)
    s = THOUSANDS.sub("", s)
    s = MD_NOISE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def evidence_items(q: dict) -> list[list[str]]:
    """Each item is a list of acceptable alternatives (any one present counts)."""
    return [[normalise(x) for x in (e if isinstance(e, list) else [e])] for e in q.get("evidence", [])]


def present(items: list[list[str]], text_norm: str) -> list[bool]:
    return [any(alt in text_norm for alt in alts) for alts in items]


def toks(s: str) -> list[str]:
    return TOKEN_RE.findall(normalise(s))


def tokens_estimate(s: str) -> int:
    try:
        import tiktoken

        return len(tiktoken.get_encoding("cl100k_base").encode(s))
    except Exception:  # noqa: BLE001 - encoding file not cached: a rough count is enough
        return len(s) // 4


def bm25_topk(bm25: BM25Okapi, n: int, query: str, k: int, first: list[int] | None = None) -> list[int]:
    scores = bm25.get_scores(toks(query))
    ranked = sorted(range(n), key=lambda i: -scores[i])
    if first:
        head = sorted(first, key=lambda i: -scores[i])[:k]
        return head + [i for i in ranked if i not in head][: k - len(head)]
    return ranked[:k]


def question_docs(q: dict) -> list[str]:
    """Reports a question draws on: `docs` for cross-document questions, else `doc`."""
    return list(q.get("docs") or [q["doc"]])


def check(store: Store | None = None, k: int = 5, routed: bool = True, questions: list[dict] | None = None) -> dict:
    """Rows per answerable question plus aggregates. `routed`: a question naming "Fig N" searches that figure's
    chunks first, which is what the agent layer's filters do."""
    store = store or open_store()
    questions = questions or load_questions()
    docs = sorted({d for q in questions for d in question_docs(q)})
    loaded: dict[str, tuple[str, list[dict], list[str]]] = {}
    for d in docs:
        if not (store.exists(f"{MARKDOWN}/{d}.md") and store.exists(f"{CHUNKS}/{d}.jsonl")):
            continue
        chunks = store.jsonl(f"{CHUNKS}/{d}.jsonl")
        loaded[d] = (normalise(store.text(f"{MARKDOWN}/{d}.md")), chunks, [normalise(c.get("text", "")) for c in chunks])
    combined: dict[tuple, tuple] = {}

    def corpus(ds: tuple[str, ...]):
        """Markdown, chunks, BM25 and normalised texts over one report or, for cross-document questions, several."""
        if ds not in combined:
            full = " ".join(loaded[d][0] for d in ds)
            chunks = [c for d in ds for c in loaded[d][1]]
            norms = [n for d in ds for n in loaded[d][2]]
            combined[ds] = (full, chunks, BM25Okapi([toks(c.get("text", "")) for c in chunks]) if chunks else None, norms)
        return combined[ds]

    rows = []
    for q in questions:
        items = evidence_items(q)
        if q.get("type") == "unanswerable" or not items:
            continue
        ds = tuple(question_docs(q))
        if any(d not in loaded for d in ds):
            rows.append({"id": q["id"], "type": q["type"], "doc": q["doc"], "available": False})
            continue
        full, chunks, bm25, norms = corpus(ds)
        retained = present(items, full)
        first = None
        m = FIG_RE.search(q["question"]) if routed else None
        if m:
            want = f"fig {m.group(1)}"
            first = [i for i, c in enumerate(chunks) if (c.get("figure") or "").lower() == want] or None
        top = bm25_topk(bm25, len(chunks), q["question"], k, first) if bm25 else []
        retrieved = present(items, " ".join(norms[i] for i in top))
        rows.append({"id": q["id"], "type": q["type"], "doc": q["doc"], "available": True,
                     "retained_all": all(retained), "retrieved_all": all(retrieved),
                     "missing": [alts[0] for alts, ok in zip(items, retained) if not ok],
                     "context_tokens": tokens_estimate(" ".join(chunks[i].get("text", "") for i in top))})

    agg: dict[str, dict] = defaultdict(lambda: {"n": 0, "retained": 0, "retrieved": 0, "context_tokens": 0})
    for r in rows:
        if not r["available"]:
            continue
        for key in (r["type"], "ALL"):
            a = agg[key]
            a["n"] += 1
            a["retained"] += r["retained_all"]
            a["retrieved"] += r["retrieved_all"]
            a["context_tokens"] += r["context_tokens"]
    return {"k": k, "docs_checked": sorted(loaded), "docs_missing": [d for d in docs if d not in loaded],
            "rows": rows, "by_type": dict(agg)}


def report_markdown(res: dict) -> str:
    k = res["k"]
    out = ["# Evidence retention and retrieval (Markdown+, no model)", "",
           f"Retained = all evidence strings present in markdown/<doc>.md. Retrieved@{k} = all present in the "
           f"top-{k} BM25 chunks of chunks/<doc>.jsonl. The Word arm has no retention measure: Azure chunks the "
           ".docx inside the service; its retrieval shows in the evaluation run.", ""]
    if res["docs_missing"]:
        out += [f"Not in the store (run ingest): {', '.join(res['docs_missing'])}", ""]
    out += ["| type | questions | retained | retrieved@%d | avg context tokens |" % k, "|---|---|---|---|---|"]
    for t, a in sorted(res["by_type"].items(), key=lambda kv: (kv[0] == "ALL", kv[0])):
        label = "**ALL**" if t == "ALL" else t
        out.append(f"| {label} | {a['n']} | {a['retained']}/{a['n']} | {a['retrieved']}/{a['n']} | "
                   f"{round(a['context_tokens'] / a['n']) if a['n'] else 0} |")
    misses = [r for r in res["rows"] if r.get("available") and not r["retained_all"]]
    out += ["", f"## Lost evidence: {len(misses)} questions", ""]
    out += [f"- {r['id']} ({r['type']}, {r['doc']}): missing `{r['missing'][0]}`" for r in misses] or ["None."]
    return "\n".join(out) + "\n"
