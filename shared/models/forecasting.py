"""Forecasting state: risk snapshots, alerts, outcomes, signatures, learned weights."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared.models.base import Base, ts, utcnow, uuid_pk


class RiskSnapshot(Base):
    """Forecasting history (PRD 7.3 risk_snapshots)."""

    __tablename__ = "risk_snapshots"
    __table_args__ = (Index("ix_risk_service_time", "service_id", "timestamp"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monitored_services.id", ondelete="CASCADE"))
    timestamp: Mapped[datetime] = ts(default=utcnow)
    risk_score: Mapped[float] = mapped_column(Float)
    velocity_score: Mapped[float] = mapped_column(Float, default=0.0)
    similarity_score: Mapped[float] = mapped_column(Float, default=0.0)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    # additions
    baseline_score: Mapped[float] = mapped_column(Float, default=0.0)
    trend: Mapped[str] = mapped_column(String(12), default="steady")  # rising | falling | steady
    eta_minutes_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    eta_minutes_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    signals: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # full signal breakdown + evidence
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)  # AI unavailable -> threshold fallback
    cycle_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class PreIncidentAlert(Base):
    """Alert records (PRD 7.3 pre_incident_alerts)."""

    __tablename__ = "pre_incident_alerts"
    __table_args__ = (Index("ix_alert_service_open", "service_id", "resolved_at"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monitored_services.id", ondelete="CASCADE"))
    risk_score: Mapped[float] = mapped_column(Float)
    alert_text: Mapped[str] = mapped_column(Text)
    recommended_actions: Mapped[list] = mapped_column(JSONB, default=list)
    resolved_at: Mapped[datetime | None] = ts(nullable=True)
    # additions
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    service_name: Mapped[str] = mapped_column(String(200))
    level: Mapped[str] = mapped_column(String(12), default="warning")  # warning | critical
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | acknowledged | resolved | dismissed
    failure_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    eta_minutes_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    eta_minutes_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_chain: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # what pattern matched, confidence, similar events
    pattern_matches: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    pre_mortem_report_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = ts(default=utcnow)
    updated_at: Mapped[datetime] = ts(default=utcnow, onupdate=utcnow)
    peak_risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    below_threshold_cycles: Mapped[int] = mapped_column(Integer, default=0)


class RecommendedAction(Base):
    """Propose-only actions awaiting a human decision (Alerts & Approvals Queue)."""

    __tablename__ = "recommended_actions"
    __table_args__ = (Index("ix_action_status", "project_id", "status"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    alert_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pre_incident_alerts.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    service_name: Mapped[str] = mapped_column(String(200))
    rank: Mapped[int] = mapped_column(Integer, default=1)
    text: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(24), default="llm")  # historical | llm | playbook
    risk_level: Mapped[str] = mapped_column(String(8), default="low")  # low | high
    autonomy_tier: Mapped[str] = mapped_column(String(32), default="propose_only")
    # proposed | approved | edited | dismissed | executed
    status: Mapped[str] = mapped_column(String(16), default="proposed")
    edited_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = ts(nullable=True)
    dismiss_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = ts(default=utcnow)


class IncidentOutcome(Base):
    """Feedback for agent self-improvement (PRD 7.3 incident_outcomes)."""

    __tablename__ = "incident_outcomes"
    id: Mapped[uuid.UUID] = uuid_pk()
    alert_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pre_incident_alerts.id", ondelete="CASCADE"), index=True)
    outcome: Mapped[str] = mapped_column(String(24))  # prevented | occurred | false_positive
    time_to_resolve: Mapped[int | None] = mapped_column(Integer, nullable=True)  # seconds
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # additions: "whether the alert was accurate, what action was taken, how long resolution took"
    alert_accurate: Mapped[bool] = mapped_column(Boolean, default=True)
    action_taken: Mapped[str | None] = mapped_column(Text, nullable=True)
    logged_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    learned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = ts(default=utcnow)


class FailureSignature(Base):
    """Leading-indicator signatures learned from past incidents (embedding lives in the vector store)."""

    __tablename__ = "failure_signatures"
    __table_args__ = (Index("ix_sig_service", "service_id", "is_negative"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monitored_services.id", ondelete="CASCADE"))
    service_name: Mapped[str] = mapped_column(String(200))
    incident_start: Mapped[datetime] = ts()
    incident_end: Mapped[datetime | None] = ts(nullable=True)
    label: Mapped[str] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(16), default="history")  # history | outcome
    is_negative: Mapped[bool] = mapped_column(Boolean, default=False)  # false-positive counter-example
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    features: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    resolved_actions: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    peak_error_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    match_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = ts(default=utcnow)


class ForecastWeights(Base):
    """Per-service adaptive weights (base 30/40/30, refined by the feedback loop)."""

    __tablename__ = "forecast_weights"
    service_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("monitored_services.id", ondelete="CASCADE"), primary_key=True
    )
    w_velocity: Mapped[float] = mapped_column(Float, default=0.30)
    w_similarity: Mapped[float] = mapped_column(Float, default=0.40)
    w_baseline: Mapped[float] = mapped_column(Float, default=0.30)
    signature_threshold: Mapped[float] = mapped_column(Float, default=0.80)
    samples: Mapped[int] = mapped_column(Integer, default=0)
    true_positives: Mapped[int] = mapped_column(Integer, default=0)
    false_positives: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = ts(default=utcnow, onupdate=utcnow)


class ForecastCycleMetric(Base):
    """Cycle completion tracking for the ">99% within 60s" technical metric."""

    __tablename__ = "forecast_cycle_metrics"
    id: Mapped[uuid.UUID] = uuid_pk()
    service_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    started_at: Mapped[datetime] = ts(default=utcnow)
    duration_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    completed: Mapped[bool] = mapped_column(Boolean, default=True)
    within_budget: Mapped[bool] = mapped_column(Boolean, default=True)
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
