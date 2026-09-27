"""Forecasting Service API: the Failure Risk view (read-only, always on) + control endpoints.

Run the loop itself with Celery:
    celery -A app.loop.scheduler:celery_app worker -B -Q forecasting -l INFO      (worker + beat in dev)
    celery -A app.loop.scheduler:celery_app beat -l INFO                          (scheduler container)
    celery -A app.loop.scheduler:celery_app worker -Q forecasting                 (worker containers)
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import Integer, cast, func, text
from sqlalchemy.orm import Session

from app.config import CYCLE_BUDGET_SECONDS, SERVICE_NAME
from app.indicators import get_weights
from app.loop import run_cycle
from app.loop.scheduler import celery_app
from shared.models import ForecastCycleMetric, ForecastWeights, MonitoredService, PreIncidentAlert, RiskSnapshot
from shared.models.base import utcnow
from shared.utils.db import db_healthy, get_db, init_db
from shared.utils.logsetup import setup_logging
from shared.utils.web import require_internal

setup_logging(SERVICE_NAME)
log = logging.getLogger("logpilot.forecast.api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="LogPilot Forecasting Service", version="2.0.0", lifespan=lifespan)
_dep = [Depends(require_internal)]


@app.get("/health")
def health():
    return {"service": SERVICE_NAME, "status": "ok" if db_healthy() else "degraded", "database": db_healthy()}


def _latest_by_service(db: Session, project_id: uuid.UUID) -> list[dict]:
    rows = db.execute(
        text(
            """
            SELECT DISTINCT ON (ms.id) ms.id, ms.name, ms.environment, ms.enabled, ms.forecast_interval_seconds, ms.warning_threshold, ms.critical_threshold,
                   r.risk_score, r.velocity_score, r.similarity_score, r.baseline_score, r.trend, r.eta_minutes_low, r.eta_minutes_high, r.timestamp,
                   r.degraded, r.signals
            FROM monitored_services ms LEFT JOIN risk_snapshots r ON r.service_id = ms.id
            WHERE ms.project_id = :p ORDER BY ms.id, r.timestamp DESC NULLS LAST
            """
        ),
        {"p": project_id},
    ).all()
    alerts = {a.service_id: a for a in db.query(PreIncidentAlert).filter(PreIncidentAlert.project_id == project_id, PreIncidentAlert.resolved_at.is_(None),
                                                                          PreIncidentAlert.status.in_(["open", "acknowledged", "pending_review"]))}
    out = []
    for r in rows:
        sig = r.signals or {}
        th = sig.get("thresholds", {})
        warn, crit = th.get("warning", 60), th.get("critical", 80)
        score = r.risk_score
        a = alerts.get(r.id)
        out.append({
            "service_id": str(r.id), "service": r.name, "environment": r.environment, "enabled": r.enabled, "interval_seconds": r.forecast_interval_seconds,
            "risk_score": round(score, 1) if score is not None else None, "velocity_score": r.velocity_score, "similarity_score": r.similarity_score,
            "baseline_score": r.baseline_score, "trend": r.trend, "eta_minutes_low": r.eta_minutes_low, "eta_minutes_high": r.eta_minutes_high,
            "as_of": r.timestamp.isoformat() if r.timestamp else None, "degraded": r.degraded,
            "level": None if score is None else "critical" if score >= crit else "warning" if score >= warn else "ok",
            "primary_signals": [d for d in _top_signals(sig)], "open_alert_id": str(a.id) if a else None,
            "thresholds": {"warning": warn, "critical": crit},
        })
    return sorted(out, key=lambda s: -(s["risk_score"] or -1))


def _top_signals(sig: dict) -> list[str]:
    out = []
    v, b, s = sig.get("velocity") or {}, sig.get("baseline") or {}, sig.get("similarity") or {}
    if v.get("errors_per_min", 0) >= 1:
        out.append(f"{v['errors_per_min']:.1f} errors/min ({v.get('ratio', 0):.1f}x baseline)")
    if v.get("acceleration", 0) > 0.05:
        out.append("errors accelerating")
    if s.get("best_match", 0) >= 0.7:
        out.append(f"matches past incident ({s['best_match']:.2f})")
    if b.get("z", 0) >= 3:
        out.append(f"{b['z']:.1f}σ above normal")
    return out


@app.get("/risk", dependencies=_dep)
def risk_board(project_id: uuid.UUID, db: Session = Depends(get_db)):
    """Failure Risk view: live risk score per service, trend, estimated time-to-incident, contributing signals."""
    return {"project_id": str(project_id), "services": _latest_by_service(db, project_id), "as_of": utcnow().isoformat()}


@app.get("/risk/{service_id}", dependencies=_dep)
def risk_detail(service_id: uuid.UUID, db: Session = Depends(get_db)):
    """Drill-down: full signal breakdown and supporting evidence for one service."""
    ms = db.get(MonitoredService, service_id)
    if not ms:
        raise HTTPException(404, "service not found")
    snap = db.query(RiskSnapshot).filter(RiskSnapshot.service_id == service_id).order_by(RiskSnapshot.timestamp.desc()).first()
    if not snap:
        raise HTTPException(404, "no risk snapshot yet for this service")
    w = get_weights(db, service_id)
    alert = db.query(PreIncidentAlert).filter(PreIncidentAlert.service_id == service_id, PreIncidentAlert.resolved_at.is_(None)).order_by(PreIncidentAlert.created_at.desc()).first()
    return {
        "service_id": str(service_id), "service": ms.name, "environment": ms.environment, "risk_score": round(snap.risk_score, 1),
        "velocity_score": round(snap.velocity_score, 1), "similarity_score": round(snap.similarity_score, 1), "baseline_score": round(snap.baseline_score, 1),
        "trend": snap.trend, "eta_minutes_low": snap.eta_minutes_low, "eta_minutes_high": snap.eta_minutes_high, "as_of": snap.timestamp.isoformat(),
        "degraded": snap.degraded, "cycle_ms": snap.cycle_ms, "signals": snap.signals, "explanation": snap.explanation,
        "weights": {"velocity": w.w_velocity, "similarity": w.w_similarity, "baseline": w.w_baseline, "signature_threshold": w.signature_threshold,
                    "feedback_samples": w.samples, "true_positives": w.true_positives, "false_positives": w.false_positives},
        "alert": None if not alert else {"id": str(alert.id), "level": alert.level, "status": alert.status, "text": alert.alert_text,
                                          "recommended_actions": alert.recommended_actions, "evidence_chain": alert.evidence_chain},
    }


@app.get("/risk/{service_id}/history", dependencies=_dep)
def risk_history(service_id: uuid.UUID, hours: float = 6, db: Session = Depends(get_db)):
    since = utcnow() - timedelta(hours=min(hours, 168))
    rows = db.query(RiskSnapshot).filter(RiskSnapshot.service_id == service_id, RiskSnapshot.timestamp >= since).order_by(RiskSnapshot.timestamp).limit(2000).all()
    return {"service_id": str(service_id), "points": [{"t": r.timestamp.isoformat(), "risk": round(r.risk_score, 1), "velocity": round(r.velocity_score, 1),
                                                       "similarity": round(r.similarity_score, 1), "baseline": round(r.baseline_score, 1)} for r in rows]}


@app.post("/risk/{service_id}/run", dependencies=_dep)
def run_now(service_id: uuid.UUID):
    """Run one forecasting cycle immediately (also used by tests and the console's 'refresh')."""
    res = run_cycle(service_id)
    if res.get("skipped"):
        raise HTTPException(404, res["skipped"])
    return res


@app.post("/learn/outcome/{outcome_id}", dependencies=_dep, status_code=202)
def learn(outcome_id: uuid.UUID):
    celery_app.send_task("forecasting.learn_outcome", args=[str(outcome_id)], queue="forecasting")
    return {"queued": True}


@app.get("/metrics", dependencies=_dep)
def metrics(project_id: uuid.UUID | None = None, hours: float = 24, db: Session = Depends(get_db)):
    """Cycle completion rate within the 60s budget (technical metric: > 99%)."""
    since = utcnow() - timedelta(hours=hours)
    q = db.query(func.count(ForecastCycleMetric.id), func.sum(cast(ForecastCycleMetric.within_budget, Integer)),
                 func.avg(ForecastCycleMetric.duration_ms), func.max(ForecastCycleMetric.duration_ms)).filter(ForecastCycleMetric.started_at >= since)
    if project_id:
        ids = [i for (i,) in db.query(MonitoredService.id).filter(MonitoredService.project_id == project_id)]
        q = q.filter(ForecastCycleMetric.service_id.in_(ids))
    n, ok, avg_ms, max_ms = q.one()
    n = n or 0
    return {"cycles": n, "within_budget": int(ok or 0), "completion_rate": (int(ok or 0) / n) if n else None, "avg_ms": float(avg_ms or 0),
            "max_ms": int(max_ms or 0), "budget_seconds": CYCLE_BUDGET_SECONDS}


_ = ForecastWeights
