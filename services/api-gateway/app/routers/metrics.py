"""Success metrics (PRD Section 12): user-facing, technical, and the cloud-cost metric."""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import numpy as np
from fastapi import APIRouter, Depends
from sqlalchemy import Integer, cast, func, select, text
from sqlalchemy.orm import Session

from app.auth import Principal, project_perm
from shared.models import ChatMessage, ChatThread, ClusterHistory, IncidentOutcome, IncidentReport, LogSession, PreIncidentAlert, RcaResult
from shared.models.base import utcnow
from shared.utils import clients
from shared.utils.clients import ServiceError
from shared.utils.db import get_db
from shared.utils.events import get_redis
from shared.utils.pii_selftest import detection_rate

router = APIRouter(prefix="/projects/{project_id}/metrics", tags=["metrics"])


def _m(name: str, value: Any, target: str, ok: bool | None, unit: str = "", detail: str | None = None) -> dict:
    return {"metric": name, "value": None if value is None else (round(value, 3) if isinstance(value, float) else value), "unit": unit, "target": target,
            "status": "no_data" if value is None else ("meets" if ok else "misses"), "detail": detail}


def _samples(mode: str) -> list[float]:
    try:
        return [float(x) for x in get_redis().lrange(f"metrics:search:{mode}", 0, 999)]
    except Exception:
        return []


@router.get("")
def metrics(project_id: uuid.UUID, days: int = 30, p: Principal = Depends(project_perm("health.view")), db: Session = Depends(get_db)):
    since = utcnow() - timedelta(days=days)

    # ---- user-facing --------------------------------------------------------------------------------------------------------
    rcas = db.execute(select(RcaResult).where(RcaResult.project_id == project_id, RcaResult.created_at >= since)).scalars().all()
    deltas = []
    for r in rcas:
        s = db.execute(select(LogSession.completed_at).where(LogSession.project_id == project_id, LogSession.completed_at.is_not(None), LogSession.completed_at <= r.created_at)
                       .order_by(LogSession.completed_at.desc()).limit(1)).scalar()
        if s:
            deltas.append((r.created_at - s).total_seconds() / 60)
    ttr = float(np.median(deltas)) if deltas else None

    gen = [g for (g,) in db.execute(select(IncidentReport.generation_ms).where(IncidentReport.project_id == project_id, IncidentReport.kind == "incident",
                                                                              IncidentReport.created_at >= since, IncidentReport.generation_ms.is_not(None)))]
    rep = float(np.median(gen)) / 60000 if gen else None

    outs = db.execute(select(IncidentOutcome).join(PreIncidentAlert, PreIncidentAlert.id == IncidentOutcome.alert_id)
                      .where(PreIncidentAlert.project_id == project_id, IncidentOutcome.created_at >= since)).scalars().all()
    acc = (sum(1 for o in outs if o.alert_accurate) / len(outs)) if outs else None
    prevented = sum(1 for o in outs if o.outcome == "prevented")
    occurred = sum(1 for o in outs if o.outcome == "occurred")
    prev_rate = prevented / (prevented + occurred) if (prevented + occurred) else None

    rated = db.execute(select(func.count(ChatMessage.id), func.sum(cast(ChatMessage.rating == 1, Integer)))
                       .join(ChatThread, ChatThread.id == ChatMessage.thread_id).where(ChatThread.project_id == project_id, ChatMessage.rating.is_not(None),
                                                                                       ChatMessage.created_at >= since)).one()
    sat = (int(rated[1] or 0) / rated[0]) if rated[0] else None

    user_facing = [
        _m("Time to identify root cause", ttr, "< 5 min", ttr is not None and ttr < 5, "min", "Median time from ingestion completing to an RCA result"),
        _m("Incident report generation time", rep, "< 2 min", rep is not None and rep < 2, "min", f"{len(gen)} report(s)"),
        _m("Forecasting alert accuracy", acc, "> 70% true positive", acc is not None and acc > 0.70, "ratio", f"{len(outs)} labelled outcome(s)"),
        _m("Incident prevention rate", prev_rate, "> 30% of alerted incidents prevented", prev_rate is not None and prev_rate > 0.30, "ratio", f"{prevented} prevented / {occurred} occurred"),
        _m("AI chat satisfaction", sat, "> 80% helpful", sat is not None and sat > 0.80, "ratio", f"{rated[0]} rated answer(s)"),
    ]

    # ---- technical ----------------------------------------------------------------------------------------------------------
    sess = db.execute(select(LogSession).where(LogSession.project_id == project_id, LogSession.status == "completed", LogSession.completed_at >= since,
                                               LogSession.processing_started_at.is_not(None), LogSession.record_count > 1000)).scalars().all()
    rates = [s.record_count / max((s.completed_at - s.processing_started_at).total_seconds() / 60, 1 / 60) for s in sess]
    thr = float(np.median(rates)) if rates else None
    sem, kw = _samples("semantic"), _samples("keyword")
    sem99 = float(np.percentile(sem, 99)) / 1000 if sem else None
    kw99 = float(np.percentile(kw, 99)) / 1000 if kw else None
    sil = db.execute(select(ClusterHistory.silhouette).where(ClusterHistory.project_id == project_id, ClusterHistory.silhouette.is_not(None)).order_by(ClusterHistory.timestamp.desc()).limit(1)).scalar()
    pii = detection_rate()
    fc: dict = {}
    try:
        fc = clients.forecasting.get("/metrics", params={"project_id": str(project_id), "hours": days * 24})
    except ServiceError:
        pass
    usage: dict = {}
    try:
        usage = clients.ai.get("/internal/usage")
    except ServiceError:
        pass
    lat = usage.get("latency_ms", {})
    p95_fast = (lat.get("fast") or {}).get("p95")
    p95_deep = (lat.get("deep") or {}).get("p95")

    technical = [
        _m("Log processing throughput", thr, "> 10,000 records/min", thr is not None and thr > 10_000, "records/min", f"{len(rates)} completed session(s)"),
        _m("Semantic search latency (p99)", sem99, "< 1.5 s", sem99 is not None and sem99 < 1.5, "s", f"{len(sem)} sample(s)"),
        _m("Keyword search latency (p99)", kw99, "< 0.5 s", kw99 is not None and kw99 < 0.5, "s", f"{len(kw)} sample(s)"),
        _m("Clustering accuracy (silhouette)", float(sil) if sil is not None else None, "> 0.65", sil is not None and sil > 0.65, "score"),
        _m("PII detection rate", pii["rate"], "> 95%", pii["rate"] > 0.95, "ratio", f"self-test on {pii['cases']} labelled cases"),
        _m("Forecasting cycle completion rate", fc.get("completion_rate"), "> 99% within 60s", (fc.get("completion_rate") or 0) > 0.99, "ratio", f"{fc.get('cycles', 0)} cycle(s); avg {fc.get('avg_ms', 0):.0f} ms"),
        _m("AI response time p95 (chat / fast model)", p95_fast / 1000 if p95_fast else None, "< 12 s", bool(p95_fast) and p95_fast / 1000 < 12, "s"),
        _m("AI response time p95 (RCA / deep model)", p95_deep / 1000 if p95_deep else None, "< 20 s", bool(p95_deep) and p95_deep / 1000 < 20, "s"),
    ]

    # ---- cloud cost -----------------------------------------------------------------------------------------------------------
    events = db.execute(text("SELECT coalesce(sum(record_count), 0) FROM log_sessions WHERE project_id = :p"), {"p": project_id}).scalar() or 0
    tokens = sum((usage.get("approx_tokens_in") or {}).values()) + sum((usage.get("approx_tokens_out") or {}).values()) if usage else None
    per_k = (tokens / (events / 1000)) if tokens is not None and events else None
    cost = [_m("AI API tokens per 1,000 monitored log events", per_k, "flat or declining as volume scales", per_k is not None, "tokens/1k events",
               "Usage-based: no fixed GPU cost. Embeddings are computed per distinct message template, not per record.")]
    return {"window_days": days, "user_facing": user_facing, "technical": technical, "cloud_cost": cost}
