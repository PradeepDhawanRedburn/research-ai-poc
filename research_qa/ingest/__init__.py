"""Ingest: raw Word in the store -> Markdown+, assets, facts, chunks, manifest."""
from .chunks import build_chunks
from .convert import Converted, convert
from .describe import describe
from .facts import facts_for, lookup
from .pipeline import ingest, ingest_one

__all__ = ["Converted", "build_chunks", "convert", "describe", "facts_for", "ingest", "ingest_one", "lookup"]
