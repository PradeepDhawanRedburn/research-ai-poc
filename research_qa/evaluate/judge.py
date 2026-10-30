"""The answer judge: one prompt, one verdict scale, for every arm and mode, so scores are comparable.

Answer: correct = 1, partial = 0.5, incorrect = 0. The judge sees the question, the expected answer and the
system's answer. JUDGE_DEPLOYMENT should be a different (and ideally stronger) deployment than the one that answers.
"""
from __future__ import annotations

import json

from ..config import settings

JUDGE_SYSTEM = """You grade answers from a document QA system against an expected answer.
correct: states the expected facts (numbers may differ only in rounding or formatting; extra correct detail is fine).
partial: some expected facts present, some missing or wrong.
incorrect: wrong, missing, or claims the documents do not say when they do.
For an unanswerable question the expected answer says the documents do not cover it; then \
"correct" means the system declined, and any made-up answer is "incorrect".
Judge only against the Expected text. If the expected answer states a fact (including "No", "None", "Only X" or \
"all red crosses"), a system answer that declines or says the documents do not say is "incorrect"; never read an \
expected "None" or "No" as "the documents do not say"."""

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["correct", "partial", "incorrect"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}

SCORE = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}


def judge_user_text(q: dict, got: str) -> str:
    return (f"Question type: {q['type']}\nQuestion: {q['question']}\nExpected: {q.get('answer', '')}\n"
            f"System answer: {got}")


def openai_client():
    """Azure OpenAI client: API key when set, otherwise an Entra ID token (az login / managed identity)."""
    from openai import AzureOpenAI

    settings.require("openai_endpoint")
    kw = {"azure_endpoint": settings.openai_endpoint, "api_version": settings.api_version, "max_retries": 4}
    if settings.openai_api_key:
        kw["api_key"] = settings.openai_api_key
    else:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider

        kw["azure_ad_token_provider"] = get_bearer_token_provider(
            DefaultAzureCredential(process_timeout=60), "https://cognitiveservices.azure.com/.default")
    return AzureOpenAI(**kw)


def judge_with_openai(client, deployment: str, q: dict, got: str) -> dict:
    kw = {} if deployment.startswith(("gpt-5", "o")) else {"temperature": 0}  # reasoning models take no temperature
    resp = client.chat.completions.create(
        model=deployment,
        messages=[{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": judge_user_text(q, got)}],
        response_format={"type": "json_schema",
                         "json_schema": {"name": "verdict", "schema": VERDICT_SCHEMA, "strict": True}},
        max_completion_tokens=400, **kw,
    )
    try:
        v = json.loads(resp.choices[0].message.content or "{}")
    except ValueError:
        v = {}
    return {"verdict": v.get("verdict", "incorrect"), "reason": v.get("reason", "judge returned no verdict")}


def default_judge():
    """judge(q, answer_text) -> {"verdict", "reason"} with the configured deployment."""
    client = openai_client()
    return lambda q, got: judge_with_openai(client, settings.judge_deployment, q, got)
