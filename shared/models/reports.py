"""Reports (incident, pre-mortem, executive summary) and RCA results."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Float, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared.models.base import Base, ts, utcnow, uuid_pk


class IncidentReport(Base):
    """Generated reports (PRD 7.3 incident_reports)."""

    __tablename__ = "incident_reports"
    __table_args__ = (Index("ix_reports_project", "project_id", "created_at"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    title: Mapped[str] = mapped_column(String(500))
    sections_json: Mapped[dict] = mapped_column(JSONB)  # {"sections":[{key,title,body}], ...}
    export_format: Mapped[str | None] = mapped_column(String(16), nullable=True)  # last exported: pdf | markdown
    created_at: Mapped[datetime] = ts(default=utcnow)
    # additions
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(20), default="incident")  # incident | pre_mortem | executive_summary
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | approved | discarded
    alert_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    rca_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    generated_by: Mapped[str] = mapped_column(String(16), default="agent")  # agent | user
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    approved_at: Mapped[datetime | None] = ts(nullable=True)
    generation_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_info: Mapped[str | None] = mapped_column(String(200), nullable=True)
    updated_at: Mapped[datetime] = ts(default=utcnow, onupdate=utcnow)


class RcaResult(Base):
    __tablename__ = "rca_results"
    __table_args__ = (Index("ix_rca_project", "project_id", "created_at"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    service: Mapped[str | None] = mapped_column(String(200), nullable=True)
    window_start: Mapped[datetime] = ts()
    window_end: Mapped[datetime] = ts()
    causal_chain: Mapped[list] = mapped_column(JSONB, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    explanation: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list] = mapped_column(JSONB, default=list)
    dependency_graph: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    review_status: Mapped[str] = mapped_column(String(16), default="pending_review")  # pending_review | approved | rejected
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    trigger: Mapped[str] = mapped_column(String(16), default="on_request")  # on_request | autonomous
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = ts(default=utcnow)
