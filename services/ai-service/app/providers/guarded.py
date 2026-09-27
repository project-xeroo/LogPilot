"""The egress gate: every byte headed for an AI provider passes through here.

* PII / secret redaction is applied to all message content, structured context and embedding
  inputs BEFORE the provider is called. It is not configurable and cannot be bypassed by callers.
* Usage and redaction counters power the cost metric ("AI API cost per 1,000 monitored log events").
"""
from __future__ import annotations

import threading
import time
from collections import Counter, defaultdict
from collections.abc import Iterator
from typing import Any

from app.providers.base import AIProvider, Message, ModelRole
from shared.utils.redaction import redact_obj, redact_text


class Usage:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls: Counter = Counter()
        self.errors: Counter = Counter()
        self.chars_in: Counter = Counter()
        self.chars_out: Counter = Counter()
        self.latency_ms: dict[str, list[float]] = defaultdict(list)
        self.redactions: Counter = Counter()
        self.embedded_texts = 0

    def record(self, role: str, cin: int, cout: int, ms: float, err: bool = False) -> None:
        with self._lock:
            self.calls[role] += 1
            self.chars_in[role] += cin
            self.chars_out[role] += cout
            self.latency_ms[role] = (self.latency_ms[role] + [ms])[-500:]
            if err:
                self.errors[role] += 1

    def snapshot(self) -> dict[str, Any]:
        import numpy as np

        with self._lock:
            lat = {r: {"p50": round(float(np.percentile(v, 50)), 1), "p95": round(float(np.percentile(v, 95)), 1), "n": len(v)}
                   for r, v in self.latency_ms.items() if v}
            return {
                "calls": dict(self.calls), "errors": dict(self.errors),
                # ~4 chars per token: a provider-agnostic proxy for spend
                "approx_tokens_in": {r: c // 4 for r, c in self.chars_in.items()},
                "approx_tokens_out": {r: c // 4 for r, c in self.chars_out.items()},
                "latency_ms": lat, "redactions_applied": dict(self.redactions), "embedded_texts": self.embedded_texts,
            }


class GuardedProvider:
    """Wraps a chat backend and an embedding backend, which may be different providers entirely.

    Not every provider is good at (or provisioned for) both roles - e.g. a provider might offer
    strong chat models but no properly-tuned retrieval embedding model. `AI_EMBEDDING_PROVIDER`
    lets an operator point embeddings at a different backend than chat without touching call sites;
    every caller still only ever sees one `GuardedProvider`.
    """

    def __init__(self, chat: AIProvider, embedding: AIProvider | None = None):
        self.inner = chat  # kept for callers/tests that inspect .inner (chat is the "primary" identity)
        self.embedding_inner = embedding or chat
        self.name = chat.name
        self.embedding_name = self.embedding_inner.name
        self.usage = Usage()

    # -- redaction ----------------------------------------------------------------------------------
    def _clean_messages(self, messages: list[Message]) -> list[Message]:
        out = []
        for m in messages:
            text, counts = redact_text(m.content) if m.content else (m.content, {})
            self.usage.redactions.update(counts)
            out.append(Message(m.role, text, tool_calls=m.tool_calls, tool_call_id=m.tool_call_id, name=m.name))
        return out

    def _clean_context(self, ctx: dict[str, Any] | None) -> dict[str, Any] | None:
        if ctx is None:
            return None
        counts: Counter = Counter()
        cleaned = redact_obj(ctx, counts)
        self.usage.redactions.update(counts)
        return cleaned

    # -- provider API -------------------------------------------------------------------------------
    def embed(self, texts: list[str]) -> list[list[float]]:
        clean = []
        for t in texts:
            c, counts = redact_text(t)
            self.usage.redactions.update(counts)
            clean.append(c)
        t0 = time.monotonic()
        try:
            out = self.embedding_inner.embed(clean)
        except Exception:
            self.usage.record("embedding", sum(map(len, clean)), 0, (time.monotonic() - t0) * 1000, err=True)
            raise
        self.usage.embedded_texts += len(clean)
        self.usage.record("embedding", sum(map(len, clean)), 0, (time.monotonic() - t0) * 1000)
        return out

    def complete(self, *, task: str, messages: list[Message], role: ModelRole, context: dict[str, Any] | None = None, **kw) -> str:
        msgs, ctx = self._clean_messages(messages), self._clean_context(context)
        t0 = time.monotonic()
        try:
            out = self.inner.complete(task=task, messages=msgs, role=role, context=ctx, **kw)
        except Exception:
            self.usage.record(role.value, sum(len(m.content) for m in msgs), 0, (time.monotonic() - t0) * 1000, err=True)
            raise
        clean_out, counts = redact_text(out)  # never let a model echo a secret onward
        self.usage.redactions.update(counts)
        self.usage.record(role.value, sum(len(m.content) for m in msgs), len(clean_out), (time.monotonic() - t0) * 1000)
        return clean_out

    def complete_with_tools(self, *, task: str, messages: list[Message], role: ModelRole, tools: list[dict[str, Any]], **kw) -> dict[str, Any]:
        msgs = self._clean_messages(messages)
        t0 = time.monotonic()
        try:
            out = self.inner.complete_with_tools(task=task, messages=msgs, role=role, tools=tools, **kw)
        except Exception:
            self.usage.record(role.value, sum(len(m.content or "") for m in msgs), 0, (time.monotonic() - t0) * 1000, err=True)
            raise
        content = out.get("content")
        if content:
            content, counts = redact_text(content)  # never let a model echo a secret onward
            self.usage.redactions.update(counts)
            out = {**out, "content": content}
        self.usage.record(role.value, sum(len(m.content or "") for m in msgs), len(content or ""), (time.monotonic() - t0) * 1000)
        return out

    def stream(self, *, task: str, messages: list[Message], role: ModelRole, context: dict[str, Any] | None = None, **kw) -> Iterator[str]:
        msgs, ctx = self._clean_messages(messages), self._clean_context(context)
        t0 = time.monotonic()
        n = 0
        try:
            for piece in self.inner.stream(task=task, messages=msgs, role=role, context=ctx, **kw):
                n += len(piece)
                yield piece
        except Exception:
            self.usage.record(role.value, sum(len(m.content) for m in msgs), n, (time.monotonic() - t0) * 1000, err=True)
            raise
        self.usage.record(role.value, sum(len(m.content) for m in msgs), n, (time.monotonic() - t0) * 1000)
