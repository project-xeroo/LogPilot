"""Health-state (tool 08) and the derived intelligence the agent maintains: clusters, dedup groups,
anomalies. Queryable on demand; target < 3 seconds."""
from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import Principal, project_perm_or_api_key
from app.util import iso
from shared.models import Anomaly, DedupEvent, ErrorCluster
from shared.utils.analytics import clock_now, health_state
from shared.utils.db import get_db

router = APIRouter(prefix="/projects/{project_id}", tags=["health"])


@router.get("/health-state")
def get_health_state(project_id: uuid.UUID, window_minutes: int = Query(60, ge=5, le=10080), service: str | None = None, environment: str | None = None,
                     p: Principal = Depends(project_perm_or_api_key("health.view")), db: Session = Depends(get_db)):
    return health_state(db, project_id, window_minutes=window_minutes, environment=environment, service=service)


@router.get("/clusters")
def clusters(project_id: uuid.UUID, status: str = "active", limit: int = 50, p: Principal = Depends(project_perm_or_api_key("health.view")), db: Session = Depends(get_db)):
    q = select(ErrorCluster).where(ErrorCluster.project_id == project_id)
    if status != "all":
        q = q.where(ErrorCluster.status == status)
    rows = db.execute(q.order_by(ErrorCluster.occurrence_count.desc()).limit(min(limit, 200))).scalars().all()
    return [{"id": str(c.id), "label": c.label, "confidence": round(c.confidence, 3), "member_count": c.member_count, "occurrences": c.occurrence_count,
             "services": c.services, "first_seen": iso(c.first_seen), "last_seen": iso(c.last_seen), "drift_score": round(c.drift_score, 4), "status": c.status} for c in rows]


@router.get("/dedup")
def dedup(project_id: uuid.UUID, limit: int = 50, p: Principal = Depends(project_perm_or_api_key("health.view")), db: Session = Depends(get_db)):
    rows = db.execute(select(DedupEvent).where(DedupEvent.project_id == project_id).order_by(DedupEvent.occurrence_count.desc()).limit(min(limit, 200))).scalars().all()
    return [{"id": str(d.id), "message": d.canonical_message, "occurrences": d.occurrence_count, "variants": d.variant_count, "first_seen": iso(d.first_seen),
             "last_seen": iso(d.last_seen), "affected_services": d.affected_services} for d in rows]


@router.get("/anomalies")
def anomalies(project_id: uuid.UUID, hours: int = 24, limit: int = 100, p: Principal = Depends(project_perm_or_api_key("health.view")), db: Session = Depends(get_db)):
    since = clock_now(db, project_id) - timedelta(hours=min(hours, 720))
    rows = db.execute(select(Anomaly).where(Anomaly.project_id == project_id, Anomaly.window_end >= since).order_by(Anomaly.window_start.desc()).limit(min(limit, 500))).scalars().all()
    return [{"id": str(a.id), "kind": a.kind, "service": a.service, "severity": a.severity, "window_start": iso(a.window_start), "window_end": iso(a.window_end),
             "score": round(a.score, 2), "observed": a.observed, "expected": a.expected, "method": a.method, "explanation": a.explanation} for a in rows]
