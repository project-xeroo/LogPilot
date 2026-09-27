"""The processing pipeline: embed -> deduplicate -> cluster -> detect anomalies -> finalize.

`processing.run_pipeline` is enqueued by the ingestion service (by task *name*, so services never
import each other). It builds a Celery chain so each step is retried independently with
exponential backoff (PRD 10.4)."""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from celery import Task, chain
from sqlalchemy import func, select

from app.anomaly import detect_project_window
from app.clustering import cluster_project
from app.deduplication import deduplicate_project, group_stats
from app.embeddings import embed_pending_templates
from app.tasks.common import audit_tool, set_stage, timed_stage
from shared.config import settings
from shared.models import Anomaly, ErrorCluster, LogSession, LogTemplate, MonitoredService, Project
from shared.models.base import utcnow
from shared.utils.celery_factory import RETRY_KWARGS
from shared.utils.clients import ServiceError, ai
from shared.utils.db import session_scope
from shared.utils.events import add_feed_item, publish_event

log = logging.getLogger("logpilot.pipeline")


def register(celery_app):
    class PipelineTask(Task):
        def on_failure(self, exc, task_id, args, kwargs, einfo):  # after retries are exhausted
            if args:
                try:
                    with session_scope() as db:
                        s = db.get(LogSession, uuid.UUID(args[0]))
                        if s and s.status != "completed":
                            s.status, s.stage = "failed", f"{self.name.split('.')[-1]} failed"
                            s.error_message = f"{type(exc).__name__}: {exc}"[:2000]
                    publish_event("ingestion.progress", {"session_id": args[0], "status": "failed", "stage": self.name}, project_id=None)
                except Exception:  # pragma: no cover
                    log.exception("could not record pipeline failure")

    def _project_of(session_id: str) -> uuid.UUID:
        with session_scope() as db:
            return db.get(LogSession, uuid.UUID(session_id)).project_id

    @celery_app.task(name="processing.run_pipeline")
    def run_pipeline(session_id: str, final: bool = True) -> str:
        sig = celery_app.signature
        chain(
            sig("processing.embed_templates", args=(session_id,), immutable=True),
            sig("processing.deduplicate", args=(session_id,), immutable=True),
            sig("processing.cluster", args=(session_id,), immutable=True),
            sig("processing.detect_anomalies", args=(session_id,), immutable=True),
            sig("processing.finalize", args=(session_id, final), immutable=True),
        ).apply_async()
        return session_id

    @celery_app.task(name="processing.embed_templates", base=PipelineTask, bind=True, **RETRY_KWARGS)
    def embed_templates(self, session_id: str) -> dict:
        pid = _project_of(session_id)
        with timed_stage(session_id, "embedding"):
            with session_scope() as db:
                res = embed_pending_templates(db, pid)
        audit_tool(session_id, "structured_storage", f"Indexed {res['embedded']} new message templates in the vector store", res)
        return res

    @celery_app.task(name="processing.deduplicate", base=PipelineTask, bind=True, **RETRY_KWARGS)
    def deduplicate(self, session_id: str) -> dict:
        pid = _project_of(session_id)
        with timed_stage(session_id, "deduplication"):
            with session_scope() as db:
                res = deduplicate_project(db, pid)
                res.update(group_stats(db, pid))
        audit_tool(session_id, "error_deduplication", f"{res['merged']} error variants merged into {res['groups']} groups", res)
        return res

    @celery_app.task(name="processing.cluster", base=PipelineTask, bind=True, **RETRY_KWARGS)
    def cluster(self, session_id: str) -> dict:
        pid = _project_of(session_id)
        with timed_stage(session_id, "clustering"):
            with session_scope() as db:
                res = cluster_project(db, pid)
        audit_tool(session_id, "error_clustering", f"{res.get('clusters', 0)} error clusters (silhouette {res.get('silhouette')})", res)
        return res

    @celery_app.task(name="processing.detect_anomalies", base=PipelineTask, bind=True, **RETRY_KWARGS)
    def detect_anomalies(self, session_id: str) -> dict:
        sid = uuid.UUID(session_id)
        with timed_stage(session_id, "anomaly_detection"):
            with session_scope() as db:
                s = db.get(LogSession, sid)
                pid, lo, hi = s.project_id, s.min_timestamp, s.max_timestamp
                if lo is None or hi is None:
                    return {"anomalies": 0}
                services = db.execute(
                    select(MonitoredService.name, MonitoredService.environment).where(MonitoredService.project_id == pid, MonitoredService.enabled)
                ).all()
                # only services present in this session's window
                svc_lists = db.execute(
                    select(LogTemplate.services).where(LogTemplate.project_id == pid, LogTemplate.last_seen >= lo)
                ).scalars().all()
                present = {svc for svcs in svc_lists for svc in (svcs or [])}
                targets = [(n, e) for n, e in services if n in present] or list(services)
                detected = detect_project_window(db, pid, lo, hi, targets)
                stored, recent = _persist(db, pid, sid, detected, hi)
                db.execute(
                    LogTemplate.__table__.update().where(LogTemplate.project_id == pid, LogTemplate.is_new_in_last_session.is_(True)).values(is_new_in_last_session=False)
                )
        audit_tool(session_id, "anomaly_detection", f"{stored} anomalies detected ({recent} recent)", {"stored": stored, "recent": recent})
        return {"anomalies": stored, "recent": recent}

    @celery_app.task(name="processing.finalize", base=PipelineTask, bind=True, **RETRY_KWARGS)
    def finalize(self, session_id: str, final: bool = True) -> dict:
        sid = uuid.UUID(session_id)
        set_stage(session_id, "finalizing")
        with session_scope() as db:
            s = db.get(LogSession, sid)
            pid = s.project_id
            org = db.get(Project, pid).org_id
            new_deploys = list((s.stage_log or {}).get("new_deployments") or [])
            if final:
                s.status, s.stage, s.progress_pct, s.completed_at = "completed", "completed", 100.0, utcnow()
            else:
                s.stage = "streaming"
            n_clusters = db.execute(select(func.count(ErrorCluster.id)).where(ErrorCluster.project_id == pid, ErrorCluster.status == "active")).scalar()
            n_anom = db.execute(select(func.count(Anomaly.id)).where(Anomaly.session_id == sid)).scalar()
            summary = {
                "filename": s.filename, "records": s.record_count, "malformed": s.malformed_count,
                "format": s.format_detected, "clusters": n_clusters, "anomalies": n_anom,
                "redactions": (s.redaction_counts or {}).get("total", 0),
            }
            if final:
                red = summary["redactions"]
                add_feed_item(
                    db, project_id=pid, org_id=org, kind="ingestion", severity="info", ref_type="log_session", ref_id=sid,
                    title=f"Finished processing {s.filename}: {s.record_count:,} records",
                    body=(f"Parsed as {s.format_detected}; {s.malformed_count} malformed lines set aside; {red} sensitive values redacted "
                          f"before storage; {n_clusters} error clusters active; {n_anom} anomalies flagged."),
                    meta=summary,
                )
        if final:
            audit_tool(session_id, "log_ingestion", f"Ingested {summary['records']:,} records from {summary['filename']}", summary)
            audit_tool(session_id, "log_parsing", f"Parsed as {summary['format']}; {summary['malformed']} malformed", summary)
            audit_tool(session_id, "pii_redaction", f"Redacted {summary['redactions']} sensitive values before storage", summary)
            for svc, ver, env in new_deploys:
                celery_app.send_task("deployment.compare_on_deploy", args=[str(pid), svc, env, ver], queue="processing")
        # hand fresh data to the forecasting subsystem: refresh baselines + learn failure signatures
        celery_app.send_task("forecasting.after_ingest", args=[str(pid)], queue="forecasting")
        publish_event("ingestion.progress", {"session_id": session_id, "status": "completed" if final else "streaming",
                                             "stage": "completed" if final else "streaming", "progress_pct": 100.0 if final else 99.0,
                                             "summary": summary}, project_id=pid, org_id=org)
        return summary

    return dict(run_pipeline=run_pipeline, embed_templates=embed_templates, deduplicate=deduplicate, cluster=cluster,
                detect_anomalies=detect_anomalies, finalize=finalize)


def _persist(db, project_id, session_id, detected, ref_end) -> tuple[int, int]:
    """Store anomalies (merging overlaps), upgrade explanations of recent ones via the fast model,
    and surface recent significant ones in the Agent Feed."""
    recent_cut = ref_end - timedelta(minutes=settings.anomaly_recent_minutes)
    org = db.get(Project, project_id).org_id
    stored = recent = 0
    explain_budget = 5
    for d in sorted(detected, key=lambda x: x.window_end, reverse=True):
        existing = db.execute(
            select(Anomaly).where(
                Anomaly.project_id == project_id, Anomaly.service == d.service, Anomaly.kind == d.kind,
                Anomaly.window_start <= d.window_end, Anomaly.window_end >= d.window_start,
            ).limit(1)
        ).scalar_one_or_none()
        is_recent = d.window_end >= recent_cut
        if existing is None and d.kind == "new_error_type":
            existing = db.execute(
                select(Anomaly).where(Anomaly.project_id == project_id, Anomaly.kind == "new_error_type",
                                      Anomaly.evidence["template_id"].astext == d.evidence.get("template_id"))
            ).scalar_one_or_none()
        if is_recent and explain_budget > 0 and d.severity != "low":
            explain_budget -= 1
            try:
                res = ai.post("/internal/explain-anomaly", json={"kind": d.kind, "service": d.service, "explanation": d.explanation,
                                                                   "observed": d.observed, "expected": d.expected, "evidence": d.evidence}, timeout=20)
                d.explanation = res.get("explanation") or d.explanation
            except ServiceError:
                pass
        if existing:
            existing.window_start, existing.window_end = min(existing.window_start, d.window_start), max(existing.window_end, d.window_end)
            existing.score, existing.observed, existing.expected = max(existing.score, d.score), d.observed, d.expected
            existing.explanation, existing.evidence, existing.severity = d.explanation, d.evidence, d.severity
            continue
        a = Anomaly(
            project_id=project_id, service=d.service, kind=d.kind, severity=d.severity, window_start=d.window_start,
            window_end=d.window_end, score=d.score, observed=d.observed, expected=d.expected, method=d.method,
            explanation=d.explanation, evidence=d.evidence, session_id=session_id,
        )
        db.add(a)
        db.flush()
        stored += 1
        if is_recent and d.severity in ("medium", "high"):
            recent += 1
            add_feed_item(
                db, project_id=project_id, org_id=org, kind="anomaly", severity="warning" if d.severity == "high" else "info",
                title=f"{d.kind.replace('_', ' ').title()} in {d.service}", body=d.explanation, ref_type="anomaly", ref_id=a.id,
                service=d.service,
            )
            publish_event("anomaly.detected", {"id": str(a.id), "kind": d.kind, "service": d.service, "severity": d.severity,
                                               "explanation": d.explanation}, project_id=project_id, org_id=org)
    return stored, recent
