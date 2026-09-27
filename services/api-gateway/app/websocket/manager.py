"""WebSocket hub: fans agent events (Redis pub/sub) out to connected consoles.

Delivers only events the client may see (same org, and a project the user can access). Alerts and
pre-mortems carry `interrupt: true` so the console can interrupt the user rather than silently log."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field

import redis.asyncio as aioredis
from fastapi import WebSocket

from shared.config import settings
from shared.utils.events import CHANNEL

log = logging.getLogger("logpilot.ws")


@dataclass(eq=False)  # identity hashing: connections live in a set
class Conn:
    ws: WebSocket
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: str
    cross_project: bool
    projects: set[str] = field(default_factory=set)
    project_filter: str | None = None  # optional: only events for the active project
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, payload: dict) -> None:
        async with self.lock:  # one writer at a time per socket
            await self.ws.send_json(payload)


class Hub:
    def __init__(self) -> None:
        self.conns: set[Conn] = set()
        self._task: asyncio.Task | None = None
        self._redis: aioredis.Redis | None = None

    async def start(self) -> None:
        self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        self._task = asyncio.create_task(self._listen(), name="event-bus-listener")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._redis:
            await self._redis.aclose()

    def add(self, c: Conn) -> None:
        self.conns.add(c)

    def remove(self, c: Conn) -> None:
        self.conns.discard(c)

    def _allowed(self, c: Conn, ev: dict) -> bool:
        org = ev.get("org_id")
        pid = ev.get("project_id")
        if org and org != str(c.org_id):
            return False
        if pid:
            if not c.cross_project and pid not in c.projects:
                return False
            if c.project_filter and pid != c.project_filter:
                return False
        return True

    async def _listen(self) -> None:
        while True:
            try:
                assert self._redis is not None
                ps = self._redis.pubsub()
                await ps.subscribe(CHANNEL)
                async for msg in ps.listen():
                    if msg.get("type") != "message":
                        continue
                    try:
                        ev = json.loads(msg["data"])
                    except json.JSONDecodeError:
                        continue
                    dead = []
                    for c in list(self.conns):
                        if self._allowed(c, ev):
                            try:
                                await c.send(ev)
                            except Exception:
                                dead.append(c)
                    for c in dead:
                        self.remove(c)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("event bus listener error (%s); reconnecting", exc)
                await asyncio.sleep(2)


hub = Hub()
