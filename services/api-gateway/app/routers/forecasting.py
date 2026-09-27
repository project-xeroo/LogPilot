"""Failure Risk Board: the queryable state behind the Failure Risk view (PRD 4.3)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import Principal, project_perm, project_perm_or_api_key
from app.util import upstream
from shared.models import MonitoredService
from shared.utils import clients
from shared.utils.clients import ServiceError
from shared.utils.db import get_db

router = APIRouter(prefix="/projects/{project_id}/risk", tags=["forecasting"])


def _own(db: Session, project_id: uuid.UUID, service_id: uuid.UUID) -> MonitoredService:
    s = db.get(MonitoredService, service_id)
    if not s or s.project_id != project_id:
        raise HTTPException(404, "service not found in this project")
    return s


@router.get("")
def risk_board(project_id: uuid.UUID, p: Principal = Depends(project_perm_or_api_key("risk.view"))):
    try:
        return clients.forecasting.get("/risk", params={"project_id": str(project_id)})
    except ServiceError as exc:
        raise upstream(exc) from exc


@router.get("/{service_id}")
def risk_detail(project_id: uuid.UUID, service_id: uuid.UUID, p: Principal = Depends(project_perm_or_api_key("risk.view")), db: Session = Depends(get_db)):
    _own(db, project_id, service_id)
    try:
        return clients.forecasting.get(f"/risk/{service_id}")
    except ServiceError as exc:
        raise upstream(exc) from exc


@router.get("/{service_id}/history")
def risk_history(project_id: uuid.UUID, service_id: uuid.UUID, hours: float = 6, p: Principal = Depends(project_perm_or_api_key("risk.view")), db: Session = Depends(get_db)):
    _own(db, project_id, service_id)
    try:
        return clients.forecasting.get(f"/risk/{service_id}/history", params={"hours": hours})
    except ServiceError as exc:
        raise upstream(exc) from exc


@router.post("/{service_id}/run")
def run_now(project_id: uuid.UUID, service_id: uuid.UUID, p: Principal = Depends(project_perm("services.configure")), db: Session = Depends(get_db)):
    """Trigger an immediate forecasting cycle (SRE/Admin)."""
    _own(db, project_id, service_id)
    try:
        return clients.forecasting.post(f"/risk/{service_id}/run", timeout=60)
    except ServiceError as exc:
        raise upstream(exc) from exc
