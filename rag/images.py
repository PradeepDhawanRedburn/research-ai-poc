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

PROMPT_VERSION = "v2"  # bump when VISION_SYSTEM changes: descriptions are cached per picture and prompt version
MAX_SIDE = 1600       # longest side sent to the model
MIN_SIDE = 80         # smaller than this on both sides: icon or logo, not worth describing
MIN_AREA = 120 * 120

# The description is what search finds and what the answer model reads, so it is written for the questions people
# ask of pictures: how many, what is printed, which one, what colour, what value. A description that already holds
# the count and the printed text saves a vision call (and its picture tokens) at answer time.
VISION_SYSTEM = """You describe one figure from a financial research report (equity or economics) so that it can be found by search and questions about it can be answered from your text alone, without seeing the picture. Be factual and exhaustive about what is visible; never invent numbers or text you cannot read. Write plain text, Markdown tables where useful, at most 400 words.

Start with one line: the kind of figure (chart, table, screenshot, photo, product shot, diagram, map, logo) and what it is about.

Then, depending on the kind:
- Chart: chart type, title, axes with units and ranges, every series name (legend), and the values you can read: latest value of each series, notable peaks and troughs with dates, the overall trend. Bar charts: every bar label and value. Pie charts: every slice with its share.
- Table or matrix drawn as a picture: transcribe it as a Markdown table, including ticks/crosses, shading and colours, with the meaning of each.
- Photo, product shot, packaging, advert or screenshot: (1) list every distinct item (can, bottle, pack, person, logo, tile, button) from left to right, with its printed text transcribed exactly (brand, variant, flavour, size, price, claim, status label such as "Not available in your region"); (2) give counts: how many of each item, and for things drawn on packaging, how many of each form (whole fruit, slice, wedge, drawing, logo) per item and in total; (3) colours and layout; (4) anything partly hidden, cut off or unreadable, said as such.
- Diagram, map, flowchart or infographic: all text, in reading order, and the relationships or flows it shows.
- Logo or decoration: one sentence.

Counts and printed text come first; do not summarise them away. If the caption and nearby text name the figure, use that name."""


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
    text = ("Describe this figure so that questions about it (how many, what is printed, which one, what value) can be "
            "answered from your description alone.")
    if context:
        text += f"\n\nText around it in the report (caption, source, nearby paragraph):\n{context[:1500]}"
    return [
        SystemMessage(VISION_SYSTEM),
        HumanMessage(content=[
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": data_url(img.png_path), "detail": "high"}},
        ]),
    ]
