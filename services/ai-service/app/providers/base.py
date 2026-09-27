"""Provider-agnostic AI interface (PRD 6: "model-agnostic by design ... OpenAI-compatible or
equivalent REST interface so the organization can swap providers without redesigning the agent")."""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class ModelRole(StrEnum):
    FAST = "fast"  # log classification, chat, search responses, log explanations
    DEEP = "deep"  # RCA, incident reports, pre-mortems, executive summaries, failure-risk explanation
    EMBEDDING = "embedding"  # embeddings, semantic search, clustering, forecasting similarity


@dataclass
class Message:
    role: str  # system | user | assistant | tool
    content: str | None = None
    # assistant turn requesting tool calls: [{"id", "type": "function", "function": {"name", "arguments" (JSON str)}}]
    tool_calls: list[dict[str, Any]] | None = None
    # tool turn answering one call: which call this is a result for, and (for providers that want it) its name
    tool_call_id: str | None = None
    name: str | None = None


class AIUnavailable(Exception):
    """Cloud inference cannot be reached / failed after retries. Callers degrade gracefully."""


class EgressBlocked(AIUnavailable):
    """Outbound call refused: target host is not on the approved AI-provider allowlist.
    Treated as "AI unavailable" so every caller degrades gracefully instead of failing."""


class AIProvider(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def complete(
        self, *, task: str, messages: list[Message], role: ModelRole, context: dict[str, Any] | None = None,
        json_mode: bool = False, max_tokens: int = 2048, temperature: float = 0.2, timeout: float | None = None,
    ) -> str: ...

    def stream(
        self, *, task: str, messages: list[Message], role: ModelRole, context: dict[str, Any] | None = None,
        max_tokens: int = 2048, temperature: float = 0.2, timeout: float | None = None,
    ) -> Iterator[str]: ...

    def complete_with_tools(
        self, *, task: str, messages: list[Message], role: ModelRole, tools: list[dict[str, Any]],
        max_tokens: int = 1500, temperature: float = 0.2, timeout: float | None = None,
    ) -> dict[str, Any]:
        """Native function-calling: the model decides which of `tools` (if any) to call next, given the
        running transcript (which may already contain earlier assistant tool_calls + tool results).
        Returns {"tool_calls": [{"id", "name", "arguments": dict}], "content": None} when it wants to call
        tools, or {"tool_calls": [], "content": str} when it's ready to answer. This is what makes the agent
        genuinely decide its own next step instead of following a fixed rule -> tool mapping."""
        ...
