"""Engine/session management and idempotent schema bootstrap (PRD tool 04: storage & indexing)."""
from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from shared.config import settings
from shared.models import Base

log = logging.getLogger("logpilot.db")

engine = create_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)

_SCHEMA_LOCK = 727_272_001


@contextmanager
def session_scope() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def init_db() -> None:
    """Create extensions/tables, convert log_records into a time-partitioned hypertable, and add
    the full-text index. Safe to call from every service at startup (advisory-locked, idempotent)."""
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _SCHEMA_LOCK})
        try:
            conn.commit()
            timescale = False
            try:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
                conn.commit()
                timescale = True
            except Exception as exc:  # plain Postgres (e.g. CI) -> regular table
                conn.rollback()
                log.warning("timescaledb extension unavailable, using plain tables: %s", exc)
            Base.metadata.create_all(conn)
            conn.commit()
            # No migration framework here (create_all only adds missing TABLES, never columns on ones that
            # already exist) - new columns on long-lived tables self-heal here instead, the same idempotent
            # way the extensions/indexes below do.
            try:
                conn.execute(text("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'done'"))
                conn.execute(text("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS steps JSONB"))
                conn.commit()
            except Exception as exc:
                conn.rollback()
                log.warning("could not ensure chat_messages columns: %s", exc)
            if timescale:
                try:
                    conn.execute(
                        text(
                            "SELECT create_hypertable('log_records', 'timestamp', "
                            "chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE, migrate_data => TRUE)"
                        )
                    )
                    conn.commit()
                except Exception as exc:
                    conn.rollback()
                    log.warning("could not create hypertable: %s", exc)
            # trigram index accelerates exact-substring (ILIKE) and regex keyword search
            try:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_log_records_message_trgm ON log_records USING gin (message gin_trgm_ops)"))
                conn.commit()
            except Exception as exc:
                conn.rollback()
                log.warning("pg_trgm index unavailable (keyword regex search will be slower): %s", exc)
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _SCHEMA_LOCK})
            conn.commit()


def db_healthy() -> bool:
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
