"""Internal service-to-service HTTP clients (authenticated with the internal token)."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from shared.config import settings

log = logging.getLogger("logpilot.clients")


class ServiceError(Exception):
    def __init__(self, service: str, status: int, detail: str):
        super().__init__(f"{service} responded {status}: {detail}")
        self.service, self.status, self.detail = service, status, detail


class ServiceUnavailable(ServiceError):
    def __init__(self, service: str, detail: str = "unavailable"):
        super().__init__(service, 503, detail)


def internal_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    return {"X-Internal-Token": settings.internal_api_token, **(extra or {})}


class ServiceClient:
    def __init__(self, name: str, base_url: str, timeout: float = 30.0):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, method: str, path: str, *, timeout: float | None = None, actor: dict | None = None, **kw: Any) -> Any:
        headers = internal_headers(kw.pop("headers", None))
        if actor:  # propagate the acting user to downstream audit/authorisation context
            headers.update({f"X-Actor-{k.replace('_', '-').title()}": str(v) for k, v in actor.items() if v is not None})
        try:
            r = httpx.request(method, f"{self.base_url}{path}", headers=headers, timeout=timeout or self.timeout, **kw)
        except httpx.HTTPError as exc:
            raise ServiceUnavailable(self.name, str(exc)) from exc
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except Exception:
                detail = r.text
            raise ServiceError(self.name, r.status_code, str(detail))
        if r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        return r.content

    def get(self, path: str, **kw: Any) -> Any:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> Any:
        return self.request("POST", path, **kw)

    def put(self, path: str, **kw: Any) -> Any:
        return self.request("PUT", path, **kw)

    def patch(self, path: str, **kw: Any) -> Any:
        return self.request("PATCH", path, **kw)

    def delete(self, path: str, **kw: Any) -> Any:
        return self.request("DELETE", path, **kw)


ai = ServiceClient("ai-service", settings.ai_service_url, timeout=settings.ai_timeout_seconds + 10)
ingestion = ServiceClient("log-ingestion-service", settings.ingestion_service_url)
forecasting = ServiceClient("forecasting-service", settings.forecasting_service_url)
notification = ServiceClient("notification-service", settings.notification_service_url, timeout=15)
audit = ServiceClient("audit-service", settings.audit_service_url)


def embed_texts(texts: list[str], *, timeout: float = 60.0) -> list[list[float]]:
    """Batch embedding via the AI service (which enforces PII redaction + egress allowlist)."""
    out: list[list[float]] = []
    for i in range(0, len(texts), 256):
        res = ai.post("/internal/embeddings", json={"texts": texts[i : i + 256]}, timeout=timeout)
        out.extend(res["embeddings"])
    return out
