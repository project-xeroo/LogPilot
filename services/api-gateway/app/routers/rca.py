"""Root Cause Analysis: causal chains with confidence and evidence; output is human-reviewed."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.tool_router import router as tools
from app.auth import Principal, project_perm
from app.util import audit, iso
from shared.models import RcaResult
from shared.utils.db import get_db

router = APIRouter(prefix="/projects/{project_id}/rca", tags=["rca"])


class RcaBody(BaseModel):
    service: str | None = None
    cluster_id: uuid.UUID | None = None
    start: datetime | None = None
    end: datetime | None = None


def _row(r: RcaResult) -> dict:
    return {"id": str(r.id), "service": r.service, "window": {"start": iso(r.window_start), "end": iso(r.window_end)}, "causal_chain": r.causal_chain,
            "confidence": round(r.confidence, 3), "explanation": r.explanation, "evidence": r.evidence, "dependency_graph": r.dependency_graph,
            "review_status": r.review_status, "trigger": r.trigger, "latency_ms": r.latency_ms, "created_at": iso(r.created_at)}


@router.post("")
def run(project_id: uuid.UUID, body: RcaBody, request: Request, p: Principal = Depends(project_perm("rca.run")), db: Session = Depends(get_db)):
    res = tools.invoke(db, p, "root_cause_analysis", project_id, body.model_dump(mode="json", exclude_none=True))
    audit(request, p, "rca.run", resource_type="rca", resource_id=res["result"].get("id"), project_id=project_id, details={"service": body.service, "latency_ms": res["latency_ms"]})
    return res["result"]


@router.get("")
def list_rca(project_id: uuid.UUID, limit: int = 20, p: Principal = Depends(project_perm("health.view")), db: Session = Depends(get_db)):
    return [_row(r) for r in db.execute(select(RcaResult).where(RcaResult.project_id == project_id).order_by(RcaResult.created_at.desc()).limit(min(limit, 100))).scalars()]


@router.get("/{rca_id}")
def get_rca(project_id: uuid.UUID, rca_id: uuid.UUID, p: Principal = Depends(project_perm("health.view")), db: Session = Depends(get_db)):
    r = db.get(RcaResult, rca_id)
    if not r or r.project_id != project_id:
        raise HTTPException(404, "RCA result not found")
    return _row(r)


class ReviewBody(BaseModel):
    status: str


@router.post("/{rca_id}/review")
def review(project_id: uuid.UUID, rca_id: uuid.UUID, body: ReviewBody, request: Request, p: Principal = Depends(project_perm("reports.edit")), db: Session = Depends(get_db)):
    """RCA output is human-reviewed (PRD tool 10)."""
    if body.status not in ("approved", "rejected"):
        raise HTTPException(422, "status must be 'approved' or 'rejected'")
    r = db.get(RcaResult, rca_id)
    if not r or r.project_id != project_id:
        raise HTTPException(404, "RCA result not found")
    r.review_status, r.reviewed_by = body.status, p.id
    audit(request, p, "rca.review", resource_type="rca", resource_id=r.id, project_id=project_id, details={"status": body.status})
    return _row(r)
