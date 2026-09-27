"""Audit helpers.

* `record_agent_action` writes the `agent_actions` audit table (PRD 8.3): tool, trigger,
  confidence, approver - for every autonomous or human-approved action.
* `emit_audit` pushes a user/system action onto the audit stream consumed by the audit-service
  (hash-chained `audit_events`). If the stream is unreachable it falls back to a direct DB write,
  so an action is never silently unaudited.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from shared.models import AgentAction
from shared.utils.events import AUDIT_STREAM, get_redis

log = logging.getLogger("logpilot.audit")


def record_agent_action(
    session,
    *,
    tool_name: str,
    trigger: str,
    autonomy_level: str,
    org_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    approved_by: uuid.UUID | None = None,
    confidence: float | None = None,
    status: str = "executed",
    input_ref: str | None = None,
    output_ref: str | None = None,
    summary: str | None = None,
    details: dict | None = None,
    high_impact: bool = False,
    reversible: bool = False,
    downgraded_from: str | None = None,
) -> AgentAction:
    action = AgentAction(
        tool_name=tool_name,
        trigger=trigger,
        autonomy_level=autonomy_level,
        org_id=org_id,
        project_id=project_id,
        actor_id=actor_id,
        approved_by=approved_by,
        confidence=confidence,
        status=status,
        input_ref=(input_ref or None) and input_ref[:500],
        output_ref=(output_ref or None) and output_ref[:500],
        summary=summary,
        details=details,
        high_impact=high_impact,
        reversible=reversible,
        downgraded_from=downgraded_from,
    )
    session.add(action)
    session.flush()
    return action


def emit_audit(
    action: str,
    *,
    org_id: uuid.UUID | str | None = None,
    actor_id: uuid.UUID | str | None = None,
    actor_type: str = "user",
    actor_label: str | None = None,
    resource_type: str | None = None,
    resource_id: str | uuid.UUID | None = None,
    project_id: uuid.UUID | str | None = None,
    ip: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    entry = {
        "action": action,
        "org_id": str(org_id) if org_id else None,
        "actor_id": str(actor_id) if actor_id else None,
        "actor_type": actor_type,
        "actor_label": actor_label,
        "resource_type": resource_type,
        "resource_id": str(resource_id) if resource_id else None,
        "project_id": str(project_id) if project_id else None,
        "ip": ip,
        "details": details or {},
    }
    try:
        get_redis().xadd(AUDIT_STREAM, {"data": json.dumps(entry, default=str)}, maxlen=100_000, approximate=True)
    except Exception as exc:
        log.warning("audit stream unavailable (%s); writing directly", exc)
        from shared.utils.audit_writer import write_audit_event

        write_audit_event(entry)
