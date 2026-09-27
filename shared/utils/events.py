"""Real-time event bus (Redis pub/sub) + Agent Feed writer.

Every service publishes here; the API gateway fans events out to connected WebSocket clients
("proactive alerts and pre-mortems interrupt the console, not just log to a page").
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

import redis

from shared.config import settings
from shared.models import FeedItem
from shared.models.base import utcnow

log = logging.getLogger("logpilot.events")
CHANNEL = "logpilot:events"
AUDIT_STREAM = "logpilot:audit"

_redis: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=5)
    return _redis


def _default(o: Any):
    if isinstance(o, (uuid.UUID,)):
        return str(o)
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(f"not serialisable: {type(o)}")


def publish_event(
    event_type: str,
    payload: dict[str, Any],
    *,
    project_id: uuid.UUID | str | None = None,
    org_id: uuid.UUID | str | None = None,
    interrupt: bool = False,
) -> None:
    """Fire-and-forget; never raises (a dead bus must not break the pipeline)."""
    msg = {
        "type": event_type,
        "project_id": str(project_id) if project_id else None,
        "org_id": str(org_id) if org_id else None,
        "interrupt": interrupt,
        "ts": utcnow().isoformat(),
        "payload": payload,
    }
    try:
        get_redis().publish(CHANNEL, json.dumps(msg, default=_default))
    except Exception as exc:  # pragma: no cover
        log.warning("event publish failed: %s", exc)


def add_feed_item(
    session,
    *,
    project_id: uuid.UUID,
    kind: str,
    title: str,
    body: str | None = None,
    severity: str = "info",
    ref_type: str | None = None,
    ref_id: str | uuid.UUID | None = None,
    service: str | None = None,
    org_id: uuid.UUID | None = None,
    meta: dict | None = None,
    interrupt: bool = False,
) -> FeedItem:
    """Persist an Agent Feed entry and push it to connected clients."""
    item = FeedItem(
        project_id=project_id,
        org_id=org_id,
        kind=kind,
        title=title[:500],
        body=body,
        severity=severity,
        ref_type=ref_type,
        ref_id=str(ref_id) if ref_id else None,
        service=service,
        meta=meta,
    )
    session.add(item)
    session.flush()
    publish_event(
        "feed.item",
        {
            "id": str(item.id),
            "kind": kind,
            "title": item.title,
            "body": body,
            "severity": severity,
            "ref_type": ref_type,
            "ref_id": item.ref_id,
            "service": service,
            "created_at": item.created_at.isoformat() if item.created_at else utcnow().isoformat(),
        },
        project_id=project_id,
        org_id=org_id,
        interrupt=interrupt,
    )
    return item
