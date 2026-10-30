"""Picture descriptions, written for the questions people ask of pictures, cached in the store.

One vision call per distinct picture and prompt version; the cache (`descriptions/<sha>.<version>.json`) is shared
by every report and every run. The description goes into the Markdown and the figure chunk, so most picture
questions are answered from text without a vision call at question time.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import PurePosixPath

from ..config import settings
from ..storage import DESCRIPTIONS, Store

log = logging.getLogger(__name__)

PROMPT_VERSION = "v2"  # bump when VISION_SYSTEM changes

VISION_SYSTEM = """You describe one figure from a financial research report (equity or economics) so that it can be \
found by search and questions about it can be answered from your text alone, without seeing the picture. \
Be factual and exhaustive about what is visible; never invent numbers or text you cannot read. Write plain text, \
Markdown tables where useful, at most 400 words.

Start with one line: the kind of figure (chart, table, screenshot, photo, product shot, diagram, map, logo) and \
what it is about.

Then, depending on the kind:
- Chart: chart type, title, axes with units and ranges, every series name (legend), and the values you can read: \
latest value of each series, notable peaks and troughs with dates, the overall trend. Bar charts: every bar label \
and value. Pie charts: every slice with its share.
- Table or matrix drawn as a picture: transcribe it as a Markdown table, including ticks/crosses, shading and \
colours, with the meaning of each.
- Photo, product shot, packaging, advert or screenshot: (1) list every distinct item (can, bottle, pack, person, \
logo, tile, button) from left to right, with its printed text transcribed exactly (brand, variant, flavour, size, \
price, claim, status label such as "Not available in your region"); (2) give counts: how many of each item, and for \
things drawn on packaging, how many of each form (whole fruit, slice, wedge, drawing, logo) per item and in total; \
(3) colours and layout; (4) anything partly hidden, cut off or unreadable, said as such.
- Diagram, map, flowchart or infographic: all text, in reading order, and the relationships or flows it shows.
- Logo or decoration: one sentence.

Counts and printed text come first; do not summarise them away. If the caption and nearby text name the figure, \
use that name."""

USER_TEXT = ("Describe this figure so that questions about it (how many, what is printed, which one, what value) can "
             "be answered from your description alone.")


def cache_key(asset_key: str) -> str:
    return f"{DESCRIPTIONS}/{PurePosixPath(asset_key).stem}.{PROMPT_VERSION}.json"


def _client():
    from openai import AzureOpenAI

    settings.require("openai_endpoint")
    kw = {"azure_endpoint": settings.openai_endpoint, "api_version": settings.api_version, "max_retries": 4}
    if settings.openai_api_key:
        kw["api_key"] = settings.openai_api_key
    else:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider

        kw["azure_ad_token_provider"] = get_bearer_token_provider(
            DefaultAzureCredential(process_timeout=60), "https://cognitiveservices.azure.com/.default")
    return AzureOpenAI(**kw)


def describe_one(client, png: bytes, context: str) -> dict:
    """One vision call; returns the cache record."""
    text = USER_TEXT + (f"\n\nText around it in the report (caption, source, nearby paragraph):\n{context[:1500]}"
                        if context else "")
    url = "data:image/png;base64," + base64.b64encode(png).decode()
    reasoning = settings.vision_deployment.lower().startswith(("gpt-5", "o"))
    kw = {"reasoning_effort": settings.vision_effort} if reasoning else {"temperature": 0}
    t0 = time.perf_counter()
    resp = client.chat.completions.create(
        model=settings.vision_deployment,
        messages=[{"role": "system", "content": VISION_SYSTEM},
                  {"role": "user", "content": [{"type": "text", "text": text},
                                               {"type": "image_url", "image_url": {"url": url, "detail": "high"}}]}],
        max_completion_tokens=1500, **kw,
    )
    u = getattr(resp, "usage", None)
    return {"description": (resp.choices[0].message.content or "").strip(), "model": settings.vision_deployment,
            "prompt_version": PROMPT_VERSION,
            "input_tokens": int(getattr(u, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(u, "completion_tokens", 0) or 0),
            "seconds": round(time.perf_counter() - t0, 2)}


def describe(items: list[tuple[str, str]], store: Store, vision: str = "auto",
             concurrency: int | None = None) -> dict[str, dict]:
    """Descriptions for (asset_key, context) pairs. `vision`: "none" reads the cache only; "auto" and "all" call the
    model for anything not cached (the caller decides which pictures need one, see pipeline.py). Returns
    asset_key -> record, with "cached": True on hits and "called": True on new calls; failures are left out."""
    out: dict[str, dict] = {}
    todo: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key, ctx in items:
        if key in out or key in seen:
            continue
        seen.add(key)
        ck = cache_key(key)
        if store.exists(ck):
            out[key] = {**store.json(ck), "cached": True}
        elif vision != "none":
            todo.append((key, ctx))
    if not todo:
        return out
    client = _client()
    log.info("describing %d pictures with %s (%d cached)", len(todo), settings.vision_deployment, len(out))

    def work(item: tuple[str, str]) -> tuple[str, dict | None]:
        key, ctx = item
        try:
            rec = describe_one(client, store.get(key), ctx)
            store.put_json(cache_key(key), rec)
            return key, {**rec, "called": True}
        except Exception as e:  # noqa: BLE001 - one bad picture must not stop the ingest
            log.warning("vision call failed for %s: %s", key, e)
            return key, None

    with ThreadPoolExecutor(max_workers=concurrency or settings.vision_concurrency) as pool:
        for key, rec in pool.map(work, todo):
            if rec:
                out[key] = rec
    return out
