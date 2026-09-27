"""Request IDs, timing, security headers, and audit of denied requests (RBAC failures)."""
from __future__ import annotations

import logging
import time
import uuid

from fastapi import Request

from shared.utils.audit import emit_audit

log = logging.getLogger("logpilot.http")
QUIET = ("/health", "/api/v1/health")


async def observability(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    t0 = time.monotonic()
    response = await call_next(request)
    ms = (time.monotonic() - t0) * 1000
    response.headers["X-Request-ID"] = rid
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = response.headers.get("Cache-Control", "no-store")
    if request.url.path not in QUIET:
        log.info("%s %s -> %s %.0fms rid=%s", request.method, request.url.path, response.status_code, ms, rid)
    if response.status_code in (401, 403) and not request.url.path.endswith("/auth/login"):
        p = getattr(request.state, "principal", None)
        emit_audit("security.denied", org_id=getattr(p, "org_id", None), actor_id=getattr(p, "id", None), actor_label=getattr(p, "email", None),
                   ip=request.client.host if request.client else None, details={"path": request.url.path, "method": request.method, "status": response.status_code})
    return response
