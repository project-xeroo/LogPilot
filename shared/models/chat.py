"""Chat threads and messages (thumbs ratings feed the >80% satisfaction metric)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Float, ForeignKey, Index, Integer, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared.models.base import Base, ts, utcnow, uuid_pk


class ChatThread(Base):
    __tablename__ = "chat_threads"
    __table_args__ = (Index("ix_threads_user", "user_id", "created_at"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    title: Mapped[str] = mapped_column(String(300), default="New conversation")
    created_at: Mapped[datetime] = ts(default=utcnow)
    updated_at: Mapped[datetime] = ts(default=utcnow, onupdate=utcnow)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_messages_thread", "thread_id", "created_at"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    thread_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_threads.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(12))  # user | agent
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # source references + redacted snippets
    cards: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # structured tool output (health, risk, results...)
    tool_calls: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_label: Mapped[str | None] = mapped_column(String(12), nullable=True)  # high | medium | low
    rating: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)  # +1 / -1
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    suggested_followups: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = ts(default=utcnow)
    # thinking | done | error - an agent reply row is created immediately (empty content) so a client that
    # reconnects or reloads mid-answer can see the work is still in progress, not lost.
    status: Mapped[str] = mapped_column(String(16), default="done")
    steps: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # human-readable progress log while thinking
