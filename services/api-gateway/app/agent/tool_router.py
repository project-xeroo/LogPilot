"""The agent's tool router (PRD 7.1: "hosts the agent's perceive-reason-act loop and tool router").

Every tool invocation - from the chat loop or a REST call - passes through `ToolRouter.invoke`:
    1. RBAC          the caller's role must hold the tool's permission
    2. policy        the org's autonomy policy must allow the tool (a disabled tool is refused)
    3. execute       the handler runs (calling AI / forecasting services or reading the DB)
    4. audit         an `agent_actions` row records tool, trigger, autonomy level, confidence, actor
Tools that record their own `agent_actions` rows inside the owning service (RCA, reports) are not logged twice.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import Principal
from shared.config.tools import TOOLS_BY_NAME, Tier
from shared.models import DeploymentComparison, DeploymentEvent, IncidentReport, MonitoredService, PreIncidentAlert, Project
from shared.utils import clients
from shared.utils.analytics import compare_deployments, health_state
from shared.utils.audit import record_agent_action
from shared.utils.autonomy import resolve_policy
from shared.utils.clients import ServiceError, ServiceUnavailable

log = logging.getLogger("logpilot.tools")


@dataclass
class ToolDef:
    name: str
    permission: str
    handler: Callable[[Session, Principal, uuid.UUID, dict[str, Any]], dict[str, Any]]
    self_logged: bool = False  # the owning service writes agent_actions itself


def _svc_id(db: Session, project_id: uuid.UUID, name: str) -> uuid.UUID | None:
    return db.execute(select(MonitoredService.id).where(MonitoredService.project_id == project_id, MonitoredService.name == name)).scalars().first()


# ---- handlers ---------------------------------------------------------------------------------------------------------------
def _search(db, p, pid, a):
    return clients.ai.post("/search", json={"project_id": str(pid), **a}, actor=p.actor_headers(), timeout=30)


def _health(db, p, pid, a):
    return health_state(db, pid, window_minutes=int(a.get("window_minutes", 60)), environment=a.get("environment"), service=a.get("service"))


def _rca(db, p, pid, a):
    db.commit()  # the AI service reads through its own connection
    # Must stay comfortably above analyzer.LLM_TIMEOUT (70s) - including its own internal retry-on-timeout
    # loop (up to ~3x) - otherwise the gateway gives up and reports RCA "unavailable" while ai-service is
    # still genuinely working on it in the background.
    return clients.ai.post("/rca", json={"project_id": str(pid), **{k: v for k, v in a.items() if k in ("service", "cluster_id", "start", "end")}, "trigger": "on_request"},
                           actor=p.actor_headers(), timeout=280)


def _report(db, p, pid, a):
    db.commit()
    kind = a.get("kind", "incident")
    if kind == "executive_summary":
        return clients.ai.post("/reports/executive-summary", json={"project_id": str(pid), "days": a.get("days", 7)}, actor=p.actor_headers(), timeout=100)
    if kind == "pre_mortem":
        alert_id = a.get("alert_id")
        alert = None
        if not alert_id:
            # The caller (an LLM deciding tool calls on its own) may know only a service name, not a specific
            # alert id - resolve the most at-risk open alert for it ourselves rather than requiring a prior
            # lookup step every time.
            q = select(PreIncidentAlert).where(PreIncidentAlert.project_id == pid, PreIncidentAlert.resolved_at.is_(None))
            if a.get("service"):
                q = q.where(PreIncidentAlert.service_name == a["service"])
            alert = db.execute(q.order_by(PreIncidentAlert.risk_score.desc()).limit(1)).scalars().first()
            if alert is None:
                raise HTTPException(404, "There is no open pre-incident alert to write a pre-mortem for.")
            alert_id = str(alert.id)
        if alert is not None and alert.pre_mortem_report_id:
            existing = db.get(IncidentReport, alert.pre_mortem_report_id)
            if existing:
                return {"report_id": str(existing.id), "kind": "pre_mortem", "title": existing.title, "existing": True}
        return clients.ai.post("/reports/pre-mortem", json={"alert_id": alert_id, "trigger": "user_request"}, actor=p.actor_headers(), timeout=60)
    return clients.ai.post("/reports/incident", json={"project_id": str(pid), **{k: v for k, v in a.items() if k in ("service", "cluster_id", "rca_id", "alert_id", "start", "end")}},
                           actor=p.actor_headers(), timeout=120)


def _deploy(db, p, pid, a):
    svc, env = a["service"], a.get("environment")
    frm, to = a.get("from_version"), a.get("to_version")
    if not (frm and to):  # default: the two most recent versions of the service
        rows = db.execute(select(DeploymentEvent).where(DeploymentEvent.project_id == pid, DeploymentEvent.service == svc)
                          .order_by(DeploymentEvent.deployed_at.desc()).limit(2)).scalars().all()
        if len(rows) < 2:
            raise HTTPException(404, f"fewer than two deployed versions of {svc} are known, so there is nothing to compare")
        to, frm = rows[0].version, rows[1].version
        env = env or rows[0].environment
    # Reuse an existing comparison for this exact version pair instead of inserting a duplicate every
    # time someone views it or asks in chat: a completed version's logs are fixed once a newer version
    # deploys, so repeat requests would otherwise pile up identical "regression flagged" rows forever.
    existing = db.execute(
        select(DeploymentComparison).where(DeploymentComparison.project_id == pid, DeploymentComparison.service == svc,
                                           DeploymentComparison.from_version == frm, DeploymentComparison.to_version == to,
                                           DeploymentComparison.environment == env)
        .order_by(DeploymentComparison.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return {**existing.result, "comparison_id": str(existing.id)}
    try:
        res = compare_deployments(db, pid, svc, frm, to, env)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    row = DeploymentComparison(project_id=pid, service=svc, environment=env, from_version=frm, to_version=to, result=res, regression=res["regression"], trigger="on_request")
    db.add(row)
    db.flush()
    return {**res, "comparison_id": str(row.id)}


def _risk(db, p, pid, a):
    if a.get("service"):
        sid = _svc_id(db, pid, a["service"])
        if sid is None:
            raise HTTPException(404, f"I'm not monitoring a service called '{a['service']}'")
        try:
            detail = clients.forecasting.get(f"/risk/{sid}")
        except ServiceError as exc:
            if exc.status == 404:
                return {"services": [], "focus": a["service"], "detail": None, "message": "No forecast has been computed for this service yet."}
            raise
        board = clients.forecasting.get("/risk", params={"project_id": str(pid)})
        return {"services": board["services"], "focus": a["service"], "detail": detail}
    return clients.forecasting.get("/risk", params={"project_id": str(pid)})


TOOL_DEFS: dict[str, ToolDef] = {
    "log_search": ToolDef("log_search", "logs.search", _search),
    "health_state": ToolDef("health_state", "health.view", _health),
    "root_cause_analysis": ToolDef("root_cause_analysis", "rca.run", _rca, self_logged=True),
    "incident_report_generation": ToolDef("incident_report_generation", "reports.generate", _report, self_logged=True),
    "deployment_comparison": ToolDef("deployment_comparison", "deployments.compare", _deploy),
    "proactive_failure_forecasting": ToolDef("proactive_failure_forecasting", "risk.view", _risk),
}


class ToolRouter:
    def invoke(self, db: Session, p: Principal, tool_name: str, project_id: uuid.UUID, args: dict[str, Any] | None = None, *, trigger: str = "user_request") -> dict[str, Any]:
        d = TOOL_DEFS.get(tool_name)
        if d is None:
            raise HTTPException(404, f"unknown tool '{tool_name}'")
        if not p.can(d.permission):
            raise HTTPException(403, f"your role ({p.role}) may not use {TOOLS_BY_NAME[tool_name].title} (needs '{d.permission}')")
        project = db.get(Project, project_id)
        policy = resolve_policy(db, p.org_id, tool_name, project.environment if project else None)
        if not policy["enabled"]:
            raise HTTPException(403, f"{TOOLS_BY_NAME[tool_name].title} has been disabled by your organization's autonomy policy")
        t0 = time.monotonic()
        try:
            result = d.handler(db, p, project_id, args or {})
        except ServiceUnavailable as exc:
            raise HTTPException(503, f"{exc.service} is currently unavailable") from exc
        except ServiceError as exc:
            raise HTTPException(exc.status if 400 <= exc.status < 600 and exc.status != 401 else 502, exc.detail) from exc
        ms = int((time.monotonic() - t0) * 1000)
        action_id = None
        if not d.self_logged:
            a = record_agent_action(
                db, tool_name=tool_name, trigger=trigger, autonomy_level=policy["tier"], org_id=p.org_id, project_id=project_id, actor_id=p.id, status="executed",
                input_ref=", ".join(f"{k}={str(v)[:40]}" for k, v in (args or {}).items())[:480], summary=f"{TOOLS_BY_NAME[tool_name].title} ({ms}ms) for {p.name}",
                details={"latency_ms": ms, "tier": policy["tier"], "tier_source": policy["source"]},
            )
            action_id = str(a.id)
        return {"tool": tool_name, "result": result, "latency_ms": ms, "action_id": action_id, "autonomy_level": policy["tier"]}

    def catalogue(self) -> list[dict]:
        return [{"name": n, "permission": d.permission, "title": TOOLS_BY_NAME[n].title} for n, d in TOOL_DEFS.items()]


router = ToolRouter()
_ = Tier
