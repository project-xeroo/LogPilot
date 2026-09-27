"""Shared helpers for pipeline tasks: stage tracking, progress events, agent-action audit."""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import contextmanager

from shared.models import LogSession
from shared.models.base import utcnow
from shared.utils.audit import record_agent_action
from shared.utils.db import session_scope
from shared.utils.events import publish_event

log = logging.getLogger("logpilot.pipeline")

# progress ranges (0-20 upload+redaction, 20-55 parsing, 55-100 enrichment)
STAGES = {
    "embedding": 62.0,
    "deduplication": 72.0,
    "clustering": 82.0,
    "anomaly_detection": 90.0,
    "baselines": 96.0,
    "finalizing": 99.0,
}


def set_stage(session_id: str, stage: str, status: str | None = None) -> None:
    sid = uuid.UUID(session_id)
    with session_scope() as db:
        s = db.get(LogSession, sid)
        if not s:
            return
        s.stage = stage
        s.progress_pct = max(s.progress_pct or 0, STAGES.get(stage, s.progress_pct or 0))
        if status:
            s.status = status
        project_id, pct, st = s.project_id, s.progress_pct, s.status
    publish_event(
        "ingestion.progress",
        {"session_id": session_id, "stage": stage, "progress_pct": pct, "status": st},
        project_id=project_id,
    )


@contextmanager
def timed_stage(session_id: str, stage: str):
    set_stage(session_id, stage)
    t0 = time.monotonic()
    yield
    ms = int((time.monotonic() - t0) * 1000)
    with session_scope() as db:
        s = db.get(LogSession, uuid.UUID(session_id))
        if s:
            log_ = dict(s.stage_log or {})
            log_[stage] = {"ms": ms, "at": utcnow().isoformat()}
            s.stage_log = log_


def audit_tool(
    session_id: str,
    tool: str,
    summary: str,
    details: dict | None = None,
    *,
    tier: str = "autonomous_background",
    trigger: str = "upload",
) -> None:
    """Every autonomous action lands in agent_actions (PRD 8.3)."""
    with session_scope() as db:
        s = db.get(LogSession, uuid.UUID(session_id))
        record_agent_action(
            db, tool_name=tool, trigger=trigger, autonomy_level=tier,
            project_id=s.project_id if s else None, org_id=_org_of(db, s.project_id) if s else None,
            input_ref=f"log_session:{session_id}", summary=summary, details=details, status="executed",
        )


def _org_of(db, project_id):
    from shared.models import Project

    p = db.get(Project, project_id)
    return p.org_id if p else None
