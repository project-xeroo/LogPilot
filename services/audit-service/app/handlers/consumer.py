"""Audit stream consumer: reads `logpilot:audit` (Redis Streams, consumer group) and appends to the
hash-chained `audit_events` table. At-least-once with ack-after-write; pending entries from a crashed
consumer are reclaimed on start."""
from __future__ import annotations

import json
import logging
import socket
import threading

from shared.utils.audit_writer import write_audit_event
from shared.utils.events import AUDIT_STREAM, get_redis

log = logging.getLogger("logpilot.audit.consumer")
GROUP = "audit-service"
CONSUMER = f"{socket.gethostname()}-{threading.get_native_id()}"


class AuditConsumer(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True, name="audit-consumer")
        self._halt = threading.Event()
        self.processed = 0

    def stop(self) -> None:
        self._halt.set()

    def _ensure_group(self) -> None:
        try:
            get_redis().xgroup_create(AUDIT_STREAM, GROUP, id="0", mkstream=True)
        except Exception as exc:  # BUSYGROUP = already exists
            if "BUSYGROUP" not in str(exc):
                raise

    def _handle(self, msg_id: str, fields: dict) -> None:
        try:
            write_audit_event(json.loads(fields["data"]))
            self.processed += 1
        except Exception:
            log.exception("failed to write audit entry %s; leaving pending for retry", msg_id)
            return
        get_redis().xack(AUDIT_STREAM, GROUP, msg_id)

    def run(self) -> None:  # pragma: no cover - exercised by integration tests
        while not self._halt.is_set():
            try:
                self._ensure_group()
                r = get_redis()
                # reclaim entries another (dead) consumer left pending
                try:
                    _, claimed, _ = r.xautoclaim(AUDIT_STREAM, GROUP, CONSUMER, min_idle_time=30_000, start_id="0-0", count=100)
                    for mid, fields in claimed:
                        self._handle(mid, fields)
                except Exception:
                    pass
                while not self._halt.is_set():
                    resp = r.xreadgroup(GROUP, CONSUMER, {AUDIT_STREAM: ">"}, count=100, block=2000)
                    for _, entries in resp or []:
                        for mid, fields in entries:
                            self._handle(mid, fields)
            except Exception as exc:
                log.warning("audit consumer error (%s); retrying in 3s", exc)
                self._halt.wait(3)
