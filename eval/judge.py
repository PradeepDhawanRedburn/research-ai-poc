"""The answer judge shared by `rag eval` and `agentic eval`: one prompt, one verdict scale, so scores are comparable.

Answer: correct = 1, partial = 0.5, incorrect = 0. The judge sees the question, the expected answer and the
system's answer. It should be a different (and ideally stronger) deployment than the one that wrote the answers.
"""
from __future__ import annotations

import json

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


def judge_with_openai(client, deployment: str, q: dict, got: str) -> dict:
    """Verdict through the plain Azure OpenAI client (used by `agentic eval`, which has no LangChain)."""
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
