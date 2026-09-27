"""Log storage: sessions, records (hypertable), malformed records, templates."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Computed, Float, ForeignKey, Index, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared.models.base import Base, ts, utcnow, uuid_pk


class LogSession(Base):
    """Upload tracking (PRD 7.3 log_sessions)."""

    __tablename__ = "log_sessions"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(500))
    upload_time: Mapped[datetime] = ts(default=utcnow)
    record_count: Mapped[int] = mapped_column(BigInteger, default=0)
    # uploading | queued | parsing | processing | completed | failed
    status: Mapped[str] = mapped_column(String(32), default="uploading", index=True)
    # -- additions for tracking / surfacing validation errors ---
    source: Mapped[str] = mapped_column(String(16), default="file")  # file | api | stream
    stage: Mapped[str | None] = mapped_column(String(48), nullable=True)
    progress_pct: Mapped[float] = mapped_column(Float, default=0.0)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    object_key: Mapped[str | None] = mapped_column(String(600), nullable=True)
    format_detected: Mapped[str | None] = mapped_column(String(32), nullable=True)
    environment: Mapped[str | None] = mapped_column(String(32), nullable=True)
    default_service: Mapped[str | None] = mapped_column(String(200), nullable=True)
    custom_pattern: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    malformed_count: Mapped[int] = mapped_column(BigInteger, default=0)
    redaction_counts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    validation_errors: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    min_timestamp: Mapped[datetime | None] = ts(nullable=True)  # earliest record time in this session
    max_timestamp: Mapped[datetime | None] = ts(nullable=True)
    stage_log: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # per-pipeline-step status/timings
    enqueued_at: Mapped[datetime | None] = ts(nullable=True)
    processing_started_at: Mapped[datetime | None] = ts(nullable=True)
    completed_at: Mapped[datetime | None] = ts(nullable=True)


class LogRecord(Base):
    """Primary structured log storage (PRD 7.3 log_records).

    Converted to a time-series hypertable partitioned by `timestamp` ("date-partitioning for
    efficient range queries"); the partition column is therefore part of the primary key.
    """

    __tablename__ = "log_records"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp: Mapped[datetime] = ts(primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    service: Mapped[str] = mapped_column(String(200))
    severity: Mapped[str] = mapped_column(String(8))
    severity_num: Mapped[int] = mapped_column(SmallInteger)
    message: Mapped[str] = mapped_column(Text)
    embedding_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # --- extracted fields (PRD tool 02) ---
    request_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    environment: Mapped[str | None] = mapped_column(String(32), nullable=True)
    deployment_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    line_no: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    template_hash: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attributes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    message_tsv = mapped_column(TSVECTOR, Computed("to_tsvector('english', coalesce(message, ''))", persisted=True))

    __table_args__ = (
        Index("ix_log_records_proj_ts", "project_id", "timestamp"),
        Index("ix_log_records_svc_ts", "project_id", "service", "timestamp"),
        Index("ix_log_records_sev_ts", "project_id", "severity_num", "timestamp"),
        Index("ix_log_records_trace", "trace_id"),
        Index("ix_log_records_session", "session_id"),
        Index("ix_log_records_template", "project_id", "template_hash"),
        Index("ix_log_records_deploy", "project_id", "service", "deployment_version"),
        Index("ix_log_records_fts", "message_tsv", postgresql_using="gin"),
    )


class MalformedRecord(Base):
    """Malformed records are flagged and stored separately for inspection."""

    __tablename__ = "malformed_records"
    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("log_sessions.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    line_no: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    raw_redacted: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = ts(default=utcnow)


class LogTemplate(Base):
    """Normalised message template. Embeddings are computed per template (not per record) so cost
    scales with distinct error shapes rather than raw volume."""

    __tablename__ = "log_templates"
    __table_args__ = (
        Index("uq_template", "project_id", "template_hash", unique=True),
        Index("ix_template_error", "project_id", "is_error"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    template_hash: Mapped[str] = mapped_column(String(32))
    template: Mapped[str] = mapped_column(Text)
    sample_message: Mapped[str] = mapped_column(Text)
    embedding_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    is_error: Mapped[bool] = mapped_column(Boolean, default=False)
    max_severity: Mapped[str] = mapped_column(String(8), default="INFO")
    first_seen: Mapped[datetime] = ts()
    last_seen: Mapped[datetime] = ts()
    occurrence_count: Mapped[int] = mapped_column(BigInteger, default=0)
    services: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    dedup_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    is_new_in_last_session: Mapped[bool] = mapped_column(Boolean, default=False)
    embedded_at: Mapped[datetime | None] = ts(nullable=True)


class DeploymentEvent(Base):
    __tablename__ = "deployments"
    __table_args__ = (Index("uq_deployment", "project_id", "service", "version", "environment", unique=True),)
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    service: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(100))
    environment: Mapped[str] = mapped_column(String(32), default="production")
    deployed_at: Mapped[datetime] = ts()
    last_seen_at: Mapped[datetime | None] = ts(nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="detected")  # detected | api
    compared: Mapped[bool] = mapped_column(Boolean, default=False)


class DeploymentComparison(Base):
    __tablename__ = "deployment_comparisons"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    service: Mapped[str] = mapped_column(String(200))
    environment: Mapped[str | None] = mapped_column(String(32), nullable=True)
    from_version: Mapped[str] = mapped_column(String(100))
    to_version: Mapped[str] = mapped_column(String(100))
    result: Mapped[dict] = mapped_column(JSONB)
    regression: Mapped[bool] = mapped_column(Boolean, default=False)
    trigger: Mapped[str] = mapped_column(String(16), default="on_request")  # on_request | deploy_event
    created_at: Mapped[datetime] = ts(default=utcnow)


__all__ = [
    "LogSession", "LogRecord", "MalformedRecord", "LogTemplate", "DeploymentEvent", "DeploymentComparison",
]
