"""Solution 2: load the MarkItDown + research plugin output (out/A+/<doc>.md and out/A+/<doc>/assets/).

The A+ Markdown already carries most of what the docx loader has to work out: headings, tables with spans,
native chart data, Wingdings ticks, and values digitised from pasted Excel (EMF) charts. What it leaves open is
pictures, marked "(vision description pending)". Those, and pictures with only labels or alt text, are sent to
the vision model here; digitised charts keep their numbers (more precise than a vision reading) unless
`vision="all"` asks for a description on top.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from ..chunking import Block, DocInfo, is_caption
from ..images import RasterImage, describe_images, rasterise_file

log = logging.getLogger(__name__)

ANCHOR_RE = re.compile(r"\s*\[e\d{4,}\]")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
IMAGE_RE = re.compile(r"^Image:\s*(\S+\.(?:png|jpe?g|gif|bmp|emf|wmf|tiff?))\s*$", re.I | re.M)
PENDING = "(vision description pending)"
DIGITISED = "Values digitised from the drawing"


def _front_matter(text: str) -> tuple[dict, str]:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return yaml.safe_load(text[3:end]) or {}, text[end + 4:]
    return {}, text


def _kind(block: str) -> str:
    lines = block.splitlines()
    if IMAGE_RE.search(block):
        return "figure"
    if any(l.startswith("Chart type:") for l in lines):
        return "chart"
    if sum(l.lstrip().startswith("|") for l in lines) >= 2:
        return "table"
    return "text"


def load_markdown(path: Path, vision: str = "auto") -> tuple[DocInfo, list[Block]]:
    """vision: "auto" (pictures without digitised values), "all", or "none"."""
    meta, body = _front_matter(path.read_text(encoding="utf-8"))
    doc_id = str(meta.get("doc_id") or path.stem)
    info = DocInfo(doc_id, str(meta.get("title", "")), str(meta.get("subtitle", "")), str(meta.get("date", "")),
                   extra={k: str(v) for k, v in meta.items() if k in ("sector", "report_type", "analysts")})
    assets = path.parent / doc_id / "assets"

    blocks: list[Block] = []
    headings: dict[int, str] = {}
    for raw in re.split(r"\n\s*\n", body):
        block = ANCHOR_RE.sub("", raw).strip()
        if not block:
            continue
        m = HEADING_RE.match(block)
        if m and "\n" not in block:
            level = len(m.group(1))
            headings = {k: v for k, v in headings.items() if k < level}
            headings[level] = m.group(2).strip()
            continue
        kind = _kind(block)
        first = block.splitlines()[0].strip("* ")
        caption = first if kind != "text" and (is_caption(first) or block.startswith("**")) else ""
        blocks.append(Block(kind, block, " > ".join(headings[k] for k in sorted(headings)), caption=caption))

    # pictures: rasterise every one (the answer step shows them to the model), describe the ones that need it
    todo: list[tuple[Block, RasterImage, str]] = []
    for i, b in enumerate(blocks):
        if b.kind != "figure":
            continue
        images = []
        for name in IMAGE_RE.findall(b.text):
            img = rasterise_file(assets / name) if (assets / name).exists() else None
            if img is not None and not img.too_small:
                images.append(img)
        b.image_path = ";".join(str(img.png_path) for img in images)
        needs = vision == "all" or (vision == "auto" and DIGITISED not in b.text)
        if needs:
            before = next((x.text for x in reversed(blocks[:i]) if x.kind == "text"), "")
            for img in images:
                todo.append((b, img, f"{b.text[:800]}\n{before[-600:]}"))
    descriptions = describe_images([(img, ctx) for _, img, ctx in todo])
    for (b, _, _), desc in zip(todo, descriptions):
        if not desc:
            continue
        line = f"Image description (vision model): {desc}"
        b.text = b.text.replace(PENDING, line, 1) if PENDING in b.text else f"{b.text}\n{line}"
    log.info("%s: %d blocks, %d figures, %d described", path.name, len(blocks),
             sum(b.kind == "figure" for b in blocks), len(todo))
    return info, blocks


def discover(inputs: list[str] | None, default_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for p in (inputs or [str(default_dir)]):
        pp = Path(p)
        paths.extend(sorted(pp.glob("*.md")) if pp.is_dir() else [pp])
    return paths
