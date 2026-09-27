"""Log ingestion endpoints: streamed file upload (up to 500MB), API ingestion, real-time streaming."""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import Principal, project_perm_or_api_key
from app.util import audit, upstream
from shared.config import settings
from shared.utils import clients
from shared.utils.clients import ServiceError, internal_headers
from shared.utils.db import get_db

router = APIRouter(prefix="/projects/{project_id}/logs", tags=["logs"])
OVERHEAD = 2 * 1024 * 1024  # multipart framing


@router.post("/upload", status_code=202)
async def upload(project_id: uuid.UUID, request: Request, environment: str | None = None, default_service: str | None = None,
                 custom_pattern: str | None = None, p: Principal = Depends(project_perm_or_api_key("logs.upload"))):
    """Streams the multipart body straight to the ingestion service (never buffered here). The 500MB limit is
    enforced both on Content-Length and on bytes actually received."""
    limit = settings.max_upload_bytes + OVERHEAD
    clen = request.headers.get("content-length")
    if clen and int(clen) > limit:
        raise HTTPException(413, f"File exceeds the {settings.max_upload_bytes // 1024 // 1024}MB upload limit.")
    received = 0

    async def body() -> AsyncIterator[bytes]:
        nonlocal received
        async for chunk in request.stream():
            received += len(chunk)
            if received > limit:
                raise HTTPException(413, f"File exceeds the {settings.max_upload_bytes // 1024 // 1024}MB upload limit.")
            yield chunk

    headers = internal_headers({k: v for k, v in request.headers.items() if k.lower() in ("content-type", "content-length")})
    headers.update({f"X-Actor-{k.replace('_', '-').title()}": v for k, v in p.actor_headers().items()})
    params = {"project_id": str(project_id), **{k: v for k, v in {"environment": environment, "default_service": default_service,
                                                                  "custom_pattern": custom_pattern}.items() if v}}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0)) as c:
            r = await c.post(f"{settings.ingestion_service_url}/ingest/upload", params=params, content=body(), headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(503, "ingestion service is currently unavailable") from exc
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"detail": r.text}
    detail = data.get("detail") if isinstance(data.get("detail"), dict) else {}
    audit(request, p, "logs.upload", resource_type="log_session", resource_id=data.get("session_id") or detail.get("session_id"),
          project_id=project_id, details={"bytes": received, "status": r.status_code, "environment": environment})
    return JSONResponse(data, status_code=r.status_code)


class ApiRecord(BaseModel):
    timestamp: str | float | int | None = None
    message: str
    severity: str | None = None
    level: str | None = None
    service: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    environment: str | None = None
    deployment_version: str | None = None
    attributes: dict = Field(default_factory=dict)


class RecordsBody(BaseModel):
    environment: str | None = None
    records: list[ApiRecord] = Field(min_length=1, max_length=10_000)


@router.post("/records", status_code=202)
def ingest_records(project_id: uuid.UUID, body: RecordsBody, request: Request, p: Principal = Depends(project_perm_or_api_key("logs.upload"))):
    try:
        res = clients.ingestion.post("/ingest/records", json={"project_id": str(project_id), **body.model_dump(exclude_none=True)}, actor=p.actor_headers(), timeout=120)
    except ServiceError as exc:
        raise upstream(exc) from exc
    audit(request, p, "logs.ingest_api", resource_type="log_session", resource_id=res.get("session_id"), project_id=project_id, details={"records": res.get("accepted")})
    return res


@router.post("/stream", status_code=202)
async def stream(project_id: uuid.UUID, request: Request, environment: str | None = None, p: Principal = Depends(project_perm_or_api_key("logs.upload"))):
    """Real-time streaming ingestion: send a chunked NDJSON body (one JSON record per line)."""
    headers = internal_headers({"content-type": "application/x-ndjson"})
    headers.update({f"X-Actor-{k.replace('_', '-').title()}": v for k, v in p.actor_headers().items()})

    async def body() -> AsyncIterator[bytes]:
        async for chunk in request.stream():
            yield chunk

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10.0)) as c:
            r = await c.post(f"{settings.ingestion_service_url}/ingest/stream", params={"project_id": str(project_id), **({"environment": environment} if environment else {})},
                             content=body(), headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(503, "ingestion service is currently unavailable") from exc
    audit(request, p, "logs.stream", project_id=project_id, details={"status": r.status_code})
    return JSONResponse(r.json(), status_code=r.status_code)


@router.get("/sessions")
def sessions(project_id: uuid.UUID, limit: int = 50, p: Principal = Depends(project_perm_or_api_key("logs.view_sessions"))):
    try:
        return clients.ingestion.get("/ingest/sessions", params={"project_id": str(project_id), "limit": limit})
    except ServiceError as exc:
        raise upstream(exc) from exc


@router.get("/sessions/{session_id}")
def session(project_id: uuid.UUID, session_id: uuid.UUID, p: Principal = Depends(project_perm_or_api_key("logs.view_sessions"))):
    try:
        s = clients.ingestion.get(f"/ingest/sessions/{session_id}")
    except ServiceError as exc:
        raise upstream(exc) from exc
    if s.get("project_id") != str(project_id):
        raise HTTPException(404, "session not found")
    return s


@router.get("/sessions/{session_id}/malformed")
def malformed(project_id: uuid.UUID, session_id: uuid.UUID, limit: int = 100, p: Principal = Depends(project_perm_or_api_key("logs.view_sessions"))):
    try:
        return clients.ingestion.get(f"/ingest/sessions/{session_id}/malformed", params={"limit": limit})
    except ServiceError as exc:
        raise upstream(exc) from exc


_ = Session, get_db
