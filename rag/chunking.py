"""Shared chunking: both loaders emit Blocks in reading order, this turns them into LangChain Documents.

Rules, so the two solutions are compared on their input and not on chunking:
- consecutive text blocks of one section are packed up to `chunk_chars`;
- a figure or chart is its own chunk (caption + description/data), carrying the image path for answer time;
- a table is its own chunk, split by rows with the header repeated when it is long;
- every chunk starts with a header naming the report and section, so a chunk read alone still says
  "April 2025 Money Map > The Fed", which is what both embeddings and BM25 need for cross-report questions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .config import settings


@dataclass
class Block:
    kind: str                     # text | table | figure | chart
    text: str
    section: str = ""             # heading breadcrumb, "H1 > H2 > H3"
    caption: str = ""
    image_path: str = ""          # rasterised PNG for figures
    meta: dict = field(default_factory=dict)


@dataclass
class DocInfo:
    doc_id: str
    title: str = ""
    subtitle: str = ""
    date: str = ""
    extra: dict = field(default_factory=dict)

    def header(self) -> str:
        name = " - ".join(x for x in (self.title, self.subtitle) if x)
        return f"Report {self.doc_id}: {name}" + (f" ({self.date})" if self.date else "")


CAPTION_RE = re.compile(r"^\s*(\*\*)?\s*(fig(ure)?|chart|exhibit|table)\.?\s*\d+[a-z]?\s*[:.\-–]", re.I)


def is_caption(text: str) -> bool:
    return bool(CAPTION_RE.match(text))


def _split_table(text: str, limit: int) -> list[str]:
    lines = text.splitlines()
    head_n = 2 if len(lines) > 1 and re.match(r"^\|?\s*:?-{3,}", lines[1]) else 1
    head, rows = lines[:head_n], lines[head_n:]
    parts, cur = [], []
    for r in rows:
        if cur and len("\n".join(head + cur + [r])) > limit:
            parts.append("\n".join(head + cur))
            cur = []
        cur.append(r)
    if cur or not parts:
        parts.append("\n".join(head + cur))
    return parts


def to_documents(info: DocInfo, blocks: list[Block], source: str) -> list[Document]:
    limit = settings.chunk_chars
    splitter = RecursiveCharacterTextSplitter(chunk_size=limit, chunk_overlap=150)
    pieces: list[tuple[str, Block]] = []  # (body, representative block)

    buf: list[str] = []
    buf_block: Block | None = None

    def flush() -> None:
        nonlocal buf, buf_block
        if buf and buf_block is not None:
            body = "\n\n".join(buf)
            for part in (splitter.split_text(body) if len(body) > limit else [body]):
                pieces.append((part, buf_block))
        buf, buf_block = [], None

    for b in blocks:
        if not b.text.strip():
            continue
        if b.kind == "text":
            if buf_block is not None and (b.section != buf_block.section or
                                          len("\n\n".join(buf)) + len(b.text) > limit):
                flush()
            buf_block = buf_block or b
            buf.append(b.text.strip())
            continue
        flush()
        if b.kind == "table" and len(b.text) > limit:
            for part in _split_table(b.text, limit):
                pieces.append(((b.caption + "\n" if b.caption else "") + part, b))
        elif len(b.text) > 2 * limit:  # long digitised series: keep the caption on every piece
            for part in splitter.split_text(b.text):
                pieces.append((part if part.startswith(b.caption) else f"{b.caption} (continued)\n{part}", b))
        else:
            pieces.append((b.text.strip(), b))
    flush()

    docs = []
    for i, (body, b) in enumerate(pieces):
        header = info.header() + (f"\nSection: {b.section}" if b.section else "")
        docs.append(Document(
            page_content=f"{header}\n\n{body}",
            metadata={
                "doc_id": info.doc_id, "title": info.title, "subtitle": info.subtitle, "date": info.date,
                "source": source, "section": b.section, "kind": b.kind, "caption": b.caption,
                "image_path": b.image_path, "chunk": i,
            },
        ))
    return docs
