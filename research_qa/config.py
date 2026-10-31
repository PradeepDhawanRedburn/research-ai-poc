"""Settings for the whole system, read from the environment and `.env` at the repo root.

One place for: the store (Blob container or local folder), the Azure OpenAI deployments, the search service and
Foundry project, the two arms and two modes of the comparison, and the price table for cost per question.
Names of Azure objects derive from one base name so a deployment can run several copies side by side.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:  # dotenv is optional: the environment may already be set
    pass

try:  # corporate TLS inspection: trust the Windows certificate store
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass


def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _openai_host(endpoint: str) -> str:
    """Azure AI Search calls the embedding model through the `<resource>.openai.azure.com` endpoint."""
    m = re.match(r"https://([^./]+)\.(?:services\.ai|cognitiveservices|openai)\.azure\.com", endpoint)
    return f"https://{m.group(1)}.openai.azure.com" if m else endpoint.rstrip("/")


# ----------------------------------------------------------------------------- arms and modes

@dataclass(frozen=True)
class Arm:
    """One input format and the Azure objects that serve it."""
    key: str            # word | markdown-plus
    label: str          # shown in the app
    short: str          # column label
    source: str         # blob (Azure indexes raw Word itself) | index (we push Markdown+ chunks)
    description: str

    @property
    def has_tools(self) -> bool:
        """Facts and pictures exist only for Markdown+; the Word arm is Azure's default pipeline, untouched."""
        return self.source == "index"


ARMS: dict[str, Arm] = {
    "word": Arm("word", "Word, default conversion", "Word", "blob",
                "The .docx files as they are, through the default pipeline of the retrieval engine: Azure AI "
                "Search's Blob ingestion (cracking, chunking, embeddings, image verbalisation by the service) or, "
                "with the LangChain engine, the stock MarkItDown conversion. No custom code either way."),
    "markdown-plus": Arm("markdown-plus", "Markdown+", "Markdown+", "index",
                         "Word converted with MarkItDown and the research plugin: charts read as numbers, matrices "
                         "as states, pictures described once; chunks, facts and assets pushed from the store."),
}
MODES: dict[str, str] = {
    "agent": "Foundry agent: plans queries, calls the knowledge base, may call tools, cites passages",
    "direct": "One knowledge-base retrieval and one chat completion; no planning, no tools",
}
DEFAULT_ARMS = list(ARMS)
DEFAULT_MODES = ["agent"]


def combo_key(arm: str, mode: str) -> str:
    return f"{arm}/{mode}"


def combo_label(arm: str, mode: str) -> str:
    return f"{ARMS[arm].label} · {mode}"


# ----------------------------------------------------------------------------- names of Azure objects

@dataclass(frozen=True)
class Names:
    arm: str
    index: str              # Markdown+ only; the Word arm's index is created by the service
    knowledge_source: str
    knowledge_base: str
    connection: str
    agent: str
    mcp_endpoint: str


# ----------------------------------------------------------------------------- settings

@dataclass(frozen=True)
class Settings:
    # --- store: a Blob container (Entra ID or connection string) or a local folder for development and tests
    storage_account_url: str = _env("STORAGE_ACCOUNT_URL").rstrip("/")        # https://<account>.blob.core.windows.net
    storage_connection_string: str = _env("STORAGE_CONNECTION_STRING")
    # ARM id of the storage account; lets the search service read raw/ with its managed identity (no connection string)
    storage_resource_id: str = _env("STORAGE_RESOURCE_ID")
    storage_container: str = _env("STORAGE_CONTAINER", "research")
    store_local_dir: str = _env("STORE_LOCAL_DIR")                            # set -> local folder instead of Blob
    cache_dir: Path = Path(_env("CACHE_DIR", str(ROOT / ".cache")))          # downloaded assets for libraries that need a path

    # --- Azure OpenAI (Foundry) deployments
    openai_endpoint: str = _env("AZURE_OPENAI_ENDPOINT").rstrip("/")
    openai_api_key: str = _env("AZURE_OPENAI_API_KEY")
    api_version: str = _env("AZURE_OPENAI_API_VERSION", "2024-10-21")
    embedding_endpoint: str = _env("AZURE_EMBEDDING_ENDPOINT") or _env("AZURE_OPENAI_ENDPOINT").rstrip("/")
    embedding_api_key: str = _env("AZURE_EMBEDDING_API_KEY") or _env("AZURE_OPENAI_API_KEY")
    embedding_deployment: str = _env("AZURE_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")
    embedding_model: str = _env("AZURE_EMBEDDING_MODEL", "text-embedding-3-large")
    embedding_dimensions: int = int(_env("AZURE_EMBEDDING_DIMENSIONS", "3072"))
    answer_model: str = _env("ANSWER_MODEL") or _env("AGENT_MODEL", "gpt-5-mini")      # agent and direct mode
    reasoning_effort: str = _env("AGENT_REASONING_EFFORT", "low")
    vision_deployment: str = _env("VISION_DEPLOYMENT") or _env("AGENT_MODEL", "gpt-5-mini")  # ingest + inspect_figure
    vision_effort: str = _env("VISION_EFFORT", "medium")
    vision_concurrency: int = int(_env("VISION_CONCURRENCY", "4"))
    judge_deployment: str = _env("JUDGE_DEPLOYMENT") or _env("ANSWER_MODEL") or _env("AGENT_MODEL", "gpt-5-mini")

    # --- retrieval engine: "azure" (AI Search knowledge bases + Foundry agents) or "langchain" (Chroma + LangChain,
    # runs on any tier; the Word arm is then the stock MarkItDown conversion, with no plugin)
    retrieval_backend: str = _env("RETRIEVAL_BACKEND", "azure").lower()
    chroma_dir: Path = Path(_env("CHROMA_DIR", str(Path(_env("CACHE_DIR", str(ROOT / ".cache"))) / "chroma")))

    # --- Azure AI Search and Foundry project
    search_endpoint: str = _env("AZURE_SEARCH_ENDPOINT").rstrip("/")
    search_openai_endpoint: str = _env("AZURE_SEARCH_OPENAI_ENDPOINT") or _openai_host(_env("AZURE_OPENAI_ENDPOINT"))
    search_api_version: str = _env("AZURE_SEARCH_API_VERSION", "2026-08-01-preview")
    project_endpoint: str = _env("PROJECT_ENDPOINT")
    project_resource_id: str = _env("PROJECT_RESOURCE_ID")
    base_name: str = _env("BASE_NAME", "research-reports")
    max_docs: int = int(_env("MAX_DOCS", "12"))                  # passages per knowledge-base retrieval, both arms
    chunk_chars: int = int(_env("CHUNK_CHARS", "1800"))

    # --- prices for cost per question: prices.yaml at the repo root (per deployment, per million tokens)
    prices_file: Path = Path(_env("PRICES_FILE", str(ROOT / "prices.yaml")))
    currency: str = _env("CURRENCY", "GBP")

    def names(self, arm: str) -> Names:
        if arm not in ARMS:
            raise ValueError(f"arm must be one of {list(ARMS)}, got {arm!r}")
        kb = f"{self.base_name}-kb-{arm}"
        return Names(
            arm=arm,
            index=f"{self.base_name}-{arm}",
            knowledge_source=f"{self.base_name}-ks-{arm}",
            knowledge_base=kb,
            connection=f"{self.base_name}-conn-{arm}".replace("-", ""),  # connection names: letters and digits
            agent=f"{self.base_name}-agent-{arm}",
            mcp_endpoint=f"{self.search_endpoint}/knowledgebases/{kb}/mcp?api-version={self.search_api_version}",
        )

    @property
    def store_is_local(self) -> bool:
        return bool(self.store_local_dir)

    def require(self, *names: str) -> None:
        env = {"search_endpoint": "AZURE_SEARCH_ENDPOINT", "project_endpoint": "PROJECT_ENDPOINT",
               "project_resource_id": "PROJECT_RESOURCE_ID", "openai_endpoint": "AZURE_OPENAI_ENDPOINT",
               "storage_account_url": "STORAGE_ACCOUNT_URL or STORAGE_CONNECTION_STRING or STORE_LOCAL_DIR"}
        missing = [env.get(n, n) for n in names if not getattr(self, n)]
        if missing:
            raise RuntimeError("missing settings: " + ", ".join(missing) + " (see .env.example)")


settings = Settings()
