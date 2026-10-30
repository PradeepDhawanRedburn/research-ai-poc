"""Settings for the agentic retrieval solution, read from the environment (and `.env` at the repo root).

Variable names follow the Azure AI Search tutorial. Values that can be derived from other settings are, so the
same `.env` that serves `rag/` needs only the three Azure resource identifiers below.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

try:  # corporate TLS inspection: trust the Windows certificate store
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass


SOURCES = ("docx", "markdown")


def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


@dataclass(frozen=True)
class Names:
    """The Azure objects that belong to one solution."""
    source: str
    index: str
    knowledge_source: str
    knowledge_base: str
    connection: str
    agent: str
    mcp_endpoint: str


def _openai_host(endpoint: str) -> str:
    """Azure AI Search calls the embedding model through the `<resource>.openai.azure.com` endpoint."""
    m = re.match(r"https://([^./]+)\.(?:services\.ai|cognitiveservices|openai)\.azure\.com", endpoint)
    return f"https://{m.group(1)}.openai.azure.com" if m else endpoint.rstrip("/")


@dataclass(frozen=True)
class Settings:
    search_endpoint: str = _env("AZURE_SEARCH_ENDPOINT").rstrip("/")
    project_endpoint: str = _env("PROJECT_ENDPOINT")
    project_resource_id: str = _env("PROJECT_RESOURCE_ID")
    # The search service vectorizes queries with this endpoint (<resource>.openai.azure.com)
    openai_endpoint: str = _env("AZURE_SEARCH_OPENAI_ENDPOINT") or _openai_host(_env("AZURE_OPENAI_ENDPOINT"))
    # Used only to embed chunks at ingestion; no key -> Entra ID (az login)
    embedding_endpoint: str = _env("AZURE_EMBEDDING_ENDPOINT") or _env("AZURE_OPENAI_ENDPOINT")
    embedding_api_key: str = _env("AZURE_EMBEDDING_API_KEY") or _env("AZURE_OPENAI_API_KEY")
    api_version: str = _env("AZURE_OPENAI_API_VERSION", "2024-10-21")
    embedding_deployment: str = _env("AZURE_OPENAI_EMBEDDING_DEPLOYMENT") or _env("AZURE_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")
    embedding_model: str = _env("AZURE_OPENAI_EMBEDDING_MODEL", "text-embedding-3-large")
    agent_model: str = _env("AGENT_MODEL", "gpt-5-mini")
    # gpt-5-mini reasons before every step; "low" keeps answers quick. minimal | low | medium | high
    reasoning_effort: str = _env("AGENT_REASONING_EFFORT", "low")
    # Vision model behind the agent's inspect_figure tool (must accept images). Same model as the agent by default:
    # on the Fanta can picture gpt-5-mini and gpt-5.5 counted the oranges correctly, gpt-4.1 did not.
    chat_endpoint: str = _env("AZURE_OPENAI_ENDPOINT")
    chat_api_key: str = _env("AZURE_OPENAI_API_KEY")
    vision_deployment: str = _env("AGENTIC_VISION_DEPLOYMENT") or _env("AGENT_MODEL", "gpt-5-mini")
    # Counting is unstable at low effort (3 to 5 for the same picture) and steady at medium, at about 15 s a picture
    vision_effort: str = _env("AGENTIC_VISION_EFFORT", "medium")
    # Most passages the knowledge base hands to the agent per retrieval (stored on the knowledge base)
    max_docs: int = int(_env("AGENTIC_MAX_DOCS", "12"))
    # Base names; each solution (docx, markdown) gets its own set, suffixed with the source name
    agent_name: str = _env("AGENT_NAME", "research-reports-agent")
    connection_name: str = _env("PROJECT_CONNECTION_NAME", "researchknowledge")
    index_name: str = _env("AZURE_SEARCH_INDEX", "research-reports")
    knowledge_source_name: str = _env("AZURE_SEARCH_KNOWLEDGE_SOURCE_NAME", "research-reports-source")
    knowledge_base_name: str = _env("AZURE_SEARCH_AGENT_NAME", "research-reports-base")

    docx_dir: Path = ROOT / "data" / "raw"
    markdown_dir: Path = ROOT / "out" / "A+"

    def names(self, source: str) -> "Names":
        if source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}, got {source!r}")
        kb = f"{self.knowledge_base_name}-{source}"
        return Names(
            source=source,
            index=f"{self.index_name}-{source}",
            knowledge_source=f"{self.knowledge_source_name}-{source}",
            knowledge_base=kb,
            connection=f"{self.connection_name}-{source}",
            agent=f"{self.agent_name}-{source}",
            mcp_endpoint=f"{self.search_endpoint}/knowledgebases/{kb}/mcp?api-version=2026-08-01-preview",
        )

    @property
    def chunk_chars(self) -> int:
        """Chunk size used at ingestion; the same variable as rag/ (read late so the CLI can override it)."""
        return int(os.environ.get("RAG_CHUNK_CHARS") or 1800)

    def resource_parts(self) -> dict[str, str]:
        """Split PROJECT_RESOURCE_ID into subscription, resource group, account and project names."""
        from azure.mgmt.core.tools import parse_resource_id

        p = parse_resource_id(self.project_resource_id)
        return {"subscription": p["subscription"], "resource_group": p["resource_group"],
                "account": p["name"], "project": p["child_name_1"]}

    def require(self, *names: str) -> None:
        missing = [n for n in names if not getattr(self, n)]
        if missing:
            env = {"search_endpoint": "AZURE_SEARCH_ENDPOINT", "project_endpoint": "PROJECT_ENDPOINT",
                   "project_resource_id": "PROJECT_RESOURCE_ID", "openai_endpoint": "AZURE_OPENAI_ENDPOINT"}
            raise RuntimeError("missing settings: " + ", ".join(env.get(n, n) for n in missing)
                               + " (see the Agentic retrieval section of .env.example)")


settings = Settings()
