"""Azure clients shared by search and agent: credential, search clients, Azure OpenAI client, embeddings, REST.

Every Azure import lives inside a function so the pure parts of the package (tools, citation mapping, the
evaluation report) import without the Azure SDKs installed.
"""
from __future__ import annotations

from ..config import settings

SEARCH_SCOPE = "https://search.azure.com/.default"
COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"


def credential():
    from azure.identity import DefaultAzureCredential

    # az can be slow to hand out a token; the default 10 s timeout makes the first call fail now and then
    return DefaultAzureCredential(process_timeout=60)


def bearer(scope: str) -> str:
    from azure.identity import get_bearer_token_provider

    return get_bearer_token_provider(credential(), scope)()


def index_client():
    from azure.search.documents.indexes import SearchIndexClient

    settings.require("search_endpoint")
    return SearchIndexClient(endpoint=settings.search_endpoint, credential=credential())


def search_client(index: str):
    from azure.search.documents import SearchClient

    settings.require("search_endpoint")
    return SearchClient(endpoint=settings.search_endpoint, index_name=index, credential=credential())


def openai_client(endpoint: str | None = None, api_key: str | None = None):
    """Azure OpenAI client: API key when given, else Entra ID."""
    from openai import AzureOpenAI

    endpoint = endpoint or settings.openai_endpoint
    api_key = settings.openai_api_key if api_key is None and endpoint == settings.openai_endpoint else api_key
    settings.require("openai_endpoint") if not endpoint else None
    kw = {"azure_endpoint": endpoint, "api_version": settings.api_version, "max_retries": 4}
    if api_key:
        kw["api_key"] = api_key
    else:
        from azure.identity import get_bearer_token_provider

        kw["azure_ad_token_provider"] = get_bearer_token_provider(credential(), COGNITIVE_SCOPE)
    return AzureOpenAI(**kw)


def embed(texts: list[str], batch: int = 64) -> list[list[float]]:
    client = openai_client(settings.embedding_endpoint, settings.embedding_api_key)
    out: list[list[float]] = []
    for i in range(0, len(texts), batch):
        resp = client.embeddings.create(model=settings.embedding_deployment, input=texts[i:i + batch])
        out.extend(d.embedding for d in resp.data)
    return out


def search_rest(method: str, path: str, body: dict | None = None, timeout: int = 120) -> dict:
    """Call the search service REST API directly, for operations the installed SDK may not expose yet.
    `path` is relative to the service, e.g. 'knowledgesources/name'."""
    import requests

    settings.require("search_endpoint")
    url = f"{settings.search_endpoint}/{path}"
    sep = "&" if "?" in url else "?"
    url += f"{sep}api-version={settings.search_api_version}"
    resp = requests.request(method, url, json=body, timeout=timeout,
                            headers={"Authorization": f"Bearer {bearer(SEARCH_SCOPE)}",
                                     "Content-Type": "application/json"})
    if not resp.ok:
        raise RuntimeError(f"{method} {path} failed: {resp.status_code} {resp.text[:800]}")
    return resp.json() if resp.content else {}
