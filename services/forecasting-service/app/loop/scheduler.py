"""Forecasting scheduler + workers (PRD 4.4: managed scheduler/task queue, configurable interval per service).

Celery beat fires `forecasting.tick` every SCHEDULER_TICK_SECONDS. The tick claims every enabled
service whose `next_forecast_at` has passed (row-locked with SKIP LOCKED, so several scheduler replicas
never double-run a service) and enqueues one `forecasting.run_cycle` per service. Default cadence is
60s per service and is configurable per service (and per org).
"""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from celery.signals import worker_process_init
from sqlalchemy import text

from app.baseline import update_baselines
from app.indicators import learn_from_outcome, learn_history
from app.loop.cycle import run_cycle, thresholds
from shared.config import settings
from shared.models import MonitoredService, PreIncidentAlert, Project
from shared.models.base import utcnow
from shared.utils.celery_factory import RETRY_KWARGS, make_celery
from shared.utils.clients import ServiceError, ai
from shared.utils.db import init_db, session_scope
from shared.utils.events import publish_event
from shared.utils.logsetup import setup_logging

setup_logging("forecasting")
log = logging.getLogger("logpilot.forecast.scheduler")

celery_app = make_celery(
    "forecasting",
    beat_schedule={
        "forecasting-tick": {"task": "forecasting.tick", "schedule": float(settings.scheduler_tick_seconds), "options": {"queue": "forecasting", "expires": 30}},
    },
)


@worker_process_init.connect
def _init(**_):
    init_db()


@celery_app.task(name="forecasting.tick")
def tick() -> dict:
    """Claim and enqueue every service that is due."""
    claimed: list[str] = []
    with session_scope() as db:
        rows = db.execute(
            text(
                """
                SELECT id FROM monitored_services WHERE enabled AND (next_forecast_at IS NULL OR next_forecast_at <= now())
                ORDER BY next_forecast_at NULLS FIRST LIMIT 500 FOR UPDATE SKIP LOCKED
                """
            )
        ).all()
        for (sid,) in rows:
            ms = db.get(MonitoredService, sid)
            # claim now so a slow cycle is never double-scheduled; run_cycle sets the real next time
            ms.next_forecast_at = utcnow() + timedelta(seconds=max(ms.forecast_interval_seconds or settings.forecast_interval_seconds, 5))
            claimed.append(str(sid))
    for sid in claimed:
        celery_app.send_task("forecasting.run_cycle", args=[sid], queue="forecasting", expires=max(settings.forecast_interval_seconds, 30) * 2)
    return {"enqueued": len(claimed)}


@celery_app.task(name="forecasting.run_cycle")
def run_cycle_task(service_id: str) -> dict:
    return run_cycle(uuid.UUID(service_id))


@celery_app.task(name="forecasting.after_ingest", bind=True, **RETRY_KWARGS)
def after_ingest(self, project_id: str) -> dict:
    """New data arrived: refresh baselines, learn failure signatures from history, then forecast now."""
    pid = uuid.UUID(project_id)
    with session_scope() as db:
        rows = update_baselines(db, pid)
    with session_scope() as db:
        learned = learn_history(db, pid)
        db.execute(text("UPDATE monitored_services SET next_forecast_at = now() WHERE project_id = :p AND enabled"), {"p": pid})
    return {"baselines": rows, **learned}


@celery_app.task(name="forecasting.learn_outcome", bind=True, **RETRY_KWARGS)
def learn_outcome(self, outcome_id: str) -> dict:
    with session_scope() as db:
        res = learn_from_outcome(db, uuid.UUID(outcome_id))
    return res


@celery_app.task(name="forecasting.draft_pre_mortem", bind=True, **RETRY_KWARGS)
def draft_pre_mortem(self, alert_id: str) -> dict:
    """Critical threshold crossed: run an autonomous RCA, then draft the pre-mortem (human sign-off required)."""
    aid = uuid.UUID(alert_id)
    with session_scope() as db:
        alert = db.get(PreIncidentAlert, aid)
        if alert is None or alert.pre_mortem_report_id is not None:
            return {"skipped": True}
        pid, service, org = alert.project_id, alert.service_name, db.get(Project, alert.project_id).org_id
    rca = None
    try:
        rca = ai.post("/rca", json={"project_id": str(pid), "service": service, "trigger": "autonomous"}, timeout=25)
        with session_scope() as db:
            alert = db.get(PreIncidentAlert, aid)
            ev = dict(alert.evidence_chain or {})
            ev["rca"] = {"id": rca.get("id"), "confidence": rca.get("confidence"), "explanation": rca.get("explanation"),
                         "chain": [c.get("service") for c in rca.get("causal_chain", [])]}
            alert.evidence_chain = ev
    except ServiceError as exc:
        log.warning("autonomous RCA unavailable for %s: %s", service, exc)
    try:
        rep = ai.post("/reports/pre-mortem", json={"alert_id": alert_id, "trigger": "autonomous"}, timeout=75)  # > PREMORTEM_TIMEOUT + margin
    except ServiceError as exc:
        log.warning("pre-mortem drafting failed for %s: %s", service, exc)
        publish_event("report.pre_mortem_failed", {"alert_id": alert_id, "service": service}, project_id=pid, org_id=org)
        raise
    return {"report_id": rep.get("report_id"), "rca": bool(rca)}


_ = thresholds
