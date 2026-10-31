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

PROMPT_VERSION = "v3"  # bump when VISION_SYSTEM or the JSON shape changes

VISION_SYSTEM = """You read one figure from a financial research report (equity or economics) and return JSON, so \
that questions about it (how many, what is printed, which one, what value) can be answered from your output alone, \
without seeing the picture. Be factual and exhaustive about what is visible; never invent numbers or text you cannot \
read; say "unreadable" where you cannot. Return exactly this JSON object:

{
 "kind": one of "chart", "table", "matrix", "screenshot", "photo", "product shot", "packaging", "advert", "map", \
"diagram", "infographic", "logo", "other",
 "about": one line saying what the figure is about (use the caption's name if given),
 "items": [ for photos, packaging, adverts, screenshots and maps: every distinct item from left to right (can, \
bottle, pack, person, logo, tile, button, site, label): {"name": ..., "printed_text": [every piece of text printed \
on or next to it, exactly as printed: brand, variant, flavour, size, price, claim, status label], "count": how \
many of this item are shown, "forms": {form such as "whole fruit", "slice", "wedge", "drawing", "logo": count} \
for things drawn on packaging, else {}} ],
 "text": [all other printed text in reading order: titles, axis labels, legend entries, buttons, banners, \
footers],
 "numbers": [ every number that has a label: {"label": what it refers to (e.g. "Wakefield employees", "Pepsi Max \
share Dec 2024", "latest CPI"), "value": the number as printed, "unit": "%", "£", "m", "" ...} ; for charts: the \
latest value of each series, notable peaks and troughs with their dates, every bar or slice ],
 "table": for a table or matrix drawn as a picture, a Markdown transcription including ticks and crosses with \
their colours, else "",
 "summary": 2 to 5 sentences: chart type, axes and units, series names and trend; or layout, colours, what is \
partly hidden, cut off or unreadable
}

Counts and printed text come first; do not summarise them away. Consider the whole picture, not only the item the \
caption names."""

USER_TEXT = ("Read this figure and return the JSON object described in the instructions.")


def render_description(s: dict) -> str:
    """The structured record as compact text for the chunk and the Markdown (one line per fact)."""
    if not isinstance(s, dict):
        return ""
    lines = []
    head = " · ".join(x for x in (str(s.get("kind") or "").strip(), str(s.get("about") or "").strip()) if x)
    if head:
        lines.append(head)
    for it in s.get("items") or []:
        if not isinstance(it, dict):
            continue
        bits = [str(it.get("name") or "item")]
        if it.get("count") not in (None, "", 0):
            bits[0] += f" ×{it['count']}"
        printed = [str(t) for t in (it.get("printed_text") or []) if str(t).strip()]
        if printed:
            bits.append("printed: " + " | ".join(printed))
        forms = it.get("forms") or {}
        if isinstance(forms, dict) and forms:
            bits.append("forms: " + ", ".join(f"{k} {v}" for k, v in forms.items()))
        lines.append("- " + "; ".join(bits))
    nums = [n for n in (s.get("numbers") or []) if isinstance(n, dict) and str(n.get("value", "")).strip()]
    if nums:
        lines.append("Numbers: " + "; ".join(f"{n.get('label', '')}: {n.get('value')}{n.get('unit', '') or ''}".strip()
                                             for n in nums))
    text = [str(t) for t in (s.get("text") or []) if str(t).strip()]
    if text:
        lines.append("Text in the picture: " + " | ".join(text))
    if str(s.get("table") or "").strip():
        lines.append(str(s["table"]).strip())
    if str(s.get("summary") or "").strip():
        lines.append(str(s["summary"]).strip())
    return "\n".join(lines)


def parse_structured(content: str) -> dict:
    """The model's JSON, tolerant of a code fence; {} when it is not JSON."""
    t = (content or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[4:] if t.lower().startswith("json") else t
    try:
        obj = json.loads(t)
    except ValueError:
        start, end = t.find("{"), t.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            obj = json.loads(t[start:end + 1])
        except ValueError:
            return {}
    return obj if isinstance(obj, dict) else {}


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
        max_completion_tokens=2500, response_format={"type": "json_object"}, **kw,
    )
    u = getattr(resp, "usage", None)
    raw = (resp.choices[0].message.content or "").strip()
    structured = parse_structured(raw)
    return {"description": render_description(structured) or raw, "structured": structured,
            "model": settings.vision_deployment, "prompt_version": PROMPT_VERSION,
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
