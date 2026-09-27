"""Tenancy, identity and access."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared.models.base import Base, ts, utcnow, uuid_pk


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = ts(default=utcnow)


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(32), default="developer")
    guided_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = ts(default=utcnow)
    last_login_at: Mapped[datetime | None] = ts(nullable=True)
    promoted_at: Mapped[datetime | None] = ts(nullable=True)
    promoted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_project_name"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    environment: Mapped[str] = mapped_column(String(32), default="production")
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = ts(default=utcnow)


class UserProject(Base):
    __tablename__ = "user_projects"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)


class MonitoredService(Base):
    """A service the agent watches. `id` is the `service_id` used across forecasting tables."""

    __tablename__ = "monitored_services"
    __table_args__ = (UniqueConstraint("project_id", "name", "environment", name="uq_service"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    environment: Mapped[str] = mapped_column(String(32), default="production")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # configurable cadence per service (default 60s)
    forecast_interval_seconds: Mapped[int] = mapped_column(Integer, default=60)
    warning_threshold: Mapped[int | None] = mapped_column(Integer, nullable=True)
    critical_threshold: Mapped[int | None] = mapped_column(Integer, nullable=True)
    playbook: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # optional service-specific playbook
    last_forecast_at: Mapped[datetime | None] = ts(nullable=True)
    next_forecast_at: Mapped[datetime | None] = ts(nullable=True, index=True)
    created_at: Mapped[datetime] = ts(default=utcnow)


class OrgSetting(Base):
    """Org-level key/value settings: forecast thresholds, model/provider config (non-secret)."""

    __tablename__ = "org_settings"
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = ts(default=utcnow, onupdate=utcnow)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class GlossaryTerm(Base):
    __tablename__ = "glossary_terms"
    id: Mapped[uuid.UUID] = uuid_pk()
    term: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    definition: Mapped[str] = mapped_column(String(1000))
    category: Mapped[str] = mapped_column(String(60), default="reliability")
    seeded_from: Mapped[str] = mapped_column(String(60), default="agent")  # agent | seed | user
