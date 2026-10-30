"""Images: rasterise to PNG, describe with the vision model, cache everything on disk.

Vision models take PNG/JPEG/GIF/WebP only. Excel charts pasted into Word arrive as EMF; Pillow renders EMF/WMF
through GDI on Windows, so this runs on the analysts' machines but not in a Linux container (those images are
skipped with a warning there). Descriptions are cached by image content hash, so the docx and markdown solutions
share them and re-ingesting costs nothing.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from PIL import Image

from .azure_models import vision_model
from .config import settings

log = logging.getLogger(__name__)

PROMPT_VERSION = "v1"
MAX_SIDE = 1600       # longest side sent to the model
MIN_SIDE = 80         # smaller than this on both sides: icon or logo, not worth describing
MIN_AREA = 120 * 120

VISION_SYSTEM = """You describe figures from financial research reports (equity and economics) so they can be \
found by search and used to answer questions. Be factual; never invent numbers you cannot read.

- Chart: give the chart type, title, axes with units and ranges, every series name (legend), and the values you \
can read: latest value of each series, notable peaks and troughs with their dates, and the overall trend. \
For bar charts read every bar label and value.
- Table or matrix rendered as an image: transcribe it as a Markdown table, including ticks/crosses and colours.
- Diagram, map or infographic: list all text and the relationships it shows.
- Photo, logo or decoration: one sentence.
Answer in plain text (Markdown tables allowed), at most 350 words."""


@dataclass
class RasterImage:
    sha: str          # hash of the original bytes (cache key)
    png_path: Path    # rasterised copy, used again at answer time
    width: int
    height: int

    @property
    def too_small(self) -> bool:
        return (self.width < MIN_SIDE and self.height < MIN_SIDE) or self.width * self.height < MIN_AREA


def _png_dir() -> Path:
    d = settings.cache_dir / "png"
    d.mkdir(parents=True, exist_ok=True)
    return d


def rasterise(data: bytes, ext: str) -> RasterImage | None:
    """Convert image bytes (png, jpeg, gif, bmp, emf, wmf, tiff) to a cached PNG. None if it cannot be read."""
    sha = hashlib.sha256(data).hexdigest()[:16]
    out = _png_dir() / f"{sha}.png"
    if out.exists():
        with Image.open(out) as im:
            return RasterImage(sha, out, *im.size)
    try:
        im = Image.open(io.BytesIO(data))
        if ext.lower().lstrip(".") in ("emf", "wmf"):
            im.load(dpi=150)
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, "white")  # EMF and transparent PNGs have no background
        im = Image.alpha_composite(bg, im).convert("RGB")
        im.thumbnail((MAX_SIDE, MAX_SIDE))
        im.save(out, "PNG", optimize=True)
        return RasterImage(sha, out, *im.size)
    except Exception as e:  # unsupported format, or EMF off Windows
        log.warning("cannot rasterise %s image (%s): %s", ext, sha, e)
        return None


def rasterise_file(path: Path) -> RasterImage | None:
    return rasterise(path.read_bytes(), path.suffix)


def local_png(path: str | Path) -> Path | None:
    """The PNG behind a path recorded at ingest time. Chunk metadata and the search index store the absolute path of
    the cached PNG, which breaks when the index was built on another machine or with another RAG_CACHE_DIR; the
    file name is the content hash, so the same picture is found again in this machine's cache."""
    pp = Path(path)
    if pp.exists():
        return pp
    local = _png_dir() / pp.name
    return local if local.exists() else None


def data_url(png_path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(png_path.read_bytes()).decode()


def _cache_path(sha: str) -> Path:
    d = settings.cache_dir / "vision"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{sha}.{PROMPT_VERSION}.json"


def describe_images(items: list[tuple[RasterImage, str]]) -> list[str]:
    """Describe each (image, context) pair; context is the caption and surrounding text. Uses the cache."""
    results: list[str | None] = []
    todo: list[int] = []
    first: dict[str, int] = {}  # the same picture (a logo, a repeated chart) is described once
    for i, (img, _) in enumerate(items):
        p = _cache_path(img.sha)
        if p.exists():
            results.append(json.loads(p.read_text(encoding="utf-8"))["description"])
        else:
            results.append(None)
            if img.sha not in first:
                first[img.sha] = i
                todo.append(i)
    if todo:
        log.info("describing %d images with %s (%d cached)", len(todo), settings.vision_deployment,
                 len(items) - len(todo))
        prompts = [_vision_messages(*items[i]) for i in todo]
        outputs = vision_model().batch(prompts, config={"max_concurrency": settings.vision_concurrency},
                                       return_exceptions=True)
        for i, out in zip(todo, outputs):
            if isinstance(out, Exception):
                log.warning("vision call failed for %s: %s", items[i][0].sha, out)
                results[i] = ""
                continue
            text = str(out.content).strip()
            results[i] = text
            _cache_path(items[i][0].sha).write_text(
                json.dumps({"description": text, "model": settings.vision_deployment}, ensure_ascii=False),
                encoding="utf-8")
    for i, (img, _) in enumerate(items):
        if results[i] is None:
            results[i] = results[first[img.sha]]
    return [r or "" for r in results]


def _vision_messages(img: RasterImage, context: str) -> list:
    text = "Describe this figure."
    if context:
        text += f"\n\nText around it in the report (caption, source, nearby paragraph):\n{context[:1500]}"
    return [
        SystemMessage(VISION_SYSTEM),
        HumanMessage(content=[
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": data_url(img.png_path), "detail": "high"}},
        ]),
    ]
