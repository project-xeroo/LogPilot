"""Search screen backend: keyword (regex-capable) and semantic search with filters (tool 05)."""
from __future__ import annotations

import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.auth import Principal, project_perm_or_api_key
from app.util import audit, upstream
from shared.utils import clients
from shared.utils.clients import ServiceError
from shared.utils.events import get_redis
from shared.utils.redaction import redact

router = APIRouter(prefix="/projects/{project_id}", tags=["search"])


class SearchBody(BaseModel):
    query: str = ""
    mode: str = Field(default="keyword", pattern="^(keyword|semantic)$")
    regex: bool = False
    exact: bool = False
    start: datetime | None = None
    end: datetime | None = None
    severity: list[str] = []
    min_severity: str | None = None
    services: list[str] = []
    environment: str | None = None
    deployment_version: str | None = None
    trace_id: str | None = None
    request_id: str | None = None
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)
    context_window: int = Field(default=2, ge=0, le=10)


@router.post("/search")
def search(project_id: uuid.UUID, body: SearchBody, request: Request, p: Principal = Depends(project_perm_or_api_key("logs.search"))):
    t0 = time.monotonic()
    try:
        res = clients.ai.post("/search", json={"project_id": str(project_id), **body.model_dump(mode="json", exclude_none=True)}, actor=p.actor_headers(), timeout=30)
    except ServiceError as exc:
        raise upstream(exc) from exc
    ms = (time.monotonic() - t0) * 1000
    try:  # latency samples feed the p99 technical metric
        r = get_redis()
        r.lpush(f"metrics:search:{body.mode}", f"{ms:.1f}")
        r.ltrim(f"metrics:search:{body.mode}", 0, 999)
    except Exception:
        pass
    audit(request, p, "logs.search", project_id=project_id, details={"mode": body.mode, "query": redact(body.query)[:200], "regex": body.regex,
                                                                     "results": res.get("count"), "took_ms": res.get("took_ms")})
    return res
