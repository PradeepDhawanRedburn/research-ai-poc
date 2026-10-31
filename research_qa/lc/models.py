"""Azure OpenAI deployments wrapped as LangChain models (chat and embeddings), key or Entra ID.

Foundry serves OpenAI-family deployments through the Azure OpenAI API, so `langchain-openai` covers a classic
Azure OpenAI resource (https://<name>.openai.azure.com) and a Foundry resource (https://<name>.services.ai.azure.com
or .cognitiveservices.azure.com) alike.
"""
from __future__ import annotations

from functools import lru_cache

from ..config import settings

COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"


def _auth(api_key: str | None) -> dict:
    if api_key:
        return {"api_key": api_key}
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    provider = get_bearer_token_provider(DefaultAzureCredential(process_timeout=60), COGNITIVE_SCOPE)
    return {"azure_ad_token_provider": provider}


def is_reasoning(deployment: str) -> bool:
    return deployment.startswith(("gpt-5", "o"))


@lru_cache(maxsize=None)
def chat_model(deployment: str | None = None, max_tokens: int = 1500):
    """AzureChatOpenAI on the answer model. Reasoning deployments (gpt-5*, o*) take reasoning_effort and no
    temperature; others run at temperature 0."""
    from langchain_openai import AzureChatOpenAI

    settings.require("openai_endpoint")
    name = deployment or settings.answer_model
    kw: dict = {"azure_endpoint": settings.openai_endpoint, "azure_deployment": name,
                "api_version": settings.api_version, "max_retries": 6, **_auth(settings.openai_api_key)}
    if is_reasoning(name):
        kw["reasoning_effort"] = settings.reasoning_effort
        kw["max_completion_tokens"] = max_tokens
    else:
        kw["temperature"] = 0
        kw["max_tokens"] = max_tokens
    return AzureChatOpenAI(**kw)


@lru_cache(maxsize=None)
def embeddings():
    from langchain_openai import AzureOpenAIEmbeddings

    endpoint = settings.embedding_endpoint or settings.openai_endpoint
    if not endpoint:
        raise RuntimeError("missing settings: AZURE_OPENAI_ENDPOINT (see .env.example)")
    extra = {"dimensions": settings.embedding_dimensions} if settings.embedding_dimensions else {}
    return AzureOpenAIEmbeddings(
        azure_endpoint=endpoint, azure_deployment=settings.embedding_deployment, api_version=settings.api_version,
        chunk_size=256, max_retries=6, **extra, **_auth(settings.embedding_api_key or settings.openai_api_key),
    )


def usage_of(message) -> tuple[int, int, int]:
    """(input, output, reasoning) tokens of an AIMessage, 0 when the provider gave none."""
    u = getattr(message, "usage_metadata", None) or {}
    details = u.get("output_token_details") or {}
    return int(u.get("input_tokens", 0) or 0), int(u.get("output_tokens", 0) or 0), int(details.get("reasoning", 0) or 0)
