"""Reading chunk records from either knowledge source without depending on field names."""
from __future__ import annotations


TEXT_FIELDS = ("page_chunk", "content_text", "chunk", "content", "text", "page_content", "merged_content")
SKIP_FIELDS = {"id", "chunk_id", "parent_id", "doc_id", "url", "metadata_storage_path", "metadata_storage_name",
               "source_file", "title", "date", "section", "caption", "figure", "kind", "assets", "has_facts",
               "image_path", "docKey", "rerankerScore", "activitySource"}


def chunk_text(c: dict) -> str:
    """The text of a chunk record whatever the field is called: a known name when present, else the longest string
    value that is not an id, url or file name. Azure's Blob pipeline and our index name the field differently."""
    for k in TEXT_FIELDS:
        v = c.get(k)
        if isinstance(v, str) and v.strip():
            return v
    best = ""
    for k, v in c.items():
        if k in SKIP_FIELDS or not isinstance(v, str) or k.startswith("@"):
            continue
        if len(v) > len(best) and not v.startswith(("http://", "https://")):
            best = v
    return best


def chunk_file(c: dict) -> str:
    """The report file a chunk came from: our field, Azure's storage name, or any value ending in .docx."""
    for k in ("source_file", "metadata_storage_name", "file_name", "filename"):
        v = c.get(k)
        if isinstance(v, str) and v:
            return v.rsplit("/", 1)[-1]
    for v in c.values():
        if isinstance(v, str) and v.lower().endswith(".docx"):
            return v.rsplit("/", 1)[-1]
    return ""
