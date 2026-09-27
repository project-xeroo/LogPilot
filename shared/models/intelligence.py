"""Derived intelligence: dedup groups, clusters, baselines, anomalies."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared.models.base import Base, ts, utcnow, uuid_pk


class DedupEvent(Base):
    """Deduplicated error groups (PRD 7.3 dedup_events)."""

    __tablename__ = "dedup_events"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    canonical_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))  # log_templates.id of the canonical form
    canonical_message: Mapped[str] = mapped_column(Text)
    occurrence_count: Mapped[int] = mapped_column(BigInteger, default=0)
    first_seen: Mapped[datetime] = ts()
    last_seen: Mapped[datetime] = ts()
    affected_services: Mapped[list] = mapped_column(JSONB, default=list)
    variant_count: Mapped[int] = mapped_column(Integer, default=1)


class ErrorCluster(Base):
    """Clustering results (PRD 7.3 error_clusters)."""

    __tablename__ = "error_clusters"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    label: Mapped[str] = mapped_column(String(300))
    centroid_embedding: Mapped[list[float]] = mapped_column(ARRAY(Float))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    # --- tracking over time / drift signal for the forecasting loop ---
    occurrence_count: Mapped[int] = mapped_column(BigInteger, default=0)
    services: Mapped[list] = mapped_column(JSONB, default=list)
    first_seen: Mapped[datetime | None] = ts(nullable=True)
    last_seen: Mapped[datetime | None] = ts(nullable=True)
    drift_score: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = ts(default=utcnow)
    updated_at: Mapped[datetime] = ts(default=utcnow, onupdate=utcnow)


class ClusterHistory(Base):
    __tablename__ = "cluster_history"
    __table_args__ = (Index("ix_cluster_history", "cluster_id", "timestamp"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    cluster_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("error_clusters.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    timestamp: Mapped[datetime] = ts(default=utcnow)
    centroid_embedding: Mapped[list[float]] = mapped_column(ARRAY(Float))
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    drift_from_previous: Mapped[float] = mapped_column(Float, default=0.0)
    silhouette: Mapped[float | None] = mapped_column(Float, nullable=True)


class ServiceBaseline(Base):
    """Normal behaviour profile per service per (day-of-week, hour) window (PRD 7.3 service_baselines)."""

    __tablename__ = "service_baselines"
    service_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("monitored_services.id", ondelete="CASCADE"), primary_key=True
    )
    window: Mapped[str] = mapped_column(String(16), primary_key=True)  # "<dow>:<hour>" e.g. "2:14"; "all" fallback
    error_rate_mean: Mapped[float] = mapped_column(Float, default=0.0)  # errors / minute
    error_rate_std: Mapped[float] = mapped_column(Float, default=0.0)
    pattern_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # additions
    request_rate_mean: Mapped[float] = mapped_column(Float, default=0.0)  # records / minute
    request_rate_std: Mapped[float] = mapped_column(Float, default=0.0)
    latency_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_std: Mapped[float | None] = mapped_column(Float, nullable=True)
    pattern_dist: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # template_hash -> share of errors
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = ts(default=utcnow, onupdate=utcnow)


class Anomaly(Base):
    __tablename__ = "anomalies"
    __table_args__ = (Index("ix_anomaly_proj_time", "project_id", "detected_at"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    service: Mapped[str] = mapped_column(String(200))
    # error_spike | latency_spike | traffic_spike | new_error_type | service_silence
    kind: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16), default="medium")
    detected_at: Mapped[datetime] = ts(default=utcnow)
    window_start: Mapped[datetime] = ts()
    window_end: Mapped[datetime] = ts()
    score: Mapped[float] = mapped_column(Float, default=0.0)
    observed: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected: Mapped[float | None] = mapped_column(Float, nullable=True)
    method: Mapped[str] = mapped_column(String(32), default="baseline_zscore")  # or isolation_forest
    explanation: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
