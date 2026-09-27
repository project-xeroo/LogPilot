"""Structured storage writes (tool 04): bulk inserts, template upserts, service/deployment discovery."""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import insert, literal_column, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.parsers import Malformed, ParsedRecord
from shared.models import (
    DeploymentEvent,
    LogRecord,
    LogSession,
    LogTemplate,
    MalformedRecord,
    MonitoredService,
    SEVERITY_NUM,
)
from shared.models.base import utcnow
from shared.utils.logtemplate import template_id, template_of


@dataclass
class BatchStats:
    records: int = 0
    malformed: int = 0
    min_ts: datetime | None = None
    max_ts: datetime | None = None
    new_deployments: list[tuple[str, str, str | None]] = field(default_factory=list)  # (service, version, env)
    services: set[tuple[str, str]] = field(default_factory=set)


class Repository:
    """One instance per ingest job; tracks what has been seen so discovery upserts are cheap."""

    MAX_STORED_MALFORMED = 1000

    def __init__(self, db: Session, session: LogSession):
        self.db = db
        self.session = session
        self.project_id = session.project_id
        self._known_services: set[tuple[str, str]] = set()
        self._known_deployments: set[tuple[str, str, str]] = set()
        self._stored_malformed = 0
        self.stats = BatchStats()

    # -- records ------------------------------------------------------------------------------------
    def insert_records(self, records: list[ParsedRecord]) -> None:
        if not records:
            return
        rows, templates = [], {}
        deploy: dict[tuple[str, str, str], list[datetime]] = defaultdict(list)
        for r in records:
            tmpl, thash = template_of(r.message)
            sev_num = SEVERITY_NUM[r.severity]
            env = r.environment or self.session.environment or "production"
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "timestamp": r.timestamp,
                    "project_id": self.project_id,
                    "service": r.service,
                    "severity": r.severity,
                    "severity_num": sev_num,
                    "message": r.message,
                    "session_id": self.session.id,
                    "request_id": r.request_id,
                    "trace_id": r.trace_id,
                    "environment": env,
                    "deployment_version": r.deployment_version,
                    "line_no": r.line_no,
                    "template_hash": thash,
                    "embedding_id": template_id(self.project_id, thash),  # point id of the template vector (embedded async)
                    "attributes": r.attributes or None,
                }
            )
            t = templates.get(thash)
            if t is None:
                t = templates[thash] = {
                    "template": tmpl, "sample": r.message[:2000], "count": 0, "first": r.timestamp, "last": r.timestamp,
                    "sev": sev_num, "sev_name": r.severity, "services": set(),
                }
            t["count"] += 1
            t["first"], t["last"] = min(t["first"], r.timestamp), max(t["last"], r.timestamp)
            t["services"].add(r.service)
            if sev_num > t["sev"]:
                t["sev"], t["sev_name"] = sev_num, r.severity
            self.stats.services.add((r.service, env))
            if r.deployment_version:
                deploy[(r.service, r.deployment_version, env)].append(r.timestamp)
            ts_ = r.timestamp
            self.stats.min_ts = ts_ if self.stats.min_ts is None else min(self.stats.min_ts, ts_)
            self.stats.max_ts = ts_ if self.stats.max_ts is None else max(self.stats.max_ts, ts_)
        self.db.execute(insert(LogRecord), rows)
        self._upsert_templates(templates)
        self._discover_services()
        self._discover_deployments(deploy)
        self.stats.records += len(rows)

    def _upsert_templates(self, templates: dict) -> None:
        rows = []
        for h, t in templates.items():
            rows.append(
                {
                    "id": template_id(self.project_id, h),
                    "project_id": self.project_id,
                    "template_hash": h,
                    "template": t["template"],
                    "sample_message": t["sample"],
                    "is_error": t["sev"] >= SEVERITY_NUM["ERROR"],
                    "max_severity": t["sev_name"],
                    "first_seen": t["first"],
                    "last_seen": t["last"],
                    "occurrence_count": t["count"],
                    "services": sorted(t["services"]),
                    "is_new_in_last_session": True,
                }
            )
        rows.sort(key=lambda r: r["template_hash"])  # deterministic lock order avoids worker deadlocks
        stmt = pg_insert(LogTemplate).values(rows)
        tbl = LogTemplate.__table__
        stmt = stmt.on_conflict_do_update(
            index_elements=["project_id", "template_hash"],
            set_={
                "occurrence_count": tbl.c.occurrence_count + stmt.excluded.occurrence_count,
                "first_seen": text("LEAST(log_templates.first_seen, excluded.first_seen)"),
                "last_seen": text("GREATEST(log_templates.last_seen, excluded.last_seen)"),
                "is_error": tbl.c.is_error | stmt.excluded.is_error,
                "max_severity": text(
                    "CASE WHEN excluded.is_error AND NOT log_templates.is_error THEN excluded.max_severity "
                    "WHEN excluded.max_severity = 'FATAL' THEN 'FATAL' ELSE log_templates.max_severity END"
                ),
                "services": text(
                    "(SELECT COALESCE(jsonb_agg(DISTINCT s), '[]'::jsonb) FROM jsonb_array_elements("
                    "COALESCE(log_templates.services, '[]'::jsonb) || excluded.services) AS s)"
                ),
            },
        )
        self.db.execute(stmt)

    def _discover_services(self) -> None:
        new = self.stats.services - self._known_services
        if not new:
            return
        now = utcnow()
        stmt = pg_insert(MonitoredService).values(
            [
                {"id": uuid.uuid4(), "project_id": self.project_id, "name": name, "environment": env,
                 "enabled": True, "forecast_interval_seconds": 60, "next_forecast_at": now, "created_at": now}
                for name, env in sorted(new)
            ]
        ).on_conflict_do_nothing(constraint="uq_service")
        self.db.execute(stmt)
        self._known_services |= new

    def _discover_deployments(self, deploy: dict[tuple[str, str, str], list[datetime]]) -> None:
        for key, stamps in deploy.items():
            if key in self._known_deployments:
                continue
            svc, ver, env = key
            stmt = pg_insert(DeploymentEvent).values(
                id=uuid.uuid4(), project_id=self.project_id, service=svc, version=ver, environment=env,
                deployed_at=min(stamps), last_seen_at=max(stamps), source="detected",
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["project_id", "service", "version", "environment"],
                set_={
                    "deployed_at": text("LEAST(deployments.deployed_at, excluded.deployed_at)"),
                    "last_seen_at": text("GREATEST(deployments.last_seen_at, excluded.last_seen_at)"),
                },
            ).returning(literal_column("(xmax = 0)").label("inserted"))
            inserted = self.db.execute(stmt).scalar()
            self._known_deployments.add(key)
            if inserted:
                self.stats.new_deployments.append((svc, ver, env))

    # -- malformed ----------------------------------------------------------------------------------
    def store_malformed(self, items: list[Malformed]) -> None:
        self.stats.malformed += len(items)
        room = self.MAX_STORED_MALFORMED - self._stored_malformed
        if room <= 0 or not items:
            return
        keep = items[:room]
        self.db.execute(
            insert(MalformedRecord),
            [
                {"id": uuid.uuid4(), "session_id": self.session.id, "project_id": self.project_id,
                 "line_no": m.line_no, "raw_redacted": m.raw, "reason": m.reason[:300]}
                for m in keep
            ],
        )
        self._stored_malformed += len(keep)

    # -- session progress ---------------------------------------------------------------------------
    def update_session(self, **values) -> None:
        self.db.execute(update(LogSession).where(LogSession.id == self.session.id).values(**values))
        self.db.commit()
