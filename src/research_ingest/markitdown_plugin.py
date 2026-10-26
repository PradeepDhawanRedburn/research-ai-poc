"""MarkItDown plugin: route .docx through the research extractor instead of mammoth.

MarkItDown stays the chassis (CLI, stream handling, other formats, future Azure routing);
this converter replaces only the Word step, so charts, tick/cross matrices and spanned
tables survive. Registered via the `markitdown.plugin` entry point in pyproject.toml, or
directly with `md.register_converter(ResearchDocxConverter())`.

Usage:
    md = MarkItDown(enable_plugins=True)
    result = md.convert("report.docx", assets_dir="out/assets")   # assets_dir optional
"""
from __future__ import annotations

import os
import tempfile
from typing import Any, BinaryIO

from markitdown import DocumentConverter, DocumentConverterResult, StreamInfo

from .extract import Extractor
from .render import render_document

__plugin_interface_version__ = 1

ACCEPTED_EXTENSIONS = [".docx"]
ACCEPTED_MIME_PREFIXES = ["application/vnd.openxmlformats-officedocument.wordprocessingml.document"]


class ResearchDocxConverter(DocumentConverter):
    def accepts(self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any) -> bool:
        ext = (stream_info.extension or "").lower()
        mime = (stream_info.mimetype or "").lower()
        return ext in ACCEPTED_EXTENSIONS or any(mime.startswith(p) for p in ACCEPTED_MIME_PREFIXES)

    def convert(self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any) -> DocumentConverterResult:
        doc_id = os.path.splitext(os.path.basename(stream_info.filename or stream_info.local_path or "document"))[0]
        assets_dir = kwargs.get("assets_dir")
        tmp_dir = None
        if assets_dir is None:
            tmp_dir = tempfile.mkdtemp(prefix="research_ingest_")
            assets_dir = os.path.join(tmp_dir, "assets")
        # the extractor works on a zip on disk; MarkItDown hands us a stream
        path = stream_info.local_path
        tmp_file = None
        if not path or not os.path.exists(path):
            fd, tmp_file = tempfile.mkstemp(suffix=".docx")
            with os.fdopen(fd, "wb") as fh:
                fh.write(file_stream.read())
            path = tmp_file
        try:
            doc = Extractor(path, assets_dir, doc_id).run()
        finally:
            if tmp_file:
                os.unlink(tmp_file)
        md = doc["metadata"]
        self.last_document = doc  # canonical model, for callers that want more than Markdown
        return DocumentConverterResult(markdown=render_document(doc), title=md.get("title"))


def register_converters(markitdown, **kwargs) -> None:
    # priority 0 and registered after the built-ins, so it is tried before the stock DocxConverter
    markitdown.register_converter(ResearchDocxConverter(), priority=0.0)
