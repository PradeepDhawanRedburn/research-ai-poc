"""The agent's picture tool: a vision model looks at the rasterised figure behind a chunk and answers a question.

The knowledge base only returns text, so a question such as "how many oranges are in the Fanta picture" cannot be
answered from the written description. The agent calls `inspect_figure`; this module runs it on our side (function
tools are executed by the client) and hands the answer back to the agent.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from openai import AzureOpenAI

from .config import settings
from .search_setup import credential, figure_chunk

SYSTEM = (
    "You look at a picture from a financial research report and answer one question about it. Describe only what is "
    "visible. When counting, first list every instance you find (where it is, what form it takes: whole fruit, "
    "slice, wedge, drawing, logo), then give the count per item (can, panel) and the total. Include partly hidden "
    "or cut ones and say which they are. Consider the whole picture: if the question names only part of it (for "
    "example 'the Fanta image' when other brands are shown), answer for that part and also report the rest "
    "separately. Read chart labels and values exactly as printed. If something is unclear or cut off, say so "
    "instead of guessing."
)

TOOL_NAME = "inspect_figure"
TOOL_DESCRIPTION = (
    "Look at the picture behind a figure chunk and answer a question about what it shows: counting objects, "
    "colours, labels, layout, or values read from a chart. Use it when the answer depends on the picture and the "
    "written text of the chunk does not already give it exactly. chunk_id is the `id` field inside the retrieved "
    "content (for example RA02752-00145) of a chunk whose kind is figure or chart."
)
TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "chunk_id": {"type": "string", "description": "id of the figure chunk, e.g. RA02752-00145"},
        "question": {"type": "string", "description": "precise question about the picture, e.g. how many oranges are shown"},
    },
    "required": ["chunk_id", "question"],
    "additionalProperties": False,
}


@dataclass
class FigureLook:
    chunk_id: str
    question: str
    answer: str
    paths: list[str] = field(default_factory=list)
    seconds: float = 0.0
    model: str = ""
    input_tokens: int = 0   # the picture(s) and the caption; counted so agentic and RAG tokens compare like for like
    output_tokens: int = 0


def _client() -> AzureOpenAI:
    kw = {"azure_endpoint": settings.chat_endpoint, "api_version": settings.api_version, "max_retries": 4}
    if settings.chat_api_key:
        kw["api_key"] = settings.chat_api_key
    else:
        from azure.identity import get_bearer_token_provider

        kw["azure_ad_token_provider"] = get_bearer_token_provider(
            credential(), "https://cognitiveservices.azure.com/.default")
    return AzureOpenAI(**kw)


def inspect_figure(source: str, chunk_id: str, question: str) -> FigureLook:
    from rag.images import data_url  # same PNG-to-data-URL conversion the plain RAG uses

    t0 = time.perf_counter()
    chunk = figure_chunk(source, chunk_id.strip())
    paths = [p for p in chunk.get("paths", []) if Path(p).exists()][:3]
    if not chunk:
        return FigureLook(chunk_id, question, f"No chunk with id {chunk_id!r} in the index; use an id from the "
                          "knowledge base results.", seconds=time.perf_counter() - t0)
    if not paths:
        return FigureLook(chunk_id, question, f"Chunk {chunk_id} has no picture ({chunk.get('kind')}); answer from "
                          "its text.", seconds=time.perf_counter() - t0)
    content = [{"type": "text", "text": f"Caption and description of the figure:\n{chunk['text'][:1500]}\n\n"
                                        f"Question: {question}"}]
    content += [{"type": "image_url", "image_url": {"url": data_url(Path(p)), "detail": "high"}} for p in paths]
    reasoning = settings.vision_deployment.startswith(("gpt-5", "o"))  # reasoning models: no temperature
    kw = {"reasoning_effort": settings.vision_effort} if reasoning else {"temperature": 0}
    resp = _client().chat.completions.create(
        model=settings.vision_deployment,
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}],
        max_completion_tokens=2000, **kw,
    )
    u = getattr(resp, "usage", None)
    return FigureLook(chunk_id, question, (resp.choices[0].message.content or "").strip(), paths,
                      time.perf_counter() - t0, settings.vision_deployment,
                      int(getattr(u, "prompt_tokens", 0) or 0), int(getattr(u, "completion_tokens", 0) or 0))
