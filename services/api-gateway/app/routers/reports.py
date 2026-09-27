"""Incident & Pre-Mortem Reports: agent drafts, humans review/edit/sign off, then export PDF or Markdown."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.tool_router import router as tools
from app.auth import Principal, project_perm
from app.util import audit, iso, upstream
from shared.models import IncidentReport
from shared.models.base import utcnow
from shared.utils import clients
from shared.utils.clients import ServiceError
from shared.utils.db import get_db

router = APIRouter(prefix="/projects/{project_id}/reports", tags=["reports"])


def _row(r: IncidentReport, full: bool = False) -> dict:
    d = {"id": str(r.id), "title": r.title, "kind": r.kind, "status": r.status, "created_at": iso(r.created_at), "updated_at": iso(r.updated_at),
         "generated_by": r.generated_by, "generation_ms": r.generation_ms, "model": r.model_info, "approved_at": iso(r.approved_at),
         "alert_id": str(r.alert_id) if r.alert_id else None, "rca_id": str(r.rca_id) if r.rca_id else None, "last_export": r.export_format}
    if full:
        d["sections"] = (r.sections_json or {}).get("sections", [])
        d["ai_narrated"] = (r.sections_json or {}).get("ai_narrated")
    return d


def _own(db: Session, project_id: uuid.UUID, report_id: uuid.UUID) -> IncidentReport:
    r = db.get(IncidentReport, report_id)
    if not r or r.project_id != project_id:
        raise HTTPException(404, "report not found")
    return r


@router.get("")
def list_reports(project_id: uuid.UUID, kind: str | None = None, status: str | None = None, limit: int = 50,
                 p: Principal = Depends(project_perm("reports.view")), db: Session = Depends(get_db)):
    q = select(IncidentReport).where(IncidentReport.project_id == project_id)
    if kind:
        q = q.where(IncidentReport.kind == kind)
    if status:
        q = q.where(IncidentReport.status == status)
    return [_row(r) for r in db.execute(q.order_by(IncidentReport.created_at.desc()).limit(min(limit, 200))).scalars()]


@router.get("/{report_id}")
def get_report(project_id: uuid.UUID, report_id: uuid.UUID, p: Principal = Depends(project_perm("reports.view")), db: Session = Depends(get_db)):
    return _row(_own(db, project_id, report_id), full=True)


class Section(BaseModel):
    key: str
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(max_length=50000)


class EditBody(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    sections: list[Section] | None = None


@router.patch("/{report_id}")
def edit_report(project_id: uuid.UUID, report_id: uuid.UUID, body: EditBody, request: Request, p: Principal = Depends(project_perm("reports.edit")), db: Session = Depends(get_db)):
    """Reports are editable in-app before export."""
    r = _own(db, project_id, report_id)
    if r.status == "discarded":
        raise HTTPException(409, "discarded reports cannot be edited")
    if body.title:
        r.title = body.title
    if body.sections is not None:
        r.sections_json = {**(r.sections_json or {}), "sections": [s.model_dump() for s in body.sections], "edited": True}
    if r.status == "approved":
        r.status, r.approved_by, r.approved_at = "draft", None, None  # an edit voids the earlier sign-off
    r.updated_at = utcnow()
    audit(request, p, "report.edit", resource_type="report", resource_id=r.id, project_id=project_id)
    return _row(r, full=True)


@router.post("/{report_id}/approve")
def approve(project_id: uuid.UUID, report_id: uuid.UUID, request: Request, p: Principal = Depends(project_perm("reports.edit")), db: Session = Depends(get_db)):
    """Human sign-off on an agent-drafted report."""
    r = _own(db, project_id, report_id)
    if r.status == "discarded":
        raise HTTPException(409, "discarded reports cannot be approved")
    r.status, r.approved_by, r.approved_at = "approved", p.id, utcnow()
    audit(request, p, "report.approve", resource_type="report", resource_id=r.id, project_id=project_id)
    return _row(r)


@router.delete("/{report_id}", status_code=204)
def discard(project_id: uuid.UUID, report_id: uuid.UUID, request: Request, p: Principal = Depends(project_perm("reports.edit")), db: Session = Depends(get_db)):
    r = _own(db, project_id, report_id)
    r.status = "discarded"
    audit(request, p, "report.discard", resource_type="report", resource_id=r.id, project_id=project_id)


@router.get("/{report_id}/export")
def export(project_id: uuid.UUID, report_id: uuid.UUID, request: Request, format: Literal["pdf", "markdown"] = "pdf",
           p: Principal = Depends(project_perm("reports.export")), db: Session = Depends(get_db)):
    _own(db, project_id, report_id)
    db.commit()
    try:
        content = clients.ai.get(f"/reports/{report_id}/export", params={"format": format}, timeout=60)
    except ServiceError as exc:
        raise upstream(exc) from exc
    audit(request, p, "report.export", resource_type="report", resource_id=report_id, project_id=project_id, details={"format": format})
    media = "application/pdf" if format == "pdf" else "text/markdown; charset=utf-8"
    ext = "pdf" if format == "pdf" else "md"
    return Response(content, media_type=media, headers={"Content-Disposition": f'attachment; filename="logpilot-report-{str(report_id)[:8]}.{ext}"'})


# ---- generation (via the tool router so policy + audit apply) -------------------------------------------------------------
class IncidentBody(BaseModel):
    service: str | None = None
    cluster_id: uuid.UUID | None = None
    rca_id: uuid.UUID | None = None
    alert_id: uuid.UUID | None = None
    start: datetime | None = None
    end: datetime | None = None


@router.post("/incident", status_code=201)
def generate_incident(project_id: uuid.UUID, body: IncidentBody, request: Request, p: Principal = Depends(project_perm("reports.generate")), db: Session = Depends(get_db)):
    res = tools.invoke(db, p, "incident_report_generation", project_id, {"kind": "incident", **body.model_dump(mode="json", exclude_none=True)})
    audit(request, p, "report.generate", resource_type="report", resource_id=res["result"].get("report_id"), project_id=project_id, details={"kind": "incident"})
    return res["result"]


class ExecBody(BaseModel):
    days: int = Field(default=7, ge=1, le=90)


@router.post("/executive-summary", status_code=201)
def generate_exec(project_id: uuid.UUID, body: ExecBody, request: Request, p: Principal = Depends(project_perm("reports.generate")), db: Session = Depends(get_db)):
    res = tools.invoke(db, p, "incident_report_generation", project_id, {"kind": "executive_summary", "days": body.days})
    audit(request, p, "report.generate", resource_type="report", resource_id=res["result"].get("report_id"), project_id=project_id, details={"kind": "executive_summary"})
    return res["result"]
