"""Notification routing policy: which channels get an event, de-duplication and rate limiting.

* in_app always fires (proactive alerts interrupt the console)
* webhooks fire only for autonomous, external-eligible events at or above the endpoint's minimum level
* identical events (same dedupe_key) are suppressed for DEDUPE_TTL; each service is capped per hour
"""
from __future__ import annotations

from shared.models import WebhookEndpoint
from shared.utils.events import get_redis

DEDUPE_TTL = 600
MAX_PER_SERVICE_PER_HOUR = 30
LEVEL_RANK = {"warning": 1, "critical": 2}


def is_duplicate(dedupe_key: str | None) -> bool:
    if not dedupe_key:
        return False
    try:
        # SET NX: first caller wins, later identical events inside the TTL are duplicates
        return not get_redis().set(f"notify:dedupe:{dedupe_key}", "1", nx=True, ex=DEDUPE_TTL)
    except Exception:  # bus down: better a duplicate than a lost alert
        return False


def rate_limited(project_id: str, service: str) -> bool:
    try:
        r = get_redis()
        key = f"notify:rate:{project_id}:{service}"
        n = r.incr(key)
        if n == 1:
            r.expire(key, 3600)
        return n > MAX_PER_SERVICE_PER_HOUR
    except Exception:
        return False


def endpoints_for(event_type: str, level: str, endpoints: list[WebhookEndpoint]) -> list[WebhookEndpoint]:
    out = []
    for e in endpoints:
        if not e.enabled:
            continue
        evs = e.events or []
        if evs and event_type not in evs and "*" not in evs:
            continue
        if LEVEL_RANK.get(level, 1) < LEVEL_RANK.get(e.min_level or "warning", 1):
            continue
        out.append(e)
    return out
