"""Offline provider (`AI_PROVIDER=mock`): deterministic embeddings + rule-based composition.

It exists so the whole agent works end-to-end with no API key and no network (CI, demos, air-gapped
evaluation). It is NOT a language model: `complete()` ignores the prompt text and composes an answer
from the structured `context` the caller supplies - the same context a real cloud model is given
inside its prompt - so switching to a cloud provider changes prose quality, not behaviour.

Embeddings: signed feature hashing over word unigrams, bigrams, character trigrams and a small
concept lexicon (timeout/refused/exhausted/...), L2-normalised. Semantically close error messages
land close together; unrelated ones do not.
"""
from __future__ import annotations

import re
import zlib
from collections.abc import Iterator
from typing import Any

import numpy as np

from app.providers import composers
from app.providers.base import Message, ModelRole
from shared.config import settings

_STOP = set(
    "a an the of to for in on at by with from and or is are was were be been it its this that these those as into over "
    "under via not no num ts uuid hex id ip while after before when during than then so if".split()
)
_CONCEPTS: dict[str, set[str]] = {
    "c_timeout": {"timeout", "timedout", "timed", "deadline", "expired", "exceeded", "slow", "latency", "elapsed"},
    "c_refused": {"refused", "rejected", "unreachable", "unavailable", "down", "offline", "reset", "closed", "broken", "dropped", "lost", "failed"},
    "c_exhaust": {"exhausted", "saturated", "limit", "maxed", "max", "full", "pool", "capacity", "overflow", "backlog", "queue", "starved", "no", "available"},
    "c_conn": {"connection", "connections", "connect", "connecting", "connected", "socket", "conn", "sockets"},
    "c_memory": {"memory", "oom", "heap", "outofmemory", "leak", "gc", "alloc"},
    "c_auth": {"unauthorized", "forbidden", "denied", "authentication", "auth", "credential", "credentials", "token", "permission"},
    "c_disk": {"disk", "space", "storage", "enospc", "filesystem", "volume"},
    "c_db": {"database", "db", "postgres", "postgresql", "sql", "query", "deadlock", "mysql", "transaction", "commit", "rollback"},
    "c_cache": {"redis", "cache", "memcached", "session"},
    "c_http5": {"500", "502", "503", "504", "gateway", "upstream", "internal"},
    "c_null": {"null", "nil", "none", "undefined", "npe", "nullpointer"},
    "c_payment": {"payment", "charge", "card", "checkout", "billing", "invoice"},
}
_WORD_TO_CONCEPT = {w: c for c, ws in _CONCEPTS.items() for w in ws}
_TOK = re.compile(r"[a-z][a-z0-9]{1,}")


def _tokens(text: str) -> list[str]:
    text = re.sub(r"<[a-z]+>", " ", text.lower())
    text = text.replace("_", " ").replace("-", " ")
    return [t for t in _TOK.findall(text) if t not in _STOP]


def _slot(feature: str, dim: int) -> tuple[int, float]:
    h = zlib.crc32(feature.encode())
    return h % dim, 1.0 if (h >> 31) & 1 else -1.0


def embed_text(text: str, dim: int | None = None) -> list[float]:
    dim = dim or settings.embedding_dim
    v = np.zeros(dim, dtype=np.float32)
    toks = _tokens(text)
    feats: list[tuple[str, float]] = []
    for i, t in enumerate(toks):
        feats.append((f"w:{t}", 1.0))
        c = _WORD_TO_CONCEPT.get(t)
        if c:
            feats.append((c, 2.2))
        if i + 1 < len(toks):
            feats.append((f"b:{t}_{toks[i + 1]}", 0.6))
        if len(t) > 4:
            padded = f"#{t}#"
            feats.extend((f"t:{padded[j : j + 3]}", 0.12) for j in range(len(padded) - 2))
    if not feats:
        feats = [("empty", 1.0)]
    for f, w in feats:
        idx, sign = _slot(f, dim)
        v[idx] += sign * w
    n = float(np.linalg.norm(v))
    return (v / n if n else v).tolist()


class HeuristicProvider:
    name = "mock"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [embed_text(t) for t in texts]

    def complete(self, *, task: str, messages: list[Message], role: ModelRole, context: dict[str, Any] | None = None,
                 json_mode: bool = False, max_tokens: int = 2048, temperature: float = 0.2, timeout: float | None = None) -> str:
        fn = composers.TASKS.get(task)
        if fn is None:
            return composers.generic(messages, context or {})
        return fn(context or {})

    def stream(self, *, task: str, messages: list[Message], role: ModelRole, context: dict[str, Any] | None = None,
               max_tokens: int = 2048, temperature: float = 0.2, timeout: float | None = None) -> Iterator[str]:
        text = self.complete(task=task, messages=messages, role=role, context=context)
        for m in re.finditer(r"\S+\s*", text):
            yield m.group(0)

    def complete_with_tools(self, *, task: str, messages: list[Message], role: ModelRole, tools: list[dict[str, Any]],
                            max_tokens: int = 1500, temperature: float = 0.2, timeout: float | None = None) -> dict[str, Any]:
        """No real model to reason over the tool schemas with, so this approximates it: keyword-match the
        latest user message against each tool's name/description and call at most one match per step, skipping
        tools already called earlier in this transcript. Good enough for offline/CI use; real multi-step
        reasoning only happens when a cloud provider (the actual demo path) is configured."""
        last_user = next((m.content or "" for m in reversed(messages) if m.role == "user"), "")
        low = last_user.lower()
        already = {c["function"]["name"] for m in messages if m.role == "assistant" and m.tool_calls for c in m.tool_calls}
        for t in tools:
            fn = t["function"]
            name = fn["name"]
            if name in already:
                continue
            keywords = set(re.findall(r"[a-z]{4,}", (name + " " + fn.get("description", "")).lower())) - _STOP
            if any(kw in low for kw in keywords):
                props = fn.get("parameters", {}).get("properties", {})
                args = {"query": last_user} if "query" in props else {}
                return {"tool_calls": [{"id": f"mock-{name}", "name": name, "arguments": args}], "content": None}
        return {"tool_calls": [], "content": ""}
