"""The Word arm of the LangChain engine: the stock MarkItDown conversion of the .docx, no plugin, no vision, no
facts. What a team gets from `pip install markitdown` and nothing else; Phase 0 measured it at 52 of 71 answers
retained. The stock Markdown is kept in the store (markdown-stock/<doc>.md) for the Reports page.
"""
from __future__ import annotations

import re

from ..config import settings
from ..schema import Chunk
from ..storage import RAW, Store

STOCK = "markdown-stock"
DATA_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(data:image/[^)]*\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def stock_markdown(doc_id: str, store: Store) -> str:
    """Convert raw/<doc>.docx with MarkItDown's own converters and cache the result in the store."""
    key = f"{STOCK}/{doc_id}.md"
    if store.exists(key):
        return store.text(key)
    from markitdown import MarkItDown

    path = store.local_path(f"{RAW}/{doc_id}.docx")
    text = MarkItDown(enable_plugins=False).convert(str(path)).markdown or ""
    text = DATA_IMAGE_RE.sub("", text)  # inline base64 pictures: nothing a text index can use
    store.put(key, text, "text/markdown")
    return text


def _blocks(text: str) -> list[tuple[str, str, str]]:
    """(heading path, kind, block text) for every paragraph or table, headings tracked by level."""
    out: list[tuple[str, str, str]] = []
    headings: dict[int, str] = {}
    for raw in re.split(r"\n\s*\n", text):
        block = raw.strip()
        if not block:
            continue
        m = HEADING_RE.match(block)
        if m and "\n" not in block:
            level = len(m.group(1))
            headings = {k: v for k, v in headings.items() if k < level}
            headings[level] = m.group(2).strip()
            continue
        kind = "table" if sum(l.lstrip().startswith("|") for l in block.splitlines()) >= 2 else "text"
        out.append((" > ".join(headings[k] for k in sorted(headings)), kind, block))
    return out


def word_chunks(doc_id: str, store: Store) -> list[Chunk]:
    """Chunks of about settings.chunk_chars: prose grouped by heading, a table whole when it fits, each with a
    context header."""
    limit = settings.chunk_chars
    source_file = f"{doc_id}.docx"
    pieces: list[tuple[str, str, str]] = []  # (section, kind, body)
    buf: list[str] = []
    buf_section = None

    def flush() -> None:
        nonlocal buf, buf_section
        if buf and buf_section is not None:
            body = "\n\n".join(buf)
            if len(body) <= limit:
                pieces.append((buf_section, "text", body))
            else:  # long run of prose under one heading: cut on paragraph boundaries
                cur: list[str] = []
                for para in buf:
                    if cur and len("\n\n".join(cur)) + len(para) > limit:
                        pieces.append((buf_section, "text", "\n\n".join(cur)))
                        cur = []
                    while len(para) > limit:  # a single oversized paragraph: hard split
                        pieces.append((buf_section, "text", para[:limit]))
                        para = para[limit:]
                    cur.append(para)
                if cur:
                    pieces.append((buf_section, "text", "\n\n".join(cur)))
        buf, buf_section = [], None

    for section, kind, block in _blocks(stock_markdown(doc_id, store)):
        if kind == "text":
            if buf_section is not None and (section != buf_section or len("\n\n".join(buf)) + len(block) > limit):
                flush()
            buf_section = buf_section if buf_section is not None else section
            buf.append(block)
            continue
        flush()
        if len(block) <= limit:
            pieces.append((section, "table", block))
        else:  # long table: row groups, header rows repeated
            lines = block.splitlines()
            head = lines[:2] if len(lines) > 2 and set(lines[1].replace("|", "").strip()) <= set("-: ") else []
            rows, cur = lines[len(head):], []
            for r in rows:
                if cur and len("\n".join(head + cur)) + len(r) > limit:
                    pieces.append((section, "table", "\n".join(head + cur)))
                    cur = []
                cur.append(r)
            if cur:
                pieces.append((section, "table", "\n".join(head + cur)))
    flush()

    chunks = []
    for i, (section, kind, body) in enumerate(pieces):
        header = f"Report {doc_id} ({source_file})" + (f"\nSection: {section}" if section else "")
        chunks.append(Chunk(id=f"{doc_id}-w{i:05d}", doc_id=doc_id, chunk=i, kind=kind, text=f"{header}\n\n{body}",
                            title=doc_id, section=section, source_file=source_file))
    return chunks
