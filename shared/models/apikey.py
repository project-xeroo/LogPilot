"""Project-scoped API keys: a deliberately narrow machine credential for external services (a team's own
webapp pushing its logs in, or reading back its own status) that should never need a full user login."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared.models.base import Base, ts, utcnow, uuid_pk


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (Index("ix_api_keys_prefix", "key_prefix"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    key_prefix: Mapped[str] = mapped_column(String(16))  # shown in the UI to tell keys apart; not secret
    key_hash: Mapped[str] = mapped_column(String(128))  # sha256 hex digest of the full key - the raw value is never stored
    scopes: Mapped[list] = mapped_column(JSONB, default=list)  # subset of shared.config.roles.API_KEY_SCOPES
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = ts(default=utcnow)
    last_used_at: Mapped[datetime | None] = ts(nullable=True)
    revoked_at: Mapped[datetime | None] = ts(nullable=True)
