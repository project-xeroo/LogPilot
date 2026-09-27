"""Agent governance: audit trail of actions, autonomy policy, user-action audit, feed."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Float, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared.models.base import Base, ts, utcnow, uuid_pk


class AgentAction(Base):
    """Audit trail of every autonomous or human-approved agent action (PRD 7.3 agent_actions)."""

    __tablename__ = "agent_actions"
    __table_args__ = (Index("ix_agent_actions_time", "org_id", "created_at"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    tool_name: Mapped[str] = mapped_column(String(64), index=True)
    trigger: Mapped[str] = mapped_column(String(32))  # autonomous | user_request | schedule | deploy_event | upload
    autonomy_level: Mapped[str] = mapped_column(String(32))  # tier under which it ran
    input_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    output_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = ts(default=utcnow)
    # --- additions required by 8.3: tool, trigger, confidence, approver; 2: "logged and reversible" ---
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # executed | proposed (downgraded, in human queue) | approved | rejected | reverted
    status: Mapped[str] = mapped_column(String(16), default="executed")
    high_impact: Mapped[bool] = mapped_column(Boolean, default=False)
    reversible: Mapped[bool] = mapped_column(Boolean, default=False)
    reverted_at: Mapped[datetime | None] = ts(nullable=True)
    reverted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    revert_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    downgraded_from: Mapped[str | None] = mapped_column(String(32), nullable=True)


class AutonomyPolicy(Base):
    """Per-tool, per-organization autonomy configuration (PRD 7.3 autonomy_policy)."""

    __tablename__ = "autonomy_policy"
    __table_args__ = (UniqueConstraint("org_id", "tool_name", "scope", name="uq_policy"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    tool_name: Mapped[str] = mapped_column(String(64))
    scope: Mapped[str] = mapped_column(String(32), default="*")  # environment: * | production | staging | dev
    min_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    # additions
    tier: Mapped[str] = mapped_column(String(32), default="read_only")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = ts(default=utcnow, onupdate=utcnow)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class AuditEvent(Base):
    """Audit log for all user actions (upload, search, export, configuration changes) and
    system-level events. Hash-chained for tamper evidence."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_time", "org_id", "ts"), Index("ix_audit_action", "action"))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = ts(default=utcnow)
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(12), default="user")  # user | agent | system
    actor_label: Mapped[str | None] = mapped_column(String(320), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    resource_type: Mapped[str | None] = mapped_column(String(48), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class FeedItem(Base):
    """Agent Feed: chronological stream of everything the agent has noticed, said or drafted."""

    __tablename__ = "feed_items"
    __table_args__ = (Index("ix_feed_project_time", "project_id", "created_at"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    # alert | report | anomaly | answer | deployment | action | ingestion | cluster | rca
    kind: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(String(500))
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str] = mapped_column(String(12), default="info")  # info | warning | critical
    ref_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ref_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    service: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = ts(default=utcnow)
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
