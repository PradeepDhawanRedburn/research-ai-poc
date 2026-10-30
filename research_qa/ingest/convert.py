"""Word → canonical model + pictures in the store.

MarkItDown is the chassis; the research plugin (src/research_ingest) does the Word step and leaves the canonical
document on the converter. Every picture the extractor saved is rasterised to PNG once (EMF/WMF through GDI on
Windows when Pillow is available) and uploaded under `assets/<doc>/<sha>.png`, where `sha` is the extractor's
content hash, the same id the figure elements refer to.
"""
from __future__ import annotations

import io
import logging
import shutil
import struct
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..storage import RAW, Store, asset_key

log = logging.getLogger(__name__)

MAX_SIDE = 1600       # longest side kept
MIN_SIDE = 80         # smaller than this on both sides: an icon or logo
MIN_AREA = 120 * 120
RASTER_EXT = {"png", "jpg", "jpeg", "gif", "bmp", "emf", "wmf", "tif", "tiff"}


@dataclass
class AssetInfo:
    sha: str
    key: str                # store key of the PNG, "" when not uploaded
    ext: str
    width: int = 0
    height: int = 0
    too_small: bool = False
    digitised_quality: str = ""   # "ok" | "low" | "" (not a vector chart)


@dataclass
class Converted:
    doc_id: str
    doc: dict                                   # canonical model
    markdown: str                               # MarkItDown output as converted (before descriptions)
    assets: dict[str, str] = field(default_factory=dict)        # sha -> store key (uploaded pictures only)
    asset_info: dict[str, AssetInfo] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _png_size(data: bytes) -> tuple[int, int]:
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        w, h = struct.unpack(">II", data[16:24])
        return int(w), int(h)
    return 0, 0


def rasterise(data: bytes, ext: str) -> tuple[bytes | None, int, int, str]:
    """(png bytes, width, height, note). Without Pillow only PNGs pass through."""
    try:
        from PIL import Image
    except ImportError:
        if ext == "png":
            w, h = _png_size(data)
            return data, w, h, "pillow missing: PNG uploaded as is"
        return None, 0, 0, f"pillow missing: {ext} not rasterised"
    try:
        im = Image.open(io.BytesIO(data))
        if ext in ("emf", "wmf"):
            try:
                im.load(dpi=200)   # vector formats render at the requested resolution (Windows GDI)
            except Exception:  # noqa: BLE001 - not every build takes dpi
                im.load()
        else:
            im.load()
        if im.mode in ("P", "LA", "RGBA"):
            bg = Image.new("RGB", im.size, "white")
            bg.paste(im.convert("RGBA"), mask=im.convert("RGBA").split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        w, h = im.size
        if max(w, h) > MAX_SIDE:
            scale = MAX_SIDE / max(w, h)
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))))
            w, h = im.size
        out = io.BytesIO()
        im.save(out, format="PNG", optimize=True)
        return out.getvalue(), w, h, ""
    except Exception as e:  # noqa: BLE001 - unreadable picture
        return None, 0, 0, f"{type(e).__name__}: {str(e)[:120]}"


def convert(doc_id: str, store: Store) -> Converted:
    """Convert `raw/<doc_id>.docx` from the store; upload its pictures; return the canonical model."""
    from markitdown import MarkItDown

    from research_ingest.markitdown_plugin import ResearchDocxConverter

    path = store.local_path(f"{RAW}/{doc_id}.docx")
    tmp = Path(tempfile.mkdtemp(prefix="research_qa_"))
    try:
        md = MarkItDown()
        conv = ResearchDocxConverter()
        md.register_converter(conv)
        result = md.convert(str(path), assets_dir=str(tmp / "assets"))
        doc = conv.last_document
        out = Converted(doc_id, doc, result.markdown, warnings=list(doc.get("stats", {}).get("warnings", [])))
        skipped: dict[str, list[str]] = {}   # reason -> files, folded into one warning each
        for sha, info in doc.get("assets", {}).items():
            ext = (info.get("ext") or "").lower()
            dig = info.get("digitised") or {}
            ai = AssetInfo(sha, "", ext, digitised_quality=dig.get("quality", "") if dig else "")
            out.asset_info[sha] = ai
            if ext not in RASTER_EXT:
                continue
            src = tmp / "assets" / info["file"]
            if not src.exists():
                out.warnings.append(f"asset file missing: {info['file']}")
                continue
            png, w, h, note = rasterise(src.read_bytes(), ext)
            ai.width, ai.height = w, h
            if png is None:
                skipped.setdefault(note, []).append(info["file"])
                continue
            if note:
                log.debug("%s: %s", info["file"], note)
            if w and h and ((w < MIN_SIDE and h < MIN_SIDE) or w * h < MIN_AREA):
                ai.too_small = True
                continue
            key = asset_key(doc_id, sha)
            if not store.exists(key):
                store.put(key, png, "image/png")
            ai.key = key
            out.assets[sha] = key
        for note, files in skipped.items():
            out.warnings.append(f"{len(files)} picture(s) not uploaded ({note}): {', '.join(files[:3])}"
                                + (" ..." if len(files) > 3 else ""))
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
