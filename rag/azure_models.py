"""Chat, vision and embedding models deployed in Azure AI Foundry, wrapped as LangChain models.

Foundry serves OpenAI-family deployments through the Azure OpenAI API, so `langchain-openai` covers both a
classic Azure OpenAI resource (https://<name>.openai.azure.com) and a Foundry resource
(https://<name>.cognitiveservices.azure.com or https://<name>.services.ai.azure.com).
"""
from __future__ import annotations

from functools import lru_cache

from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings

from .config import settings


def _auth(api_key: str | None) -> dict:
    if api_key:
        return {"api_key": api_key}
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    provider = get_bearer_token_provider(DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")
    return {"azure_ad_token_provider": provider}


def _require(value: str | None, name: str) -> str:
    if not value:
        raise RuntimeError(f"{name} is not set; copy .env.example to .env and fill it in")
    return value


@lru_cache(maxsize=None)
def chat_model(deployment: str | None = None, max_tokens: int = 1500) -> AzureChatOpenAI:
    return AzureChatOpenAI(
        azure_endpoint=_require(settings.endpoint, "AZURE_OPENAI_ENDPOINT"),
        azure_deployment=deployment or settings.chat_deployment,
        api_version=settings.api_version,
        temperature=0,
        max_tokens=max_tokens,
        max_retries=6,
        **_auth(settings.api_key),
    )


def vision_model() -> AzureChatOpenAI:
    return chat_model(settings.vision_deployment, max_tokens=900)


@lru_cache(maxsize=None)
def embeddings() -> AzureOpenAIEmbeddings:
    extra = {"dimensions": settings.embedding_dimensions} if settings.embedding_dimensions else {}
    return AzureOpenAIEmbeddings(
        azure_endpoint=_require(settings.embedding_endpoint, "AZURE_EMBEDDING_ENDPOINT / AZURE_OPENAI_ENDPOINT"),
        azure_deployment=settings.embedding_deployment,
        api_version=settings.api_version,
        chunk_size=256,  # inputs per request
        max_retries=6,
        **extra,
        **_auth(settings.embedding_api_key),
    )
