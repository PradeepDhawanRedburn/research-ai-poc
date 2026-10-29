"""Settings for the multimodal RAG, read from the environment (and `.env` at the repo root)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

try:  # corporate TLS inspection: trust the Windows certificate store, as the README does for uv
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

SOURCES = ("docx", "markdown")


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    # Azure AI Foundry / Azure OpenAI. The embedding deployment may live in a different resource.
    endpoint: str | None = _env("AZURE_OPENAI_ENDPOINT")
    api_key: str | None = _env("AZURE_OPENAI_API_KEY")  # unset -> Entra ID (DefaultAzureCredential)
    api_version: str = _env("AZURE_OPENAI_API_VERSION", "2024-10-21")
    chat_deployment: str = _env("AZURE_CHAT_DEPLOYMENT", "gpt-4.1")
    vision_deployment: str = _env("AZURE_VISION_DEPLOYMENT") or _env("AZURE_CHAT_DEPLOYMENT", "gpt-4.1")
    embedding_deployment: str = _env("AZURE_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")
    embedding_endpoint: str | None = _env("AZURE_EMBEDDING_ENDPOINT") or _env("AZURE_OPENAI_ENDPOINT")
    embedding_api_key: str | None = _env("AZURE_EMBEDDING_API_KEY") or _env("AZURE_OPENAI_API_KEY")
    embedding_dimensions: int | None = int(_env("AZURE_EMBEDDING_DIMENSIONS")) if _env("AZURE_EMBEDDING_DIMENSIONS") else None

    # Local storage
    chroma_dir: Path = Path(_env("RAG_CHROMA_DIR", str(ROOT / ".chroma")))
    cache_dir: Path = Path(_env("RAG_CACHE_DIR", str(ROOT / ".rag_cache")))

    # Inputs
    docx_dir: Path = ROOT / "data" / "raw"
    markdown_dir: Path = ROOT / "out" / "A+"

    # Chunking and retrieval
    chunk_chars: int = int(_env("RAG_CHUNK_CHARS", "1800"))
    top_k: int = int(_env("RAG_TOP_K", "8"))
    max_answer_images: int = int(_env("RAG_MAX_ANSWER_IMAGES", "3"))
    vision_concurrency: int = int(_env("RAG_VISION_CONCURRENCY", "4"))

    def collection(self, source: str) -> str:
        if source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}, got {source!r}")
        return f"research_{source}"


settings = Settings()
