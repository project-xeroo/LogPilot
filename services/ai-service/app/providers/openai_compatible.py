"""Cloud provider over an OpenAI-compatible REST interface (used for IBM Bob or any equivalent)."""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlparse

import httpx

from app.providers.base import AIUnavailable, EgressBlocked, Message, ModelRole
from shared.config import settings

log = logging.getLogger("logpilot.ai.cloud")
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def assert_egress_allowed(url: str) -> None:
    """Outbound network calls are restricted to an explicit allowlist of approved AI provider
    endpoints; nothing else may leave the boundary."""
    host = (urlparse(url).hostname or "").lower()
    if host not in settings.ai_egress_hosts:
        raise EgressBlocked(f"egress to '{host}' blocked: not in AI provider allowlist {sorted(settings.ai_egress_hosts)}")


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(self) -> None:
        if not settings.ai_base_url:
            raise RuntimeError("AI_BASE_URL is required for the openai_compatible / ibm_bob provider")
        self.base = settings.ai_base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {settings.ai_api_key}", "Content-Type": "application/json"}
        self.models = {
            ModelRole.FAST: settings.ai_fast_model,
            ModelRole.DEEP: settings.ai_deep_model,
            ModelRole.EMBEDDING: settings.ai_embedding_model,
        }

    # -- http ---------------------------------------------------------------------------------------
    def _post(self, path: str, payload: dict, timeout: float | None, stream: bool = False):
        url = f"{self.base}{path}"
        assert_egress_allowed(url)
        last: Exception | None = None
        for attempt in range(3):
            try:
                if stream:
                    return httpx.stream("POST", url, headers=self.headers, json=payload, timeout=timeout or settings.ai_timeout_seconds)
                r = httpx.post(url, headers=self.headers, json=payload, timeout=timeout or settings.ai_timeout_seconds)
                if r.status_code in RETRY_STATUS:
                    raise httpx.HTTPStatusError("retryable", request=r.request, response=r)
                r.raise_for_status()
                return r.json()
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = exc
            except httpx.HTTPStatusError as exc:
                last = exc
                if exc.response.status_code not in RETRY_STATUS:
                    raise AIUnavailable(f"provider error {exc.response.status_code}: {exc.response.text[:300]}") from exc
            time.sleep(min(0.5 * 2**attempt, 4))
        raise AIUnavailable(f"provider unreachable after retries: {last}")

    # -- api ----------------------------------------------------------------------------------------
    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 128):
            chunk = texts[i : i + 128]
            payload: dict[str, Any] = {"model": self.models[ModelRole.EMBEDDING], "input": chunk}
            try:
                data = self._post("/embeddings", payload, timeout=60)
            except AIUnavailable as exc:
                # Some asymmetric-retrieval embedding NIMs (nv-embedqa-*, e5-*) require an
                # `input_type` field ("passage" for indexing, "query" for retrieval) that is not
                # part of the standard OpenAI embeddings payload. Retry once with it added rather
                # than fail outright; symmetric models (e.g. bge-m3) never hit this branch.
                if "input_type" in str(exc).lower():
                    payload = {**payload, "input_type": "passage", "truncate": "END"}
                    data = self._post("/embeddings", payload, timeout=60)
                else:
                    raise
            vecs = [d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"])]
            if vecs and len(vecs[0]) != settings.embedding_dim:
                raise RuntimeError(
                    f"embedding model returned {len(vecs[0])} dims but EMBEDDING_DIM={settings.embedding_dim}; fix the config"
                )
            out.extend(vecs)
        return out

    def _chat_payload(self, messages, role, json_mode, max_tokens, temperature, stream=False) -> dict[str, Any]:
        msgs = []
        for m in messages:
            d: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_calls:
                d["tool_calls"] = m.tool_calls
            if m.tool_call_id:
                d["tool_call_id"] = m.tool_call_id
            if m.name:
                d["name"] = m.name
            msgs.append(d)
        p: dict[str, Any] = {
            "model": self.models[role],
            "messages": msgs,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            p["response_format"] = {"type": "json_object"}
        if stream:
            p["stream"] = True
        return p

    def complete(self, *, task, messages: list[Message], role: ModelRole, context=None, json_mode=False,
                 max_tokens=2048, temperature=0.2, timeout=None) -> str:
        data = self._post("/chat/completions", self._chat_payload(messages, role, json_mode, max_tokens, temperature), timeout)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise AIUnavailable(f"malformed provider response: {exc}") from exc

    def complete_with_tools(self, *, task, messages: list[Message], role: ModelRole, tools: list[dict[str, Any]],
                            max_tokens=1500, temperature=0.2, timeout=None) -> dict[str, Any]:
        payload = self._chat_payload(messages, role, False, max_tokens, temperature)
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
        data = self._post("/chat/completions", payload, timeout)
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError) as exc:
            raise AIUnavailable(f"malformed provider response: {exc}") from exc
        raw_calls = msg.get("tool_calls") or []
        # Reasoning models (e.g. glm-5.3-flash) return their chain of thought separately from `content` -
        # surfaced so the planner can log *why* a tool was chosen, not just which one.
        reasoning = msg.get("reasoning_content") or None
        if raw_calls:
            calls = []
            for c in raw_calls:
                try:
                    args = json.loads(c["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                calls.append({"id": c["id"], "name": c["function"]["name"], "arguments": args})
            return {"tool_calls": calls, "content": None, "reasoning": reasoning}
        return {"tool_calls": [], "content": msg.get("content") or "", "reasoning": reasoning}

    def stream(self, *, task, messages: list[Message], role: ModelRole, context=None, max_tokens=2048,
               temperature=0.2, timeout=None) -> Iterator[str]:
        url = f"{self.base}/chat/completions"
        assert_egress_allowed(url)
        try:
            with httpx.stream("POST", url, headers=self.headers, timeout=timeout or settings.ai_timeout_seconds,
                              json=self._chat_payload(messages, role, False, max_tokens, temperature, stream=True)) as r:
                if r.status_code >= 400:
                    raise AIUnavailable(f"provider error {r.status_code}")
                for line in r.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        return
                    try:
                        delta = json.loads(chunk)["choices"][0].get("delta", {}).get("content")
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    if delta:
                        yield delta
        except httpx.HTTPError as exc:
            raise AIUnavailable(f"stream failed: {exc}") from exc
