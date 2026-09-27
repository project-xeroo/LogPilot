"""Reversal of agent actions - "100% of high-impact actions logged and reversible" (PRD 2.2).

Each handler undoes the *effect* of an autonomous action that carries a typed `output_ref`
("alert:<id>", "report:<id>", "rca:<id>", "recommended_action:<id>", "deployment_comparison:<id>").
Reversal never deletes history: the original action stays in the audit trail, marked reverted."""
from __future__ import annotations

import uuid
from typing import Callable

from sqlalchemy.orm import Session

from shared.models import (
    AgentAction, DeploymentComparison, IncidentReport, PreIncidentAlert, RcaResult, RecommendedAction,
)
from shared.models.base import utcnow


class NotReversible(Exception):
    pass


def _alert(db: Session, ref: str, note: str) -> str:
    a = db.get(PreIncidentAlert, uuid.UUID(ref))
    if not a:
        raise NotReversible("alert no longer exists")
    a.status, a.resolved_at = "dismissed", utcnow()
    for r in db.query(RecommendedAction).filter(RecommendedAction.alert_id == a.id, RecommendedAction.status == "proposed"):
        r.status, r.dismiss_reason = "dismissed", f"alert reverted: {note}"
    return f"alert for {a.service_name} withdrawn and its undecided proposals dismissed"


def _report(db: Session, ref: str, note: str) -> str:
    r = db.get(IncidentReport, uuid.UUID(ref))
    if not r:
        raise NotReversible("report no longer exists")
    r.status = "discarded"
    return f"report '{r.title[:60]}' discarded"


def _rca(db: Session, ref: str, note: str) -> str:
    r = db.get(RcaResult, uuid.UUID(ref))
    if not r:
        raise NotReversible("RCA result no longer exists")
    r.review_status = "rejected"
    return "RCA result rejected"


def _action(db: Session, ref: str, note: str) -> str:
    a = db.get(RecommendedAction, uuid.UUID(ref))
    if not a:
        raise NotReversible("recommended action no longer exists")
    a.status, a.decided_by, a.decided_at = "proposed", None, None
    return "recommended action returned to the approval queue"


def _deploy(db: Session, ref: str, note: str) -> str:
    c = db.get(DeploymentComparison, uuid.UUID(ref))
    if not c:
        raise NotReversible("comparison no longer exists")
    c.regression = False
    c.result = {**c.result, "reverted": True, "revert_note": note}
    return "regression flag withdrawn"


HANDLERS: dict[str, Callable[[Session, str, str], str]] = {
    "alert": _alert, "report": _report, "rca": _rca, "recommended_action": _action, "deployment_comparison": _deploy,
}


def revert_action(db: Session, action_id: uuid.UUID, by: uuid.UUID | None, note: str) -> tuple[AgentAction, str]:
    a = db.get(AgentAction, action_id)
    if a is None:
        raise NotReversible("action not found")
    if a.reverted_at:
        raise NotReversible("action was already reverted")
    if not a.reversible:
        raise NotReversible("this action is not reversible")
    kind, _, ref = (a.output_ref or "").partition(":")
    handler = HANDLERS.get(kind)
    if not handler or not ref:
        raise NotReversible(f"no reversal defined for '{kind or 'unknown'}' actions")
    effect = handler(db, ref, note)
    a.status, a.reverted_at, a.reverted_by, a.revert_note = "reverted", utcnow(), by, note
    return a, effect
