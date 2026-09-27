"""Audit log: all user actions and all agent actions; export; hash-chain verification; reversal of agent actions."""
from __future__ import annotations

import uuid
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.auth import Principal, require
from app.util import upstream
from shared.config import settings
from shared.utils import clients
from shared.utils.clients import ServiceError, internal_headers

router = APIRouter(prefix="/audit", tags=["audit"])


def _params(p: Principal, **kw) -> dict:
    return {"org_id": str(p.org_id), **{k: (v.isoformat() if isinstance(v, datetime) else str(v)) for k, v in kw.items() if v is not None}}


@router.get("/events")
def events(action: str | None = None, actor_id: uuid.UUID | None = None, project_id: uuid.UUID | None = None, start: datetime | None = None,
           end: datetime | None = None, limit: int = 100, offset: int = 0, p: Principal = Depends(require("audit.view"))):
    try:
        return clients.audit.get("/audit/events", params=_params(p, action=action, actor_id=actor_id, project_id=project_id, start=start, end=end, limit=limit, offset=offset))
    except ServiceError as exc:
        raise upstream(exc) from exc


@router.get("/agent-actions")
def agent_actions(tool: str | None = None, status: str | None = None, project_id: uuid.UUID | None = None, start: datetime | None = None, end: datetime | None = None,
                  high_impact: bool | None = None, limit: int = 100, offset: int = 0, p: Principal = Depends(require("audit.view"))):
    try:
        return clients.audit.get("/audit/agent-actions", params=_params(p, tool=tool, status=status, project_id=project_id, start=start, end=end, high_impact=high_impact, limit=limit, offset=offset))
    except ServiceError as exc:
        raise upstream(exc) from exc


@router.get("/export")
async def export(request: Request, kind: str = "events", format: str = "csv", start: datetime | None = None, end: datetime | None = None,
                 project_id: uuid.UUID | None = None, p: Principal = Depends(require("audit.export"))):
    """Streams the audit export through (nothing is buffered)."""
    headers = internal_headers({f"X-Actor-{k.replace('_', '-').title()}": v for k, v in p.actor_headers().items()})
    client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))
    req = client.build_request("GET", f"{settings.audit_service_url}/audit/export", headers=headers, params=_params(p, kind=kind, format=format, start=start, end=end, project_id=project_id))
    r = await client.send(req, stream=True)
    if r.status_code >= 400:
        body = await r.aread()
        await client.aclose()
        raise HTTPException(r.status_code, body.decode()[:300])

    async def gen():
        try:
            async for chunk in r.aiter_bytes():
                yield chunk
        finally:
            await r.aclose()
            await client.aclose()

    return StreamingResponse(gen(), media_type=r.headers.get("content-type", "text/csv"), headers={"Content-Disposition": r.headers.get("content-disposition", 'attachment; filename="audit"')})


@router.get("/verify")
def verify(p: Principal = Depends(require("audit.view"))):
    try:
        return clients.audit.get("/audit/verify")
    except ServiceError as exc:
        raise upstream(exc) from exc


class RevertBody(BaseModel):
    note: str = ""


@router.post("/agent-actions/{action_id}/revert")
def revert(action_id: uuid.UUID, body: RevertBody, p: Principal = Depends(require("audit.revert"))):
    try:
        return clients.audit.post(f"/audit/agent-actions/{action_id}/revert", json=body.model_dump(), actor=p.actor_headers())
    except ServiceError as exc:
        raise upstream(exc) from exc
