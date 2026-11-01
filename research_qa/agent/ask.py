"""One entry point for any arm in any mode, and a parallel runner for the app and the evaluation."""
from __future__ import annotations

import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Callable

from ..config import ARMS, MODES, canon_mode, combo_label, settings
from ..schema import Answer


def label(arm: str, mode: str) -> str:
    return combo_label(arm, mode)


def ask(question: str, arm: str, mode: str = "agentic", use_tools: bool = True, chat=None,
        max_docs: int | None = None, principals=None, on_token=None) -> Answer:
    """`chat` is an existing Chat (foundry.Chat or lc.answer.Chat, matching RETRIEVAL_BACKEND) to continue a
    conversation; otherwise a fresh one is made. `principals` is the caller's identity for access control
    (default PRINCIPALS from .env). The Foundry agent calls its knowledge base itself over MCP and cannot carry a
    per-user filter, so under the Azure engine only single-pass mode enforces access; see research_qa/acl.py."""
    mode = canon_mode(mode)
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}")
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    if settings.retrieval_backend == "langchain":  # Chroma + LangChain engine, same arms, modes and Answer
        from ..lc import answer as lc

        if mode == "single-pass":
            return lc.ask_single_pass(question, arm, max_docs, principals=principals, on_token=on_token)
        return (chat or lc.Chat(arm, principals=principals)).ask(question, use_tools=use_tools, max_docs=max_docs)
    if mode == "single-pass":
        from .single_pass import ask_single_pass

        return ask_single_pass(question, arm, max_docs, principals=principals, on_token=on_token)
    from .foundry import Chat

    return (chat or Chat(arm)).ask(question, use_tools=use_tools)


def run_parallel(tasks: dict[str, Callable[[], Answer]],
                 on_done: Callable[[str, Answer | None, str | None], None],
                 on_tick: Callable[[dict[str, float]], None] | None = None, tick: float = 0.5) -> None:
    """Run every task in its own thread and report each the moment it finishes.

    `on_done(key, answer, error)` runs on the calling thread (Streamlit widgets may only be drawn there) with an
    Answer or an error string. `on_tick({key: seconds_running})` runs every `tick` seconds for tasks still running.
    No task waits for another.
    """
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, len(tasks))) as pool:
        futures: dict[Future, str] = {pool.submit(fn): key for key, fn in tasks.items()}
        pending = set(futures)
        while pending:
            done, pending = wait(pending, timeout=tick, return_when=FIRST_COMPLETED)
            for fut in done:
                key = futures[fut]
                try:
                    on_done(key, fut.result(), None)
                except Exception as e:  # noqa: BLE001 - the tile shows the error, the others carry on
                    on_done(key, None, f"{type(e).__name__}: {e}")
            if pending and on_tick:
                now = time.perf_counter() - started
                on_tick({futures[f]: now for f in pending})
