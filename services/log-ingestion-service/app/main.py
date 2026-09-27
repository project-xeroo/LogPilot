"""Log Ingestion Service - upload, API and real-time streaming ingestion (PRD tool 01)."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import ingest
from app.config import SERVICE_NAME
from shared.config import settings
from shared.models import LogSession, MalformedRecord
from shared.utils import storage
from shared.utils.db import db_healthy, get_db, init_db
from shared.utils.logsetup import setup_logging
from shared.utils.web import Actor, actor_from_request, require_internal

setup_logging(SERVICE_NAME)
log = logging.getLogger("logpilot.ingestion")

STREAM_FLUSH_RECORDS = 1000
STREAM_FLUSH_SECONDS = 2.0
STREAM_PIPELINE_SECONDS = 30.0


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    storage.ensure_bucket()
    yield


app = FastAPI(title="LogPilot Log Ingestion Service", version="2.0.0", lifespan=lifespan)


@app.get("/health")
def health():
    return {"service": SERVICE_NAME, "status": "ok" if db_healthy() and storage.healthy() else "degraded",
            "database": db_healthy(), "object_storage": storage.healthy()}


def _session_dict(s: LogSession) -> dict:
    return {
        "id": str(s.id), "project_id": str(s.project_id), "filename": s.filename, "status": s.status, "stage": s.stage,
        "progress_pct": round(s.progress_pct or 0, 1), "record_count": s.record_count, "malformed_count": s.malformed_count,
        "size_bytes": s.size_bytes, "source": s.source, "format_detected": s.format_detected,
        "upload_time": s.upload_time.isoformat() if s.upload_time else None,
        "completed_at": s.completed_at.isoformat() if s.completed_at else None,
        "enqueued_at": s.enqueued_at.isoformat() if s.enqueued_at else None,
        "processing_started_at": s.processing_started_at.isoformat() if s.processing_started_at else None,
        "validation_errors": s.validation_errors, "error_message": s.error_message,
        "redaction_counts": s.redaction_counts, "stage_log": s.stage_log,
        "min_timestamp": s.min_timestamp.isoformat() if s.min_timestamp else None,
        "max_timestamp": s.max_timestamp.isoformat() if s.max_timestamp else None,
    }


@app.post("/ingest/upload", dependencies=[Depends(require_internal)], status_code=202)
async def upload(
    request: Request,
    file: UploadFile = File(...),
    project_id: uuid.UUID = Query(...),
    environment: str | None = Query(None),
    default_service: str | None = Query(None),
    custom_pattern: str | None = Query(None),
):
    """Accepts .log .txt .json .csv .zip .gz up to 500MB. Redacts, stores, enqueues - returns immediately
    after the redacted object is durable so processing starts well within 5 seconds."""
    actor: Actor = actor_from_request(request)
    clen = request.headers.get("content-length")
    if clen and int(clen) > settings.max_upload_bytes + 1024 * 1024:
        raise HTTPException(413, f"Upload exceeds the {settings.max_upload_bytes // 1024 // 1024}MB limit.")
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    sid = await run_in_threadpool(
        ingest.create_session, project_id, file.filename or "upload", "file", uploaded_by=actor.user_id,
        environment=environment, default_service=default_service, custom_pattern=custom_pattern, size_bytes=size,
    )
    result = await run_in_threadpool(ingest.process_upload, sid, file.file, file.filename or "upload", size)
    if result["status"] == "failed":
        raise HTTPException(422, detail={"message": "Upload rejected.", "errors": result["errors"], "session_id": result["session_id"]})
    return result


class ApiRecord(BaseModel):
    timestamp: str | float | int | None = None
    message: str
    severity: str | None = None
    level: str | None = None
    service: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    environment: str | None = None
    deployment_version: str | None = None
    attributes: dict = Field(default_factory=dict)


class RecordsBody(BaseModel):
    project_id: uuid.UUID
    environment: str | None = None
    records: list[ApiRecord] = Field(min_length=1, max_length=10_000)


@app.post("/ingest/records", dependencies=[Depends(require_internal)], status_code=202)
async def ingest_records(body: RecordsBody, request: Request):
    """API ingestion: JSON batch of structured records (max 10,000 per call)."""
    actor = actor_from_request(request)
    return await run_in_threadpool(
        ingest.ingest_records, body.project_id, [r.model_dump(exclude_none=True) for r in body.records],
        uploaded_by=actor.user_id, environment=body.environment,
    )


@app.post("/ingest/stream", dependencies=[Depends(require_internal)], status_code=202)
async def ingest_stream(request: Request, project_id: uuid.UUID, environment: str | None = None):
    """Real-time streaming ingestion: a chunked NDJSON request body, one JSON record per line.

    Records are stored (and searchable) within STREAM_FLUSH_SECONDS; the enrichment pipeline
    (embeddings, clustering, anomaly detection) is triggered roughly every STREAM_PIPELINE_SECONDS
    and once more when the stream closes."""
    actor = actor_from_request(request)
    sid = await run_in_threadpool(
        ingest.create_session, project_id, "stream", "stream", uploaded_by=actor.user_id,
        environment=environment, default_service=None, custom_pattern=None,
    )
    pending: list[dict] = []
    tail = b""
    last_flush = last_pipeline = time.monotonic()
    accepted = rejected = 0
    dirty = False

    async def flush(final: bool = False):
        nonlocal pending, accepted, rejected, last_flush, last_pipeline, dirty
        due_pipeline = final or (time.monotonic() - last_pipeline) >= STREAM_PIPELINE_SECONDS
        if pending or due_pipeline:
            res = await run_in_threadpool(
                ingest.ingest_records, project_id, pending, uploaded_by=actor.user_id, environment=environment,
                source="stream", session_id=sid, trigger_pipeline=due_pipeline and (dirty or bool(pending)), final=final,
            )
            accepted += res["accepted"]
            rejected += res["rejected"]
            dirty = dirty or bool(pending)
            if due_pipeline and dirty:
                last_pipeline, dirty = time.monotonic(), False
            pending = []
        last_flush = time.monotonic()

    async for chunk in request.stream():
        data = tail + chunk
        *lines, tail = data.split(b"\n")
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
                pending.append(obj if isinstance(obj, dict) else {"message": str(obj)})
            except json.JSONDecodeError:
                rejected += 1
        if len(pending) >= STREAM_FLUSH_RECORDS or (time.monotonic() - last_flush) >= STREAM_FLUSH_SECONDS:
            await flush()
    if tail.strip():
        try:
            pending.append(json.loads(tail))
        except json.JSONDecodeError:
            rejected += 1
    await flush(final=True)
    return {"session_id": str(sid), "accepted": accepted, "rejected": rejected, "status": "processing"}


@app.get("/ingest/sessions", dependencies=[Depends(require_internal)])
def list_sessions(project_id: uuid.UUID, limit: int = 50, db=Depends(get_db)):
    rows = db.execute(
        select(LogSession).where(LogSession.project_id == project_id).order_by(LogSession.upload_time.desc()).limit(min(limit, 200))
    ).scalars().all()
    return [_session_dict(s) for s in rows]


@app.get("/ingest/sessions/{session_id}", dependencies=[Depends(require_internal)])
def get_session(session_id: uuid.UUID, db=Depends(get_db)):
    s = db.get(LogSession, session_id)
    if not s:
        raise HTTPException(404, "session not found")
    return _session_dict(s)


@app.get("/ingest/sessions/{session_id}/malformed", dependencies=[Depends(require_internal)])
def malformed(session_id: uuid.UUID, limit: int = 100, db=Depends(get_db)):
    rows = db.execute(
        select(MalformedRecord).where(MalformedRecord.session_id == session_id).order_by(MalformedRecord.line_no).limit(min(limit, 1000))
    ).scalars().all()
    return [{"line_no": r.line_no, "reason": r.reason, "raw": r.raw_redacted} for r in rows]


_ = asyncio  # noqa: F841
