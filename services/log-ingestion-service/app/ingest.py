"""Upload / API / streaming ingestion (tool 01).

Boundary rule: raw bytes are never persisted. An upload is read once, every line is redacted
(tool 03) and only the *redacted* text is written to object storage. Parsing later reads from
that redacted object, so PII cannot reach the database, the vector store or any AI provider.
"""
from __future__ import annotations

import gzip
import io
import json
import logging
import uuid
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import BinaryIO

from app.parsers import FILE_MARKER, ParseContext, ParsedRecord, parse_timestamp
from app.parsers.formats import CustomPatternParser
from app.redaction import RedactionStats, redact_lines, redact_record_dict
from app.storage import Repository
from app.validators import UploadRejected, inner_name_ok, looks_binary, validate_declared_size, validate_filename
from shared.config import settings
from shared.models import LogSession, normalize_severity
from shared.models.base import utcnow
from shared.utils import storage
from shared.utils.celery_factory import make_celery
from shared.utils.db import session_scope

log = logging.getLogger("logpilot.ingest")
_sender = make_celery("ingestion-sender")


def object_key(project_id: uuid.UUID, session_id: uuid.UUID, name: str = "redacted.log.gz") -> str:
    return f"sessions/{project_id}/{session_id}/{name}"


# ---- reading --------------------------------------------------------------------------------------------
class _CountingReader:
    """Wraps a binary reader, enforcing a decompressed-size cap (archive-bomb protection)."""

    def __init__(self, raw: BinaryIO, limit: int):
        self.raw, self.limit, self.n = raw, limit, 0

    def read(self, size: int = -1) -> bytes:
        data = self.raw.read(size)
        self.n += len(data)
        if self.n > self.limit:
            raise UploadRejected([f"Decompressed content exceeds the {self.limit // 1024 // 1024 // 1024}GB limit (possible archive bomb)."])
        return data

    def readline(self, size: int = -1) -> bytes:
        data = self.raw.readline(size)
        self.n += len(data)
        if self.n > self.limit:
            raise UploadRejected([f"Decompressed content exceeds the {self.limit // 1024 // 1024 // 1024}GB limit (possible archive bomb)."])
        return data

    def __iter__(self):
        while True:
            line = self.readline(1 << 20)
            if not line:
                return
            yield line


def _decode_lines(raw_lines) -> Iterator[str]:
    for raw in raw_lines:
        yield raw.decode("utf-8", "replace").rstrip("\r\n")


def _iter_upload_lines(fileobj: BinaryIO, filename: str, errors: list[str]) -> Iterator[str]:
    """Yield text lines (with FILE_MARKER separators) for .log/.txt/.json/.csv/.gz/.zip."""
    name = PurePosixPath(filename.replace("\\", "/")).name
    lower = name.lower()
    if lower.endswith(".zip"):
        try:
            zf = zipfile.ZipFile(fileobj)
        except zipfile.BadZipFile:
            raise UploadRejected(["The .zip file is corrupt or not a valid zip archive."])
        infos = [i for i in zf.infolist() if not i.is_dir() and inner_name_ok(i.filename)]
        skipped = [i.filename for i in zf.infolist() if not i.is_dir() and not inner_name_ok(i.filename)]
        if skipped:
            errors.append(f"Skipped {len(skipped)} unsupported file(s) in archive: {', '.join(skipped[:5])}")
        if not infos:
            raise UploadRejected(["The archive contains no supported log files (.log, .txt, .json, .csv, .gz)."])
        if len(infos) > settings.max_zip_entries:
            raise UploadRejected([f"Archive has {len(infos)} files; the maximum is {settings.max_zip_entries}."])
        if sum(i.file_size for i in infos) > settings.max_decompressed_bytes:
            raise UploadRejected(["Archive expands beyond the allowed size (possible archive bomb)."])
        for info in infos:
            with zf.open(info) as inner:
                inner_name = info.filename
                stream: BinaryIO = inner  # type: ignore[assignment]
                if inner_name.lower().endswith(".gz"):
                    stream = gzip.GzipFile(fileobj=inner)  # type: ignore[assignment]
                    inner_name = inner_name[:-3]
                head = stream.read(8192)
                if looks_binary(head):
                    errors.append(f"Skipped binary file '{info.filename}'.")
                    continue
                yield FILE_MARKER + inner_name
                cr = _CountingReader(io.BufferedReader(_Chain(head, stream)), settings.max_decompressed_bytes)  # type: ignore[arg-type]
                yield from _decode_lines(cr)
        return
    if lower.endswith(".gz"):
        inner_name = name[:-3]
        gz = gzip.GzipFile(fileobj=fileobj)
        try:
            head = gz.read(8192)
        except (OSError, EOFError, gzip.BadGzipFile) as exc:
            raise UploadRejected([f"The .gz file could not be decompressed: {exc}"])
        if looks_binary(head):
            raise UploadRejected(["The decompressed content looks binary, not text."])
        yield FILE_MARKER + inner_name
        cr = _CountingReader(io.BufferedReader(_Chain(head, gz)), settings.max_decompressed_bytes)  # type: ignore[arg-type]
        try:
            yield from _decode_lines(cr)
        except (OSError, EOFError, gzip.BadGzipFile) as exc:
            raise UploadRejected([f"The .gz stream is corrupt: {exc}"])
        return
    head = fileobj.read(8192)
    if not head.strip():
        raise UploadRejected(["File is empty."])
    if looks_binary(head):
        raise UploadRejected(["File looks binary, not text."])
    yield FILE_MARKER + name
    cr = _CountingReader(io.BufferedReader(_Chain(head, fileobj), buffer_size=1 << 20), settings.max_decompressed_bytes)  # type: ignore[arg-type]
    yield from _decode_lines(cr)


class _Chain(io.RawIOBase):
    """Re-attach an already-read head to the rest of a stream."""

    def __init__(self, head: bytes, rest):
        self._head, self._rest = head, rest

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        if self._head:
            n = min(len(b), len(self._head))
            b[:n] = self._head[:n]
            self._head = self._head[n:]
            return n
        data = self._rest.read(len(b))
        b[: len(data)] = data
        return len(data)


# ---- entry points ---------------------------------------------------------------------------------------
def create_session(project_id: uuid.UUID, filename: str, source: str, *, uploaded_by: uuid.UUID | None,
                   environment: str | None, default_service: str | None, custom_pattern: str | None,
                   size_bytes: int = 0) -> uuid.UUID:
    with session_scope() as db:
        s = LogSession(
            project_id=project_id, filename=filename[:500], source=source, status="uploading", stage="uploading",
            uploaded_by=uploaded_by, environment=environment, default_service=default_service,
            custom_pattern=custom_pattern, size_bytes=size_bytes,
        )
        db.add(s)
        db.flush()
        return s.id


def _fail(session_id: uuid.UUID, errors: list[str]) -> None:
    with session_scope() as db:
        s = db.get(LogSession, session_id)
        if s:
            s.status, s.stage, s.validation_errors = "failed", "validation", errors
            s.error_message = "; ".join(errors)[:2000]


def process_upload(session_id: uuid.UUID, fileobj: BinaryIO, filename: str, size_bytes: int) -> dict:
    """Validate -> redact -> object storage -> enqueue processing. Runs in a worker thread."""
    with session_scope() as db:
        s = db.get(LogSession, session_id)
        project_id, custom = s.project_id, s.custom_pattern
    warnings: list[str] = []
    try:
        pre = validate_filename(filename)
        pre_size = validate_declared_size(size_bytes)
        if not pre.ok or not pre_size.ok:
            raise UploadRejected(pre.errors + pre_size.errors)
        if custom:
            try:
                CustomPatternParser(custom)
            except (ValueError, Exception) as exc:  # noqa: BLE001 - regex.error is Exception
                raise UploadRejected([f"Invalid custom pattern: {exc}"])
        stats = RedactionStats()
        key = object_key(project_id, session_id)
        nbytes = 0
        with storage.GzipObjectWriter(key) as w:
            for i, line in enumerate(redact_lines(_iter_upload_lines(fileobj, filename, warnings), stats), 1):
                w.write_line(line)
                nbytes += len(line)
                if i % 100_000 == 0:
                    _progress(session_id, stage="redacting", pct=min(19.0, 19.0 * (fileobj.tell() / max(size_bytes, 1))) if hasattr(fileobj, "tell") else 10.0)
        if stats.lines == 0:
            storage.s3().delete_object(Bucket=settings.s3_bucket, Key=key)
            raise UploadRejected(["No readable log lines found in the upload."])
    except UploadRejected as exc:
        _fail(session_id, exc.errors)
        return {"session_id": str(session_id), "status": "failed", "errors": exc.errors}
    except Exception as exc:  # unexpected
        log.exception("upload processing failed")
        _fail(session_id, [f"Internal error while processing upload: {exc}"])
        return {"session_id": str(session_id), "status": "failed", "errors": [str(exc)]}

    with session_scope() as db:
        s = db.get(LogSession, session_id)
        s.status, s.stage, s.progress_pct = "queued", "queued", 20.0
        s.object_key, s.size_bytes = key, size_bytes
        s.redaction_counts = stats.as_dict()
        s.validation_errors = warnings or None  # non-fatal notes (e.g. skipped files)
        s.enqueued_at = utcnow()
    _sender.send_task("ingestion.parse_session", args=[str(session_id)], queue="ingestion")
    return {"session_id": str(session_id), "status": "queued", "warnings": warnings, "redactions": stats.as_dict()}


def _progress(session_id: uuid.UUID, *, stage: str, pct: float) -> None:
    with session_scope() as db:
        s = db.get(LogSession, session_id)
        if s:
            s.stage, s.progress_pct = stage, pct


# ---- API & streaming ingestion --------------------------------------------------------------------------
def _record_from_api(raw: dict, i: int, ctx: ParseContext) -> ParsedRecord | None:
    """`raw` must already be redacted."""
    ts = parse_timestamp(raw.get("timestamp") or raw.get("ts") or raw.get("time"), ctx) or datetime.now(UTC)
    msg = raw.get("message") or raw.get("msg")
    if not msg:
        return None
    attrs = raw.get("attributes") if isinstance(raw.get("attributes"), dict) else {}
    return ParsedRecord(
        timestamp=ts, message=str(msg)[:8000], severity=normalize_severity(raw.get("severity") or raw.get("level")),
        service=(raw.get("service") or ctx.default_service or "unknown")[:200],
        request_id=raw.get("request_id"), trace_id=raw.get("trace_id"),
        environment=raw.get("environment") or ctx.environment,
        deployment_version=raw.get("deployment_version") or raw.get("version"),
        attributes=attrs, line_no=i,
    )


def ingest_records(project_id: uuid.UUID, raw_records: list[dict], *, uploaded_by: uuid.UUID | None, environment: str | None,
                   source: str = "api", session_id: uuid.UUID | None = None, trigger_pipeline: bool = True,
                   final: bool = True, label: str = "api-batch") -> dict:
    """Store a batch of structured records. Used by POST /ingest/records and the streaming endpoints."""
    if session_id is None:
        session_id = create_session(project_id, label, source, uploaded_by=uploaded_by, environment=environment,
                                    default_service=None, custom_pattern=None)
    stats = RedactionStats()
    ctx = ParseContext(environment=environment)
    parsed, malformed, clean_rows = [], 0, []
    for i, r in enumerate(raw_records, 1):
        clean = redact_record_dict(r, stats) if isinstance(r, dict) else None  # redact BEFORE anything is stored
        rec = _record_from_api(clean, i, ctx) if clean is not None else None
        if rec is None:
            malformed += 1
        else:
            parsed.append(rec)
            clean_rows.append(json.dumps(clean, default=str))
    with session_scope() as db:
        s = db.get(LogSession, session_id)
        repo = Repository(db, s)
        repo.insert_records(parsed)
        s.record_count += len(parsed)
        s.malformed_count += malformed
        s.min_timestamp = min(filter(None, [s.min_timestamp, repo.stats.min_ts]), default=None)
        s.max_timestamp = max(filter(None, [s.max_timestamp, repo.stats.max_ts]), default=None)
        prev = s.redaction_counts or {"by_type": {}, "total": 0, "lines": 0, "lines_with_pii": 0}
        merged = dict(prev.get("by_type", {}))
        for k, v in stats.counts.items():
            merged[k] = merged.get(k, 0) + v
        s.redaction_counts = {"by_type": merged, "total": sum(merged.values()), "lines": prev["lines"] + stats.lines,
                              "lines_with_pii": prev["lines_with_pii"] + stats.lines_with_pii}
        if repo.stats.new_deployments:
            log_ = dict(s.stage_log or {})
            log_["new_deployments"] = (log_.get("new_deployments") or []) + [list(d) for d in repo.stats.new_deployments]
            s.stage_log = log_
        pid, rc = s.project_id, s.record_count
    # keep a redacted object-store copy of every API batch (object storage is the system of record for raw-ish data)
    try:
        storage.put_gzip_lines(object_key(project_id, session_id, f"batch-{uuid.uuid4().hex[:8]}.ndjson.gz"), clean_rows)
    except Exception:  # pragma: no cover - object store hiccup must not drop already-stored records
        log.warning("could not archive api batch to object storage", exc_info=True)
    if trigger_pipeline:
        with session_scope() as db:
            s = db.get(LogSession, session_id)
            s.status = "processing" if final else "streaming"
            s.stage, s.progress_pct, s.enqueued_at = "queued", 55.0, utcnow()
            s.format_detected = s.format_detected or "api"
        _sender.send_task("processing.run_pipeline", args=[str(session_id)], kwargs={"final": final}, queue="processing")
    return {"session_id": str(session_id), "accepted": len(parsed), "rejected": malformed, "record_count": rc,
            "status": ("processing" if final else "streaming") if trigger_pipeline else "streaming", "redactions": stats.as_dict(), "project_id": str(pid)}
