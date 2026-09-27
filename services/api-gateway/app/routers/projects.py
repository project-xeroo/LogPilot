"""Projects and monitored-service configuration (forecast cadence, thresholds, playbooks)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import Principal, accessible_project_ids, current_user, project_perm, require
from app.util import audit, iso
from shared.models import MonitoredService, Project
from shared.utils.db import get_db

router = APIRouter(tags=["projects"])


def _proj(x: Project) -> dict:
    return {"id": str(x.id), "name": x.name, "environment": x.environment, "description": x.description, "created_at": iso(x.created_at)}


@router.get("/projects")
def list_projects(p: Principal = Depends(current_user), db: Session = Depends(get_db)):
    ids = accessible_project_ids(db, p)
    return [_proj(x) for x in db.execute(select(Project).where(Project.id.in_(ids)).order_by(Project.name)).scalars()]


class ProjectBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    environment: str = "production"
    description: str | None = None


@router.post("/projects", status_code=201)
def create_project(body: ProjectBody, request: Request, p: Principal = Depends(require("projects.manage")), db: Session = Depends(get_db)):
    if db.execute(select(Project).where(Project.org_id == p.org_id, Project.name == body.name)).scalar_one_or_none():
        raise HTTPException(409, "a project with that name already exists")
    proj = Project(org_id=p.org_id, name=body.name, environment=body.environment, description=body.description)
    db.add(proj)
    db.flush()
    audit(request, p, "project.create", resource_type="project", resource_id=proj.id, project_id=proj.id, details={"name": body.name})
    return _proj(proj)


def _svc(s: MonitoredService) -> dict:
    return {"id": str(s.id), "name": s.name, "environment": s.environment, "enabled": s.enabled, "forecast_interval_seconds": s.forecast_interval_seconds,
            "warning_threshold": s.warning_threshold, "critical_threshold": s.critical_threshold, "playbook": s.playbook,
            "last_forecast_at": iso(s.last_forecast_at)}


@router.get("/projects/{project_id}/services")
def list_services(project_id: uuid.UUID, p: Principal = Depends(project_perm("health.view")), db: Session = Depends(get_db)):
    return [_svc(s) for s in db.execute(select(MonitoredService).where(MonitoredService.project_id == project_id).order_by(MonitoredService.name)).scalars()]


class ServiceConfig(BaseModel):
    enabled: bool | None = None
    forecast_interval_seconds: int | None = Field(default=None, ge=5, le=3600)
    warning_threshold: int | None = Field(default=None, ge=1, le=99)
    critical_threshold: int | None = Field(default=None, ge=2, le=100)
    playbook: list[str] | None = None
    clear_thresholds: bool = False


@router.patch("/projects/{project_id}/services/{service_id}")
def configure_service(project_id: uuid.UUID, service_id: uuid.UUID, body: ServiceConfig, request: Request,
                      p: Principal = Depends(project_perm("services.configure")), db: Session = Depends(get_db)):
    s = db.get(MonitoredService, service_id)
    if not s or s.project_id != project_id:
        raise HTTPException(404, "service not found")
    if body.enabled is not None:
        s.enabled = body.enabled
    if body.forecast_interval_seconds is not None:
        s.forecast_interval_seconds = body.forecast_interval_seconds
    if body.clear_thresholds:
        s.warning_threshold = s.critical_threshold = None
    if body.warning_threshold is not None:
        s.warning_threshold = body.warning_threshold
    if body.critical_threshold is not None:
        s.critical_threshold = body.critical_threshold
    if s.warning_threshold and s.critical_threshold and s.critical_threshold <= s.warning_threshold:
        raise HTTPException(422, "critical threshold must be higher than the warning threshold")
    if body.playbook is not None:
        s.playbook = {"steps": [x for x in body.playbook if x.strip()][:10]}
    audit(request, p, "service.configure", resource_type="monitored_service", resource_id=s.id, project_id=project_id, details=body.model_dump(exclude_none=True))
    return _svc(s)
