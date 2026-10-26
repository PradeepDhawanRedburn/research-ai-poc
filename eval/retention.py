"""Evidence-retention and retrieval evaluation across pipelines. No LLM needed.

For every golden question we know which strings the answer rests on (numbers, names,
tick/cross states). A pipeline "retains" the evidence when all of it survives in its
output; "retrieves" it when a plain BM25 search over its chunks puts all of it in the
top-k chunks for the question. Both are deterministic and cheap, and they measure the
exact thing under debate: does the conversion lose the information the answer needs?

Usage:  uv run python eval/retention.py [--k 5] [--out reports]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import unicodedata
from collections import defaultdict

import yaml
from rank_bm25 import BM25Okapi

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# cl100k_base is fetched once with the system certificate store (see README); tiktoken reads it from here
os.environ.setdefault("TIKTOKEN_CACHE_DIR", os.path.join(ROOT, ".tiktoken_cache"))

# ----------------------------------------------------------------------------- text normalisation

QUOTES = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "−": "-",
          " ": " ", " ": " ", " ": " "}
TICKS = [(re.compile(r"✓\s*\(green\)"), " tick_green "), (re.compile(r"✗\s*\(red\)"), " cross_red "),
         (re.compile(r"✓"), " tick "), (re.compile(r"✗"), " cross ")]
THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")
MD_NOISE = re.compile(r"[|*_`#>\[\]]")


def normalise(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    for a, b in QUOTES.items():
        s = s.replace(a, b)
    for pat, rep in TICKS:
        s = pat.sub(rep, s)
    s = THOUSANDS.sub("", s)
    s = MD_NOISE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def tokens_estimate(s: str) -> int:
    try:
        import tiktoken  # optional

        return len(tiktoken.get_encoding("cl100k_base").encode(s))
    except Exception:  # noqa: BLE001
        return len(s) // 4


# ----------------------------------------------------------------------------- pipelines

def chunk_plain(text: str, max_chars: int = 1800) -> list[str]:
    """Generic chunker for Markdown output: pack paragraphs, keep tables whole where possible."""
    blocks = re.split(r"\n\s*\n", text)
    chunks, buf = [], ""
    for b in blocks:
        if not b.strip():
            continue
        if buf and len(buf) + len(b) > max_chars:
            chunks.append(buf)
            buf = ""
        buf = (buf + "\n\n" + b) if buf else b
    if buf:
        chunks.append(buf)
    return chunks


def load_pipeline(name: str, doc_id: str) -> tuple[str, list[str], list[dict]] | None:
    """Return (full_text, chunks, chunk_metadata) for a pipeline on a document, or None if not available.

    'C+route' is pipeline C's output searched with the figure-number routing the agent layer would do:
    a question that names "Fig N" is answered first from chunks whose metadata says figure_no == Fig N.
    Pipelines A and B have no such metadata, which is part of the point.
    """
    base = name.replace("+route", "")
    if base in ("A", "A+", "B"):
        p = os.path.join(ROOT, "out", base, f"{doc_id}.md")
        if not os.path.exists(p):
            return None
        t = open(p, encoding="utf-8").read()
        chunks = chunk_plain(t)
        return t, chunks, [{} for _ in chunks]
    if base == "C":
        p = os.path.join(ROOT, "out", "C", doc_id, "rendered.md")
        if not os.path.exists(p):
            return None
        t = open(p, encoding="utf-8").read()
        meta = [json.loads(ln) for ln in open(os.path.join(ROOT, "out", "C", doc_id, "chunks.jsonl"), encoding="utf-8")]
        return t, [m["text"] for m in meta], meta
    raise ValueError(name)


FIG_RE = re.compile(r"\b(?:fig(?:ure)?\.?)\s*(\d+)", re.I)


def route(name: str, query: str, meta: list[dict]) -> list[int] | None:
    """Chunk indices a metadata-aware retriever would search first, or None for no routing."""
    if not name.endswith("+route"):
        return None
    m = FIG_RE.search(query)
    if not m:
        return None
    want = f"fig {m.group(1)}"
    hits = [i for i, md in enumerate(meta) if (md.get("figure_no") or "").lower() == want]
    return hits or None


# ----------------------------------------------------------------------------- evaluation

def evidence_items(q: dict) -> list[list[str]]:
    """Each item is a list of acceptable alternatives (any one present counts)."""
    out = []
    for e in q.get("evidence", []):
        out.append([normalise(x) for x in (e if isinstance(e, list) else [e])])
    return out


def present(items: list[list[str]], text_norm: str) -> list[bool]:
    return [any(alt in text_norm for alt in alts) for alts in items]


TOKEN_RE = re.compile(r"[a-z0-9]+(?:[./%-][a-z0-9]+)*")


def toks(s: str) -> list[str]:
    """Punctuation-insensitive tokens: 'Ticker:' and 'ticker' must meet."""
    return TOKEN_RE.findall(normalise(s))


_bm25_cache: dict[int, BM25Okapi] = {}


def bm25_topk(chunks: list[str], query: str, k: int, first: list[int] | None = None) -> list[int]:
    key = id(chunks)
    if key not in _bm25_cache:
        _bm25_cache[key] = BM25Okapi([toks(c) for c in chunks])
    scores = _bm25_cache[key].get_scores(toks(query))
    ranked = sorted(range(len(chunks)), key=lambda i: -scores[i])
    if first:
        # routed chunks take the leading slots (in score order), the rest fill up from the global ranking
        head = sorted(first, key=lambda i: -scores[i])[:k]
        return head + [i for i in ranked if i not in head][: k - len(head)]
    return ranked[:k]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--out", default=os.path.join(ROOT, "reports"))
    ap.add_argument("--pipelines", default="A,A+,C,C+route")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    questions = []
    for f in sorted(glob.glob(os.path.join(ROOT, "golden", "*.yaml"))):
        questions.extend(yaml.safe_load(open(f, encoding="utf-8")) or [])
    pipelines = args.pipelines.split(",")
    docs = sorted({q["doc"] for q in questions})

    cache: dict[tuple[str, str], tuple[str, list[str], str, list[str], list[dict]]] = {}
    for name in pipelines:
        for d in docs:
            loaded = load_pipeline(name, d)
            if loaded:
                full, chunks, meta = loaded
                cache[(name, d)] = (full, chunks, normalise(full), [normalise(c) for c in chunks], meta)

    rows = []
    for q in questions:
        items = evidence_items(q)
        if q.get("type") == "unanswerable" or not items:
            continue
        for name in pipelines:
            hit = cache.get((name, q["doc"]))
            if not hit:
                rows.append({"qid": q["id"], "type": q["type"], "doc": q["doc"], "pipeline": name, "available": False})
                continue
            full, chunks, full_norm, chunks_norm, meta = hit
            retained = present(items, full_norm)
            top = bm25_topk(chunks, q["question"], args.k, first=route(name, q["question"], meta))
            ctx_norm = " ".join(chunks_norm[i] for i in top)
            retrieved = present(items, ctx_norm)
            rows.append({
                "qid": q["id"], "type": q["type"], "doc": q["doc"], "pipeline": name, "available": True,
                "retained_all": all(retained), "retained_frac": sum(retained) / len(retained),
                "retrieved_all": all(retrieved), "retrieved_frac": sum(retrieved) / len(retrieved),
                "missing": [alts[0] for alts, ok in zip(items, retained) if not ok],
                "context_tokens": tokens_estimate(" ".join(chunks[i] for i in top)),
            })

    # aggregate
    agg: dict[tuple[str, str], dict] = defaultdict(lambda: {"n": 0, "ret": 0, "rtr": 0, "ctx": 0, "avail": 0})
    for r in rows:
        a = agg[(r["pipeline"], r["type"])]
        a["n"] += 1
        if r["available"]:
            a["avail"] += 1
            a["ret"] += r["retained_all"]
            a["rtr"] += r["retrieved_all"]
            a["ctx"] += r["context_tokens"]
    types = sorted({r["type"] for r in rows})
    doc_tokens = {(n, d): tokens_estimate(cache[(n, d)][0]) for (n, d) in cache}

    lines = ["# Evidence retention and retrieval, by pipeline and question type", "",
             f"Questions: {len({r['qid'] for r in rows})} answerable (unanswerable ones are for the LLM stage). k = {args.k}.", "",
             "Retained = all evidence strings present anywhere in the pipeline's full output. "
             f"Retrieved@{args.k} = all evidence strings present in the top-{args.k} BM25 chunks for the question.", "",
             "| Type | " + " | ".join(f"{p} retained | {p} retrieved@{args.k}" for p in pipelines) + " |",
             "|---|" + "---|---|" * len(pipelines)]
    for t in types:
        cells = []
        for p in pipelines:
            a = agg[(p, t)]
            if a["avail"] == 0:
                cells += ["not run", "not run"]
            else:
                cells += [f"{a['ret']}/{a['avail']}", f"{a['rtr']}/{a['avail']}"]
        lines.append(f"| {t} | " + " | ".join(cells) + " |")
    tot = []
    for p in pipelines:
        n = sum(a["avail"] for (pp, _), a in agg.items() if pp == p)
        ret = sum(a["ret"] for (pp, _), a in agg.items() if pp == p)
        rtr = sum(a["rtr"] for (pp, _), a in agg.items() if pp == p)
        tot += [f"**{ret}/{n}**" if n else "not run", f"**{rtr}/{n}**" if n else "not run"]
    lines.append("| **All** | " + " | ".join(tot) + " |")
    lines += ["", "## Size of what the model would see", "",
              "| Doc | " + " | ".join(f"{p} full-doc tokens" for p in pipelines) + " | " +
              " | ".join(f"{p} avg top-{args.k} context tokens" for p in pipelines) + " |",
              "|---|" + "---|" * (2 * len(pipelines))]
    for d in docs:
        cells = [str(doc_tokens.get((p, d), "not run")) for p in pipelines]
        for p in pipelines:
            rs = [r for r in rows if r["pipeline"] == p and r["doc"] == d and r["available"]]
            cells.append(str(round(sum(r["context_tokens"] for r in rs) / len(rs))) if rs else "not run")
        lines.append(f"| {d} | " + " | ".join(cells) + " |")
    lines += ["", "Token counts use tiktoken cl100k_base when available, otherwise characters/4.", "",
              "## Misses by pipeline (first evidence string missing)", ""]
    for p in pipelines:
        misses = [r for r in rows if r["pipeline"] == p and r["available"] and not r["retained_all"]]
        lines.append(f"### Pipeline {p}: {len(misses)} questions with lost evidence")
        for r in misses:
            lines.append(f"- {r['qid']} ({r['type']}, {r['doc']}): missing `{r['missing'][0]}`")
        lines.append("")
    report = "\n".join(lines)
    with open(os.path.join(args.out, "retention.md"), "w", encoding="utf-8") as fh:
        fh.write(report)
    with open(os.path.join(args.out, "retention_rows.json"), "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=1, ensure_ascii=False)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
