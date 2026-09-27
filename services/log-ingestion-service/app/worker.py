"""Celery worker for the `ingestion` queue: parse -> store, then hand off to the processing pipeline."""
from __future__ import annotations

import logging

from app.parsers import Malformed, ParseContext, dominant_format, parse_stream
from app.storage import Repository
from shared.config import settings
from shared.models import LogSession
from shared.models.base import utcnow
from shared.utils import storage
from shared.utils.celery_factory import RETRY_KWARGS, make_celery
from shared.utils.db import SessionLocal, init_db, session_scope
from shared.utils.logsetup import setup_logging

setup_logging("ingestion-worker")
log = logging.getLogger("logpilot.ingestion.worker")
celery_app = make_celery("ingestion-worker")


def _progress(session_id, *, stage: str, pct: float) -> None:
    with session_scope() as db:
        s = db.get(LogSession, session_id)
        if s:
            s.stage, s.progress_pct = stage, pct


@celery_app.task(name="ingestion.parse_session", bind=True, **RETRY_KWARGS)
def parse_session(self, session_id: str) -> dict:
    """Tool 02 (parsing) + tool 04 (structured storage).

    The whole session is written in ONE transaction so a retry after a crash never leaves
    duplicate records or double-counted templates."""
    import uuid

    sid = uuid.UUID(session_id)
    init_db()
    with session_scope() as db:
        s = db.get(LogSession, sid)
        if s is None or not s.object_key:
            raise ValueError(f"session {session_id} missing or has no stored object")
        s.status, s.stage, s.processing_started_at = "parsing", "parsing", utcnow()
        key, total_lines = s.object_key, (s.redaction_counts or {}).get("lines") or 0

    db = SessionLocal()
    try:
        s = db.get(LogSession, sid)
        ctx = ParseContext(
            default_service=s.default_service, environment=s.environment,
            base_time=s.upload_time, custom_pattern=s.custom_pattern,
        )
        repo = Repository(db, s)
        formats: list[str] = []
        batch, bad = [], []
        last_line = 0
        for item in parse_stream(storage.iter_gzip_lines(key), ctx, formats):
            if isinstance(item, Malformed):
                bad.append(item)
                last_line = max(last_line, item.line_no)
            else:
                batch.append(item)
                last_line = max(last_line, item.line_no)
            if len(batch) >= settings.ingest_batch_size:
                repo.insert_records(batch)
                batch = []
                if total_lines:
                    _progress(sid, stage="parsing", pct=20.0 + 35.0 * min(1.0, last_line / total_lines))
            if len(bad) >= 500:
                repo.store_malformed(bad)
                bad = []
        repo.insert_records(batch)
        repo.store_malformed(bad)
        st = repo.stats
        s.record_count = st.records
        s.malformed_count = st.malformed
        s.format_detected = dominant_format(formats)
        s.min_timestamp, s.max_timestamp = st.min_ts, st.max_ts
        s.status, s.stage, s.progress_pct = "processing", "parsed", 55.0
        s.stage_log = {**(s.stage_log or {}), "new_deployments": [list(d) for d in st.new_deployments],
                       "formats": sorted(set(formats))}
        db.commit()
        result = {"session_id": session_id, "records": st.records, "malformed": st.malformed, "format": s.format_detected}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    if result["records"] == 0:
        with session_scope() as d2:
            s2 = d2.get(LogSession, sid)
            s2.status, s2.stage, s2.progress_pct = "failed", "parsing", 100.0
            s2.error_message = "No records could be parsed from this file. See malformed records for details."
        return result

    celery_app.send_task("processing.run_pipeline", args=[session_id], queue="processing")
    log.info("session %s parsed: %s", session_id, result)
    return result
