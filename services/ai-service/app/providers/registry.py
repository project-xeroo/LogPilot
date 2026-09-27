"""Provider selection + the JSON-task helper with retry and graceful fallback."""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Any

from app.providers.base import AIProvider, AIUnavailable, Message, ModelRole
from app.providers.guarded import GuardedProvider
from app.providers.heuristic import HeuristicProvider
from shared.config import settings

log = logging.getLogger("logpilot.ai")


def _build(kind: str) -> AIProvider:
    kind = kind.lower()
    if kind in ("openai_compatible", "ibm_bob", "cloud"):
        from app.providers.openai_compatible import OpenAICompatibleProvider

        return OpenAICompatibleProvider()
    if kind in ("mock", "heuristic", "offline"):
        return HeuristicProvider()
    raise RuntimeError(f"unknown provider kind '{kind}'")


@lru_cache
def get_provider() -> GuardedProvider:
    chat = _build(settings.ai_provider)
    # AI_EMBEDDING_PROVIDER lets embeddings point at a different backend than chat (e.g. a provider
    # with great chat models but no well-tuned retrieval embedding model provisioned). Empty = same as chat.
    embed_kind = (settings.ai_embedding_provider or settings.ai_provider).lower()
    embedding = chat if embed_kind == settings.ai_provider.lower() else _build(embed_kind)
    return GuardedProvider(chat, embedding)


def _extract_json(text: str) -> Any:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def run_json(task: str, messages: list[Message], role: ModelRole, context: dict, *, timeout: float | None = None,
             max_tokens: int = 3000) -> dict:
    """Call the model for a JSON result; one repair retry, then AIUnavailable so the caller can fall back."""
    p = get_provider()
    last: Exception | None = None
    for attempt in range(2):
        raw = p.complete(task=task, messages=messages, role=role, context=context, json_mode=True, max_tokens=max_tokens, timeout=timeout)
        try:
            data = _extract_json(raw)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError as exc:
            last = exc
        messages = messages + [Message("assistant", raw[:2000]), Message("user", "That was not valid JSON. Reply with ONLY the JSON object.")]
    raise AIUnavailable(f"model returned invalid JSON for {task}: {last}")


def run_text(task: str, messages: list[Message], role: ModelRole, context: dict, *, timeout: float | None = None, max_tokens: int = 1500) -> str:
    return get_provider().complete(task=task, messages=messages, role=role, context=context, max_tokens=max_tokens, timeout=timeout)


def heuristic_json(task: str, context: dict) -> dict:
    """Deterministic fallback used when the cloud model is unavailable or too slow."""
    from app.providers import composers

    return json.loads(composers.TASKS[task](context))
