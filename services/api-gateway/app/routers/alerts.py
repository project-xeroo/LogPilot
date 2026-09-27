"""Alerts & Approvals Queue: pre-incident alerts and propose-only recommended actions awaiting a human
decision (PRD 8.2 / 9.2) - one-tap approve, edit or dismiss, with reasoning required on dismiss - plus the
outcome feedback that lets the agent learn (PRD 4.3)."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import Principal, project_perm
from app.util import audit, iso, upstream
from shared.config.roles import Role
from shared.models import IncidentOutcome, PreIncidentAlert, Project, RecommendedAction
from shared.models.base import utcnow
from shared.utils import clients
from shared.utils.audit import record_agent_action
from shared.utils.clients import ServiceError
from shared.utils.db import get_db
from shared.utils.events import add_feed_item, publish_event

router = APIRouter(prefix="/projects/{project_id}", tags=["alerts"])

JUNIOR_MSG = ("Approvals are withheld for Junior Engineer accounts until an Admin or SRE promotes the account. "
              "You can still view and discuss every recommended action; ask a Developer, SRE or Admin to decide.")
GUIDED_HIGH_RISK_MSG = ("This is a high-risk action. Guided-mode accounts can view and discuss it but cannot approve or dismiss it - "
                        "it has been routed to a Developer, SRE or Admin so you are not the last line of defence on a production call.")


def _guard_decide(p: Principal, *, high_risk: bool = False) -> None:
    if p.role == Role.JUNIOR.value:
        raise HTTPException(403, JUNIOR_MSG)
    if not p.can("actions.decide"):
        raise HTTPException(403, f"your role ({p.role}) cannot approve or dismiss actions")
    if p.guided_mode and high_risk:
        raise HTTPException(403, GUIDED_HIGH_RISK_MSG)


def _action(a: RecommendedAction) -> dict:
    return {"id": str(a.id), "alert_id": str(a.alert_id), "service": a.service_name, "rank": a.rank, "text": a.edited_text or a.text, "original_text": a.text,
            "rationale": a.rationale, "source": a.source, "risk_level": a.risk_level, "autonomy_tier": a.autonomy_tier, "status": a.status,
            "decided_by": str(a.decided_by) if a.decided_by else None, "decided_at": iso(a.decided_at), "dismiss_reason": a.dismiss_reason}


def _alert(a: PreIncidentAlert, db: Session | None = None, detail: bool = False) -> dict:
    d = {"id": str(a.id), "service_id": str(a.service_id), "service": a.service_name, "level": a.level, "status": a.status, "risk_score": round(a.risk_score, 1),
         "peak_risk_score": round(a.peak_risk_score or a.risk_score, 1), "failure_probability": a.failure_probability, "text": a.alert_text,
         "eta_minutes_low": a.eta_minutes_low, "eta_minutes_high": a.eta_minutes_high, "degraded": a.degraded, "created_at": iso(a.created_at),
         "updated_at": iso(a.updated_at), "resolved_at": iso(a.resolved_at), "pre_mortem_report_id": str(a.pre_mortem_report_id) if a.pre_mortem_report_id else None,
         "pattern_matches": a.pattern_matches}
    if detail and db is not None:
        d["evidence_chain"] = a.evidence_chain
        d["actions"] = [_action(x) for x in db.execute(select(RecommendedAction).where(RecommendedAction.alert_id == a.id).order_by(RecommendedAction.rank)).scalars()]
        d["outcomes"] = [{"id": str(o.id), "outcome": o.outcome, "alert_accurate": o.alert_accurate, "action_taken": o.action_taken, "time_to_resolve": o.time_to_resolve,
                          "notes": o.notes, "created_at": iso(o.created_at), "learned": o.learned}
                         for o in db.execute(select(IncidentOutcome).where(IncidentOutcome.alert_id == a.id)).scalars()]
    return d


def _own_alert(db: Session, project_id: uuid.UUID, alert_id: uuid.UUID) -> PreIncidentAlert:
    a = db.get(PreIncidentAlert, alert_id)
    if not a or a.project_id != project_id:
        raise HTTPException(404, "alert not found")
    return a


@router.get("/alerts")
def list_alerts(project_id: uuid.UUID, status: str = "open", limit: int = 50, p: Principal = Depends(project_perm("alerts.view")), db: Session = Depends(get_db)):
    q = select(PreIncidentAlert).where(PreIncidentAlert.project_id == project_id)
    if status == "open":
        q = q.where(PreIncidentAlert.resolved_at.is_(None))
    elif status == "resolved":
        q = q.where(PreIncidentAlert.resolved_at.is_not(None))
    elif status != "all":
        q = q.where(PreIncidentAlert.status == status)
    return [_alert(a, db) for a in db.execute(q.order_by(PreIncidentAlert.created_at.desc()).limit(min(limit, 200))).scalars()]


@router.get("/alerts/{alert_id}")
def alert_detail(project_id: uuid.UUID, alert_id: uuid.UUID, p: Principal = Depends(project_perm("alerts.view")), db: Session = Depends(get_db)):
    return _alert(_own_alert(db, project_id, alert_id), db, detail=True)


@router.get("/approvals")
def approvals_queue(project_id: uuid.UUID, p: Principal = Depends(project_perm("alerts.view")), db: Session = Depends(get_db)):
    """Everything awaiting a human decision: alerts routed to review by policy + propose-only actions."""
    pending = db.execute(select(PreIncidentAlert).where(PreIncidentAlert.project_id == project_id, PreIncidentAlert.status == "pending_review",
                                                        PreIncidentAlert.resolved_at.is_(None))).scalars().all()
    rows = db.execute(
        select(RecommendedAction, PreIncidentAlert).join(PreIncidentAlert, PreIncidentAlert.id == RecommendedAction.alert_id)
        .where(RecommendedAction.project_id == project_id, RecommendedAction.status == "proposed", PreIncidentAlert.resolved_at.is_(None))
        .order_by(PreIncidentAlert.risk_score.desc(), RecommendedAction.rank)
    ).all()
    can = p.can("actions.decide") and p.role != Role.JUNIOR.value
    return {"pending_alerts": [_alert(a) for a in pending],
            "actions": [{**_action(a), "alert": {"id": str(al.id), "level": al.level, "risk_score": round(al.risk_score, 1), "text": al.alert_text}} for a, al in rows],
            "viewer_can_decide": can, "viewer_guided": p.guided_mode,
            "note": None if can else (JUNIOR_MSG if p.role == Role.JUNIOR.value else "Your role can view but not decide on these actions.")}


class ReasonBody(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


class EditBody(BaseModel):
    text: str = Field(min_length=3, max_length=2000)
    approve: bool = True


def _own_action(db: Session, project_id: uuid.UUID, action_id: uuid.UUID) -> RecommendedAction:
    a = db.get(RecommendedAction, action_id)
    if not a or a.project_id != project_id:
        raise HTTPException(404, "action not found")
    if a.status not in ("proposed", "edited"):
        raise HTTPException(409, f"action was already {a.status}")
    return a


def _log_decision(db: Session, p: Principal, a: RecommendedAction, decision: str, extra: dict | None = None) -> None:
    org = db.get(Project, a.project_id).org_id
    record_agent_action(db, tool_name="recommended_actions", trigger="user_request", autonomy_level="propose_only", org_id=org, project_id=a.project_id,
                        actor_id=p.id, approved_by=p.id if decision != "dismissed" else None, status="approved" if decision != "dismissed" else "rejected",
                        output_ref=f"recommended_action:{a.id}", reversible=True, high_impact=a.risk_level == "high",
                        summary=f"{p.name} {decision} propose-only action for {a.service_name}: {(a.edited_text or a.text)[:140]}", details={"decision": decision, **(extra or {})})
    publish_event("action.decided", {"action_id": str(a.id), "alert_id": str(a.alert_id), "status": a.status}, project_id=a.project_id, org_id=org)


@router.post("/actions/{action_id}/approve")
def approve_action(project_id: uuid.UUID, action_id: uuid.UUID, request: Request, p: Principal = Depends(project_perm("alerts.view")), db: Session = Depends(get_db)):
    a = _own_action(db, project_id, action_id)
    _guard_decide(p, high_risk=a.risk_level == "high")
    a.status, a.decided_by, a.decided_at = "approved", p.id, utcnow()
    _log_decision(db, p, a, "approved")
    audit(request, p, "action.approve", resource_type="recommended_action", resource_id=a.id, project_id=project_id, details={"text": (a.edited_text or a.text)[:200]})
    return {**_action(a), "note": "Approved and recorded. LogPilot proposes actions but never executes remediations itself in this release - your team carries this out."}


@router.post("/actions/{action_id}/edit")
def edit_action(project_id: uuid.UUID, action_id: uuid.UUID, body: EditBody, request: Request, p: Principal = Depends(project_perm("alerts.view")), db: Session = Depends(get_db)):
    a = _own_action(db, project_id, action_id)
    _guard_decide(p, high_risk=a.risk_level == "high")
    a.edited_text = body.text.strip()
    a.status = "approved" if body.approve else "edited"
    a.decided_by, a.decided_at = p.id, utcnow()
    _log_decision(db, p, a, "edited and approved" if body.approve else "edited", {"edited_text": a.edited_text})
    audit(request, p, "action.edit", resource_type="recommended_action", resource_id=a.id, project_id=project_id, details={"from": a.text[:200], "to": a.edited_text[:200]})
    return _action(a)


@router.post("/actions/{action_id}/dismiss")
def dismiss_action(project_id: uuid.UUID, action_id: uuid.UUID, body: ReasonBody, request: Request, p: Principal = Depends(project_perm("alerts.view")), db: Session = Depends(get_db)):
    """Dismiss requires a reason: it is recorded and becomes learning signal."""
    a = _own_action(db, project_id, action_id)
    _guard_decide(p, high_risk=a.risk_level == "high")
    a.status, a.dismiss_reason, a.decided_by, a.decided_at = "dismissed", body.reason.strip(), p.id, utcnow()
    _log_decision(db, p, a, "dismissed", {"reason": a.dismiss_reason})
    audit(request, p, "action.dismiss", resource_type="recommended_action", resource_id=a.id, project_id=project_id, details={"reason": a.dismiss_reason[:300]})
    return _action(a)


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge(project_id: uuid.UUID, alert_id: uuid.UUID, request: Request, p: Principal = Depends(project_perm("alerts.outcome")), db: Session = Depends(get_db)):
    a = _own_alert(db, project_id, alert_id)
    if a.resolved_at:
        raise HTTPException(409, "alert is already resolved")
    if a.status == "open":
        a.status = "acknowledged"
    audit(request, p, "alert.acknowledge", resource_type="alert", resource_id=a.id, project_id=project_id)
    publish_event("alert.updated", {"alert_id": str(a.id), "status": a.status}, project_id=project_id, org_id=p.org_id)
    return _alert(a)


@router.post("/alerts/{alert_id}/dismiss")
def dismiss_alert(project_id: uuid.UUID, alert_id: uuid.UUID, body: ReasonBody, request: Request, p: Principal = Depends(project_perm("alerts.view")), db: Session = Depends(get_db)):
    a = _own_alert(db, project_id, alert_id)
    _guard_decide(p, high_risk=a.level == "critical")
    if a.resolved_at:
        raise HTTPException(409, "alert is already resolved")
    a.status, a.resolved_at = "dismissed", utcnow()
    for x in db.execute(select(RecommendedAction).where(RecommendedAction.alert_id == a.id, RecommendedAction.status == "proposed")).scalars():
        x.status, x.dismiss_reason, x.decided_by, x.decided_at = "dismissed", f"alert dismissed: {body.reason}", p.id, utcnow()
    org = db.get(Project, project_id).org_id
    record_agent_action(db, tool_name="pre_incident_alerts", trigger="user_request", autonomy_level="policy_bounded", org_id=org, project_id=project_id, actor_id=p.id,
                        status="rejected", output_ref=f"alert:{a.id}", summary=f"{p.name} dismissed the {a.level} alert for {a.service_name}", details={"reason": body.reason})
    audit(request, p, "alert.dismiss", resource_type="alert", resource_id=a.id, project_id=project_id, details={"reason": body.reason[:300]})
    publish_event("alert.updated", {"alert_id": str(a.id), "status": a.status}, project_id=project_id, org_id=org)
    return _alert(a)


@router.post("/alerts/{alert_id}/approve")
def approve_alert(project_id: uuid.UUID, alert_id: uuid.UUID, request: Request, p: Principal = Depends(project_perm("alerts.view")), db: Session = Depends(get_db)):
    """An alert that policy routed to human review (pending_review) is released: it becomes a live alert and is notified externally."""
    a = _own_alert(db, project_id, alert_id)
    _guard_decide(p, high_risk=a.level == "critical")
    if a.status != "pending_review":
        raise HTTPException(409, "alert is not awaiting review")
    a.status = "open"
    org = db.get(Project, project_id).org_id
    record_agent_action(db, tool_name="pre_incident_alerts", trigger="user_request", autonomy_level="policy_bounded", org_id=org, project_id=project_id, actor_id=p.id,
                        approved_by=p.id, status="approved", output_ref=f"alert:{a.id}", reversible=True, summary=f"{p.name} approved the held {a.level} alert for {a.service_name}")
    try:
        clients.notification.post("/notify", json={"type": "alert.created", "org_id": str(org), "project_id": str(project_id), "external": True, "dedupe_key": f"{a.id}:approved",
                                                   "alert": {"id": str(a.id), "service": a.service_name, "level": a.level, "risk_score": a.risk_score, "text": a.alert_text,
                                                             "eta_minutes_low": a.eta_minutes_low, "eta_minutes_high": a.eta_minutes_high}})
    except ServiceError:
        pass
    audit(request, p, "alert.approve", resource_type="alert", resource_id=a.id, project_id=project_id)
    return _alert(a)


class OutcomeBody(BaseModel):
    outcome: Literal["prevented", "occurred", "false_positive"]
    action_taken: str | None = Field(default=None, max_length=2000)
    time_to_resolve_minutes: float | None = Field(default=None, ge=0, le=100000)
    notes: str | None = Field(default=None, max_length=4000)


@router.post("/alerts/{alert_id}/outcome", status_code=201)
def log_outcome(project_id: uuid.UUID, alert_id: uuid.UUID, body: OutcomeBody, request: Request, p: Principal = Depends(project_perm("alerts.outcome")), db: Session = Depends(get_db)):
    """After every incident - prevented or not - engineers log whether the alert was accurate, what was done and how long
    resolution took. The forecasting subsystem learns from this (weights + leading-indicator signatures)."""
    a = _own_alert(db, project_id, alert_id)
    o = IncidentOutcome(alert_id=a.id, outcome=body.outcome, alert_accurate=body.outcome != "false_positive", action_taken=body.action_taken,
                        time_to_resolve=int(body.time_to_resolve_minutes * 60) if body.time_to_resolve_minutes is not None else None,
                        notes=body.notes, logged_by=p.id)
    db.add(o)
    if not a.resolved_at:
        a.resolved_at, a.status = utcnow(), "resolved"
    db.flush()
    org = db.get(Project, project_id).org_id
    add_feed_item(db, project_id=project_id, org_id=org, kind="alert", severity="info", service=a.service_name, ref_type="alert", ref_id=a.id,
                  title=f"Outcome logged for {a.service_name}: {body.outcome.replace('_', ' ')}", body=(body.action_taken or "") or None)
    audit(request, p, "alert.outcome", resource_type="alert", resource_id=a.id, project_id=project_id, details={"outcome": body.outcome})
    db.commit()  # the learner reads this row from another process
    try:
        clients.forecasting.post(f"/learn/outcome/{o.id}", timeout=10)
    except ServiceError:
        pass  # learning is retried later; the label itself is safely stored
    return {"id": str(o.id), "outcome": o.outcome, "alert_accurate": o.alert_accurate, "learning": "queued"}


@router.post("/alerts/{alert_id}/pre-mortem")
def pre_mortem(project_id: uuid.UUID, alert_id: uuid.UUID, request: Request, p: Principal = Depends(project_perm("reports.generate")), db: Session = Depends(get_db)):
    a = _own_alert(db, project_id, alert_id)
    if a.pre_mortem_report_id:
        return {"report_id": str(a.pre_mortem_report_id), "existing": True}
    db.commit()
    try:
        res = clients.ai.post("/reports/pre-mortem", json={"alert_id": str(a.id), "trigger": "user_request"}, actor=p.actor_headers(), timeout=60)
    except ServiceError as exc:
        raise upstream(exc) from exc
    audit(request, p, "report.generate", resource_type="report", resource_id=res.get("report_id"), project_id=project_id, details={"kind": "pre_mortem"})
    return {"report_id": res.get("report_id"), "existing": False}
