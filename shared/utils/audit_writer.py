"""Hash-chained audit_events writer, shared by the audit-service consumer and the direct fallback."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import text

from shared.models import AuditEvent
from shared.utils.db import session_scope

_CHAIN_LOCK = 727_272_002


def _uuid(v):
    return uuid.UUID(v) if v else None


def compute_hash(prev_hash: str | None, e: AuditEvent) -> str:
    body = json.dumps(
        {
            "ts": e.ts.astimezone(UTC).isoformat() if isinstance(e.ts, datetime) else str(e.ts),
            "org": str(e.org_id) if e.org_id else None,
            "actor": str(e.actor_id) if e.actor_id else None,
            "actor_type": e.actor_type,
            "action": e.action,
            "resource_type": e.resource_type,
            "resource_id": e.resource_id,
            "project": str(e.project_id) if e.project_id else None,
            "details": e.details,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(f"{prev_hash or ''}|{body}".encode()).hexdigest()


def write_audit_event(entry: dict) -> AuditEvent:
    with session_scope() as s:
        # single-writer chain: serialise appends across processes
        s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _CHAIN_LOCK})
        prev = s.execute(text("SELECT hash FROM audit_events ORDER BY id DESC LIMIT 1")).scalar()
        ev = AuditEvent(
            org_id=_uuid(entry.get("org_id")),
            actor_id=_uuid(entry.get("actor_id")),
            actor_type=entry.get("actor_type", "user"),
            actor_label=entry.get("actor_label"),
            action=entry["action"],
            resource_type=entry.get("resource_type"),
            resource_id=entry.get("resource_id"),
            project_id=_uuid(entry.get("project_id")),
            ip=entry.get("ip"),
            details=entry.get("details") or {},
        )
        from shared.models.base import utcnow

        ev.ts = utcnow()
        ev.prev_hash = prev
        ev.hash = compute_hash(prev, ev)
        s.add(ev)
        s.flush()
        return ev
