"""Deployment Comparison (tool 12): side-by-side regression view between two versions."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.agent.tool_router import router as tools
from app.auth import Principal, project_perm
from app.util import audit, iso
from shared.models import DeploymentComparison, DeploymentEvent
from shared.models.base import utcnow
from shared.utils.analytics import list_versions
from shared.utils.celery_factory import make_celery
from shared.utils.db import get_db

router = APIRouter(prefix="/projects/{project_id}/deployments", tags=["deployments"])
_celery = make_celery("gateway-sender")


@router.get("")
def versions(project_id: uuid.UUID, service: str | None = None, p: Principal = Depends(project_perm("deployments.view")), db: Session = Depends(get_db)):
    return list_versions(db, project_id, service)


class DeployBody(BaseModel):
    service: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    environment: str = "production"
    deployed_at: datetime | None = None


@router.post("", status_code=201)
def register(project_id: uuid.UUID, body: DeployBody, request: Request, p: Principal = Depends(project_perm("deployments.compare")), db: Session = Depends(get_db)):
    """Register a deployment event via the API; the agent then compares it with the previous version autonomously."""
    stmt = pg_insert(DeploymentEvent).values(id=uuid.uuid4(), project_id=project_id, service=body.service, version=body.version, environment=body.environment,
                                             deployed_at=body.deployed_at or utcnow(), source="api")
    stmt = stmt.on_conflict_do_nothing(index_elements=["project_id", "service", "version", "environment"])
    db.execute(stmt)
    db.commit()
    _celery.send_task("deployment.compare_on_deploy", args=[str(project_id), body.service, body.environment, body.version], queue="processing", countdown=5)
    audit(request, p, "deployment.register", project_id=project_id, details=body.model_dump(mode="json"))
    return {"registered": True, "note": "The agent will compare this version with the previous one once its logs arrive."}


@router.get("/compare")
def compare(project_id: uuid.UUID, service: str, from_version: str, to_version: str, environment: str | None = None,
            p: Principal = Depends(project_perm("deployments.compare")), db: Session = Depends(get_db)):
    res = tools.invoke(db, p, "deployment_comparison", project_id, {"service": service, "from_version": from_version, "to_version": to_version, "environment": environment})
    return res["result"]


@router.get("/comparisons")
def comparisons(project_id: uuid.UUID, limit: int = 20, p: Principal = Depends(project_perm("deployments.view")), db: Session = Depends(get_db)):
    rows = db.execute(select(DeploymentComparison).where(DeploymentComparison.project_id == project_id).order_by(DeploymentComparison.created_at.desc()).limit(min(limit, 100))).scalars()
    return [{"id": str(c.id), "service": c.service, "from_version": c.from_version, "to_version": c.to_version, "regression": c.regression, "trigger": c.trigger,
             "created_at": iso(c.created_at), "result": c.result} for c in rows]


@router.get("/comparisons/{comparison_id}")
def comparison(project_id: uuid.UUID, comparison_id: uuid.UUID, p: Principal = Depends(project_perm("deployments.view")), db: Session = Depends(get_db)):
    c = db.get(DeploymentComparison, comparison_id)
    if not c or c.project_id != project_id:
        raise HTTPException(404, "comparison not found")
    return {"id": str(c.id), "service": c.service, "from_version": c.from_version, "to_version": c.to_version, "regression": c.regression, "trigger": c.trigger,
            "created_at": iso(c.created_at), "result": c.result}
