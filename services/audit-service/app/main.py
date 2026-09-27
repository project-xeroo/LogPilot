"""Audit Service: immutable-by-construction audit log for all user actions and all agent actions
(upload, search, export, configuration changes, autonomous actions).

* user/system events arrive on a Redis stream and are appended to a SHA-256 hash chain
* agent actions live in `agent_actions` (written by the acting service); this service exposes
  querying, export and reversal for them
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.config import SERVICE_NAME
from app.exporters import ACTION_COLUMNS, EVENT_COLUMNS, to_csv, to_jsonl
from app.handlers import AuditConsumer, NotReversible, revert_action
from shared.models import AgentAction, AuditEvent
from shared.utils.audit import emit_audit
from shared.utils.audit_writer import compute_hash
from shared.utils.db import db_healthy, get_db, init_db
from shared.utils.logsetup import setup_logging
from shared.utils.web import actor_from_request, require_internal

setup_logging(SERVICE_NAME)
log = logging.getLogger("logpilot.audit")
consumer = AuditConsumer()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    consumer.start()
    yield
    consumer.stop()


app = FastAPI(title="LogPilot Audit Service", version="2.0.0", lifespan=lifespan)
_dep = [Depends(require_internal)]


@app.get("/health")
def health():
    return {"service": SERVICE_NAME, "status": "ok" if db_healthy() and consumer.is_alive() else "degraded", "consumer_alive": consumer.is_alive(),
            "processed": consumer.processed}


def _ev(e: AuditEvent) -> dict:
    return {"id": e.id, "ts": e.ts.isoformat(), "actor_type": e.actor_type, "actor_id": str(e.actor_id) if e.actor_id else None, "actor_label": e.actor_label,
            "action": e.action, "resource_type": e.resource_type, "resource_id": e.resource_id, "project_id": str(e.project_id) if e.project_id else None,
            "ip": e.ip, "details": e.details, "hash": e.hash}


def _act(a: AgentAction) -> dict:
    return {"id": str(a.id), "created_at": a.created_at.isoformat(), "tool_name": a.tool_name, "trigger": a.trigger, "autonomy_level": a.autonomy_level,
            "status": a.status, "confidence": a.confidence, "approved_by": str(a.approved_by) if a.approved_by else None, "input_ref": a.input_ref,
            "output_ref": a.output_ref, "high_impact": a.high_impact, "reversible": a.reversible, "reverted_at": a.reverted_at.isoformat() if a.reverted_at else None,
            "summary": a.summary, "downgraded_from": a.downgraded_from, "project_id": str(a.project_id) if a.project_id else None}


def _event_query(org_id, action, actor_id, project_id, start, end):
    q = select(AuditEvent).where(AuditEvent.org_id == org_id)
    if action:
        q = q.where(AuditEvent.action.like(action.replace("*", "%")))
    if actor_id:
        q = q.where(AuditEvent.actor_id == actor_id)
    if project_id:
        q = q.where(AuditEvent.project_id == project_id)
    if start:
        q = q.where(AuditEvent.ts >= start)
    if end:
        q = q.where(AuditEvent.ts <= end)
    return q


@app.get("/audit/events", dependencies=_dep)
def events(org_id: uuid.UUID, action: str | None = None, actor_id: uuid.UUID | None = None, project_id: uuid.UUID | None = None,
           start: datetime | None = None, end: datetime | None = None, limit: int = 100, offset: int = 0, db=Depends(get_db)):
    rows = db.execute(_event_query(org_id, action, actor_id, project_id, start, end).order_by(AuditEvent.id.desc()).limit(min(limit, 500)).offset(offset)).scalars().all()
    return [_ev(e) for e in rows]


def _action_query(org_id, tool, status, project_id, start, end, high_impact):
    q = select(AgentAction).where(AgentAction.org_id == org_id)
    if tool:
        q = q.where(AgentAction.tool_name == tool)
    if status:
        q = q.where(AgentAction.status == status)
    if project_id:
        q = q.where(AgentAction.project_id == project_id)
    if start:
        q = q.where(AgentAction.created_at >= start)
    if end:
        q = q.where(AgentAction.created_at <= end)
    if high_impact is not None:
        q = q.where(AgentAction.high_impact == high_impact)
    return q


@app.get("/audit/agent-actions", dependencies=_dep)
def agent_actions(org_id: uuid.UUID, tool: str | None = None, status: str | None = None, project_id: uuid.UUID | None = None, start: datetime | None = None,
                  end: datetime | None = None, high_impact: bool | None = None, limit: int = 100, offset: int = 0, db=Depends(get_db)):
    rows = db.execute(_action_query(org_id, tool, status, project_id, start, end, high_impact).order_by(AgentAction.created_at.desc()).limit(min(limit, 500)).offset(offset)).scalars().all()
    return [_act(a) for a in rows]


@app.get("/audit/export", dependencies=_dep)
def export(request: Request, org_id: uuid.UUID, kind: str = "events", format: str = "csv", start: datetime | None = None, end: datetime | None = None,
           project_id: uuid.UUID | None = None, db=Depends(get_db)):
    """Streamed export of user events or agent actions as CSV or JSON Lines. The export is itself audited."""
    actor = actor_from_request(request)
    emit_audit("audit.export", org_id=org_id, actor_id=actor.user_id, actor_label=actor.email, resource_type=kind, details={"format": format, "start": str(start), "end": str(end)})
    if kind == "events":
        rows = (_ev(e) for e in db.execute(_event_query(org_id, None, None, project_id, start, end).order_by(AuditEvent.id).execution_options(yield_per=1000)).scalars())
        cols = EVENT_COLUMNS
    elif kind == "agent_actions":
        rows = (_act(a) for a in db.execute(_action_query(org_id, None, None, project_id, start, end, None).order_by(AgentAction.created_at).execution_options(yield_per=1000)).scalars())
        cols = ACTION_COLUMNS
    else:
        raise HTTPException(400, "kind must be 'events' or 'agent_actions'")
    if format == "json":
        return StreamingResponse(to_jsonl(rows), media_type="application/x-ndjson", headers={"Content-Disposition": f'attachment; filename="{kind}.jsonl"'})
    if format == "csv":
        return StreamingResponse(to_csv(rows, cols), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{kind}.csv"'})
    raise HTTPException(400, "format must be 'csv' or 'json'")


@app.get("/audit/verify", dependencies=_dep)
def verify(db=Depends(get_db)):
    """Recompute the hash chain and report the first break, if any (tamper evidence)."""
    prev, n = None, 0
    for e in db.execute(select(AuditEvent).order_by(AuditEvent.id).execution_options(yield_per=2000)).scalars():
        if e.prev_hash != prev or compute_hash(prev, e) != e.hash:
            return {"valid": False, "checked": n, "first_break_id": e.id}
        prev, n = e.hash, n + 1
    return {"valid": True, "checked": n}


class RevertBody(BaseModel):
    note: str = ""


@app.post("/audit/agent-actions/{action_id}/revert", dependencies=_dep)
def revert(action_id: uuid.UUID, body: RevertBody, request: Request, db=Depends(get_db)):
    actor = actor_from_request(request)
    try:
        action, effect = revert_action(db, action_id, actor.user_id, body.note)
    except NotReversible as exc:
        raise HTTPException(409, str(exc)) from exc
    emit_audit("agent_action.revert", org_id=actor.org_id, actor_id=actor.user_id, actor_label=actor.email, resource_type="agent_action",
               resource_id=str(action_id), project_id=action.project_id, details={"tool": action.tool_name, "effect": effect, "note": body.note})
    return {"reverted": True, "effect": effect, "action": _act(action)}
