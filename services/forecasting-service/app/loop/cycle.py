"""The forecasting reasoning loop - one cycle for one service (PRD 4.2 / 6.2).

  tick -> error velocity -> pattern drift (embeddings + vector store) -> leading-indicator match ->
  baseline deviation -> weighted risk score -> [above threshold] deep-model explanation + recommended
  actions -> alert (policy-bounded) -> [critical] RCA + pre-mortem drafting.

Graceful degradation: if cloud AI inference is unavailable the alert falls back to a
threshold-based one instead of going silent.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.baseline import current_pattern, deviation
from app.config import CYCLE_BUDGET_SECONDS
from app.drift import detect_drift
from app.indicators import failure_level, get_weights, match_signatures
from app.scoring import RiskAssessment, assess
from app.velocity import compute_velocity
from shared.config import settings
from shared.config.tools import Tier
from shared.models import (
    DeploymentComparison, ForecastCycleMetric, MonitoredService, OrgSetting, PreIncidentAlert, Project, RecommendedAction, RiskSnapshot,
)
from shared.models.base import utcnow
from shared.utils import clients
from shared.utils.analytics import clock_now
from shared.utils.audit import record_agent_action
from shared.utils.autonomy import evaluate
from shared.utils.clients import ServiceError
from shared.utils.db import session_scope
from shared.utils.events import add_feed_item, publish_event

log = logging.getLogger("logpilot.forecast")
RESOLVE_AFTER_CYCLES = 5  # quiet cycles before an open alert auto-resolves
RE_EXPLAIN_DELTA = 15.0  # risk change that triggers a refreshed explanation


def thresholds(db: Session, ms: MonitoredService, org_id: uuid.UUID) -> tuple[int, int, int]:
    """(warning, critical, interval_seconds): service override > org setting > built-in default (60 / 80 / 60s)."""
    row = db.get(OrgSetting, (org_id, "forecasting"))
    cfg = (row.value if row else {}) or {}
    warning = ms.warning_threshold or cfg.get("warning_threshold") or settings.forecast_warning_threshold
    critical = ms.critical_threshold or cfg.get("critical_threshold") or settings.forecast_critical_threshold
    interval = ms.forecast_interval_seconds or cfg.get("interval_seconds") or settings.forecast_interval_seconds
    return int(warning), int(max(critical, warning + 1)), int(max(interval, 5))


def _slim_velocity(v: dict) -> dict:
    return {k: x for k, x in v.items() if k != "series"}


def run_cycle(service_id: uuid.UUID) -> dict[str, Any]:
    t0 = time.monotonic()
    started = utcnow()
    result: dict[str, Any] = {"service_id": str(service_id)}
    metric = ForecastCycleMetric(service_id=service_id, started_at=started)
    event: dict[str, Any] | None = None
    try:
        with session_scope() as db:
            ms = db.get(MonitoredService, service_id)
            if ms is None or not ms.enabled:
                return {"skipped": "service missing or disabled"}
            org_id = db.get(Project, ms.project_id).org_id
            now = clock_now(db, ms.project_id, ms.name)
            warning, critical, interval = thresholds(db, ms, org_id)
            weights = get_weights(db, ms.id)

            vel = compute_velocity(db, ms.project_id, ms.name, ms.environment, now)
            base = deviation(db, ms, now, vel.errors_per_min, current_pattern(db, ms.project_id, ms.name, now))
            drift = detect_drift(db, ms.project_id, ms.name, now)
            sim, top_errors = match_signatures(db, ms, now, weights)
            ra = assess(db, ms.id, vel, sim, base, drift, weights, warning, critical, failure_level(db, ms.id), now)

            snap = RiskSnapshot(
                service_id=ms.id, timestamp=utcnow(), risk_score=ra.score, velocity_score=ra.velocity_score, similarity_score=ra.similarity_score,
                baseline_score=ra.baseline_score, trend=ra.trend, eta_minutes_low=ra.eta_low, eta_minutes_high=ra.eta_high,
                signals={"velocity": vel.as_dict(), "baseline": base.as_dict(), "drift": drift.as_dict(), "similarity": sim.as_dict(),
                         "top_errors": top_errors, "weights": ra.weights, "thresholds": {"warning": warning, "critical": critical},
                         "as_of": now.isoformat()},
                degraded=not (sim.available and drift.available),
            )
            db.add(snap)
            db.flush()
            alert = _handle_alert(db, ms, org_id, ra, vel, sim, base, drift, top_errors, warning, critical, now, snap)
            ms.last_forecast_at = utcnow()
            ms.next_forecast_at = utcnow() + timedelta(seconds=interval)
            snap.cycle_ms = int((time.monotonic() - t0) * 1000)
            metric.degraded = bool(snap.degraded or (alert or {}).get("degraded"))
            event = {
                "service_id": str(ms.id), "service": ms.name, "project_id": str(ms.project_id), "risk_score": round(ra.score, 1),
                "velocity_score": round(ra.velocity_score, 1), "similarity_score": round(ra.similarity_score, 1),
                "baseline_score": round(ra.baseline_score, 1), "trend": ra.trend, "level": ra.level, "eta_minutes_low": ra.eta_low,
                "eta_minutes_high": ra.eta_high, "timestamp": snap.timestamp.isoformat(), "alert_id": (alert or {}).get("id"),
            }
            result.update({"risk_score": round(ra.score, 1), "level": ra.level, "alert": alert, "org_id": str(org_id)})
    except Exception as exc:
        log.exception("forecast cycle failed for %s", service_id)
        metric.completed, metric.error = False, f"{type(exc).__name__}: {exc}"[:480]
        result["error"] = metric.error
    finally:
        metric.duration_ms = int((time.monotonic() - t0) * 1000)
        metric.within_budget = metric.completed and metric.duration_ms < CYCLE_BUDGET_SECONDS * 1000
        try:
            with session_scope() as db:
                db.add(metric)
        except Exception:  # pragma: no cover
            log.warning("could not record cycle metric")
    if event:
        publish_event("risk.updated", event, project_id=event["project_id"], org_id=result.get("org_id"))
    result["duration_ms"] = metric.duration_ms
    return result


# ---- alert lifecycle -----------------------------------------------------------------------------------------------------
def _handle_alert(db: Session, ms: MonitoredService, org_id: uuid.UUID, ra: RiskAssessment, vel, sim, base, drift, top_errors: list[dict],
                  warning: int, critical: int, now: datetime, snap: RiskSnapshot) -> dict | None:
    open_alert = db.execute(
        select(PreIncidentAlert).where(PreIncidentAlert.service_id == ms.id, PreIncidentAlert.resolved_at.is_(None),
                                       PreIncidentAlert.status.in_(["open", "acknowledged", "pending_review"]))
    ).scalars().first()

    if ra.level is None:
        if open_alert:
            open_alert.below_threshold_cycles += 1
            open_alert.risk_score = ra.score
            if open_alert.below_threshold_cycles >= RESOLVE_AFTER_CYCLES:
                open_alert.resolved_at, open_alert.status = utcnow(), "resolved"
                add_feed_item(db, project_id=ms.project_id, org_id=org_id, kind="alert", severity="info", service=ms.name, ref_type="alert",
                              ref_id=open_alert.id, title=f"Risk for {ms.name} has subsided",
                              body="Risk fell below the warning threshold. Please log the outcome (prevented / occurred / false positive) so the agent can learn from it.")
                publish_event("alert.resolved", {"alert_id": str(open_alert.id), "service": ms.name}, project_id=ms.project_id, org_id=org_id)
        return None

    evidence = {
        "top_errors": top_errors, "similar_incidents": sim.matches, "last_explained_risk": ra.score, "rca": None,
        "signals": {"velocity": _slim_velocity(vel.as_dict()), "baseline": base.as_dict(), "similarity": sim.as_dict(), "drift": drift.as_dict()},
        "drivers": _drivers(ra),
    }

    if open_alert is None:
        text_res = _explain(db, ms, ra, vel, base, drift, sim, top_errors, now, warning)
        d_alerts = evaluate(db, org_id, "pre_incident_alerts", requested_tier=Tier.AUTONOMOUS_POLICY_BOUNDED, environment=ms.environment, confidence=ra.score / 100)
        d_fore = evaluate(db, org_id, "proactive_failure_forecasting", requested_tier=Tier.AUTONOMOUS_POLICY_BOUNDED, environment=ms.environment)
        autonomous = d_alerts.autonomous and d_fore.autonomous
        alert = PreIncidentAlert(
            service_id=ms.id, project_id=ms.project_id, service_name=ms.name, risk_score=ra.score, peak_risk_score=ra.score, level=ra.level,
            status="open" if autonomous else "pending_review", alert_text=text_res["alert_text"], recommended_actions=text_res["recommended_actions"],
            failure_probability=text_res["failure_probability"], eta_minutes_low=ra.eta_low, eta_minutes_high=ra.eta_high,
            evidence_chain={**evidence, "explanation": text_res["explanation"]}, pattern_matches=sim.matches, degraded=text_res["degraded"],
        )
        db.add(alert)
        db.flush()
        _store_actions(db, alert, ms, org_id, text_res["recommended_actions"])
        reason = "; ".join(x for x in (d_alerts.reason, d_fore.reason) if x and x != "within granted autonomy")
        record_agent_action(
            db, tool_name="pre_incident_alerts", trigger="schedule", autonomy_level=(d_alerts.effective_tier if autonomous else Tier.PROPOSE_ONLY.value),
            org_id=org_id, project_id=ms.project_id, confidence=ra.score / 100, status="executed" if autonomous else "proposed",
            downgraded_from=None if autonomous else Tier.AUTONOMOUS_POLICY_BOUNDED.value, reversible=True, high_impact=ra.level == "critical",
            output_ref=f"alert:{alert.id}", input_ref=f"risk_snapshot:{snap.id}",
            summary=f"{ra.level.title()} pre-incident alert for {ms.name} (risk {ra.score:.0f}/100)" + ("" if autonomous else f" - routed to human queue: {reason}"),
            details={"risk": round(ra.score, 1), "degraded": text_res["degraded"], "policy": reason or "within granted autonomy"},
        )
        add_feed_item(
            db, project_id=ms.project_id, org_id=org_id, kind="alert", severity="critical" if ra.level == "critical" else "warning", service=ms.name,
            ref_type="alert", ref_id=alert.id, interrupt=autonomous,
            title=f"{ms.name}: {text_res['failure_probability'] * 100:.0f}% failure probability" + ("" if autonomous else " (awaiting human review)"),
            body=text_res["alert_text"], meta={"level": ra.level, "eta": [ra.eta_low, ra.eta_high]},
        )
        _notify(ms, org_id, alert, "alert.created" if autonomous else "alert.pending_review", autonomous)
        if ra.level == "critical" and autonomous:
            _dispatch_pre_mortem(alert.id)
        return {"id": str(alert.id), "level": alert.level, "status": alert.status, "degraded": alert.degraded, "created": True}

    # existing alert: keep it current, escalate when warranted
    escalated = ra.level == "critical" and open_alert.level == "warning"
    ev = dict(open_alert.evidence_chain or {})
    stale = abs(ra.score - float(ev.get("last_explained_risk", ra.score))) >= RE_EXPLAIN_DELTA
    open_alert.risk_score = ra.score
    open_alert.peak_risk_score = max(open_alert.peak_risk_score or 0.0, ra.score)
    open_alert.below_threshold_cycles = 0
    open_alert.eta_minutes_low, open_alert.eta_minutes_high = ra.eta_low, ra.eta_high
    if escalated or stale:
        text_res = _explain(db, ms, ra, vel, base, drift, sim, top_errors, now, warning)
        open_alert.alert_text, open_alert.level = text_res["alert_text"], ra.level
        open_alert.failure_probability, open_alert.degraded = text_res["failure_probability"], text_res["degraded"]
        open_alert.pattern_matches = sim.matches
        open_alert.evidence_chain = {**evidence, "rca": ev.get("rca"), "explanation": text_res["explanation"]}
        if escalated:
            _refresh_open_actions(db, open_alert, ms, org_id, text_res["recommended_actions"])
            open_alert.recommended_actions = text_res["recommended_actions"]
            add_feed_item(db, project_id=ms.project_id, org_id=org_id, kind="alert", severity="critical", service=ms.name, ref_type="alert",
                          ref_id=open_alert.id, interrupt=True, title=f"{ms.name} escalated to CRITICAL ({ra.score:.0f}/100)", body=text_res["alert_text"])
            _notify(ms, org_id, open_alert, "alert.escalated", open_alert.status == "open")
            if open_alert.status == "open":
                _dispatch_pre_mortem(open_alert.id)
    return {"id": str(open_alert.id), "level": open_alert.level, "status": open_alert.status, "degraded": open_alert.degraded, "created": False}


def _drivers(ra: RiskAssessment) -> list[dict]:
    parts = [("Error velocity", ra.velocity_score, ra.weights["velocity"]), ("Pattern similarity", ra.similarity_score, ra.weights["similarity"]),
             ("Baseline deviation", ra.baseline_score, ra.weights["baseline"])]
    return sorted(({"signal": n, "score": round(s, 1), "weight": w, "contribution": round(s * w, 1)} for n, s, w in parts), key=lambda d: -d["contribution"])


def _explain(db: Session, ms: MonitoredService, ra: RiskAssessment, vel, base, drift, sim, top_errors: list[dict], now: datetime, warning: int) -> dict:
    """Deep-model pre-incident explanation. Falls back to a threshold-based alert when AI is unavailable."""
    recent_regression = db.execute(
        select(DeploymentComparison).where(DeploymentComparison.project_id == ms.project_id, DeploymentComparison.service == ms.name,
                                           DeploymentComparison.regression.is_(True), DeploymentComparison.created_at >= now - timedelta(hours=6))
        .order_by(DeploymentComparison.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    ctx = {
        "service": ms.name, "risk_score": round(ra.score, 1), "level": ra.level, "velocity_score": round(ra.velocity_score, 1),
        "similarity_score": round(ra.similarity_score, 1), "baseline_score": round(ra.baseline_score, 1),
        "signals": {"velocity": _slim_velocity(vel.as_dict()), "baseline": base.as_dict(), "similarity": sim.as_dict(), "drift": drift.as_dict()},
        "eta": {"low": ra.eta_low, "high": ra.eta_high}, "top_errors": top_errors,
        "similar_incidents": sim.matches, "playbook": _playbook_steps(ms),
        "deployment": ({"service": recent_regression.service, "from": {"version": recent_regression.from_version}, "to": {"version": recent_regression.to_version},
                        "regression": True, "regression_reasons": recent_regression.result.get("regression_reasons", [])} if recent_regression else None),
    }
    try:
        res = clients.ai.post("/internal/forecast/explain", json=ctx, timeout=55)  # background cycle: no one is watching a spinner
        actions = res.get("recommended_actions") or []
        return {"alert_text": res["alert_text"], "explanation": res.get("explanation", ""), "recommended_actions": actions,
                "failure_probability": float(res.get("failure_probability", ra.score / 100)), "degraded": False}
    except (ServiceError, KeyError, ValueError) as exc:
        log.warning("AI explanation unavailable for %s (%s) - threshold-based fallback", ms.name, exc)
    # ---- threshold-based fallback: never go silent ----
    hist_actions = [
        {"text": a, "rationale": f"Resolved the similar incident \"{m.get('label')}\".", "risk_level": "high", "source": "historical"}
        for m in sim.matches[:2] for a in (m.get("resolved_actions") or [])[:2]
    ] or [{"text": f"Investigate the dominant error in {ms.name} and review recent changes", "rationale": "AI reasoning unavailable; generic first step.", "risk_level": "low", "source": "playbook"}]
    return {
        "alert_text": (f"{ms.name}: risk score {ra.score:.0f}/100 ({ra.level}). Error rate {vel.errors_per_min:.1f}/min is {max(base.ratio, vel.ratio):.1f}x baseline "
                       f"(threshold {warning}). AI reasoning is currently unavailable, so this is a threshold-based alert."),
        "explanation": "AI inference unavailable - alert generated from threshold rules only.", "recommended_actions": hist_actions,
        "failure_probability": min(0.97, max(0.05, ra.score / 100)), "degraded": True,
    }


def _playbook_steps(ms: MonitoredService) -> list[str]:
    pb = ms.playbook or {}
    steps = pb.get("steps") if isinstance(pb, dict) else pb
    return [str(s) for s in (steps or [])][:6]


def _store_actions(db: Session, alert: PreIncidentAlert, ms: MonitoredService, org_id: uuid.UUID, actions: list[dict]) -> None:
    decision = evaluate(db, org_id, "recommended_actions", requested_tier=Tier.PROPOSE_ONLY, environment=ms.environment)
    for i, a in enumerate(actions[:6], 1):
        db.add(RecommendedAction(
            alert_id=alert.id, project_id=ms.project_id, service_name=ms.name, rank=i, text=str(a.get("text", ""))[:1000],
            rationale=a.get("rationale"), source=a.get("source", "llm") if a.get("source") in ("historical", "llm", "playbook") else "llm",
            risk_level=a.get("risk_level", "low") if a.get("risk_level") in ("low", "high") else "low",
            autonomy_tier=decision.effective_tier, status="proposed",
        ))
    db.flush()
    record_agent_action(
        db, tool_name="recommended_actions", trigger="schedule", autonomy_level=Tier.PROPOSE_ONLY.value, org_id=org_id, project_id=ms.project_id,
        status="proposed", output_ref=f"alert:{alert.id}", summary=f"Proposed {len(actions[:6])} remediation action(s) for {ms.name} - awaiting human approval",
        details={"note": "propose-only: the agent never executes these itself"},
    )


def _refresh_open_actions(db: Session, alert: PreIncidentAlert, ms: MonitoredService, org_id: uuid.UUID, actions: list[dict]) -> None:
    for a in db.query(RecommendedAction).filter(RecommendedAction.alert_id == alert.id, RecommendedAction.status == "proposed").all():
        db.delete(a)  # only undecided proposals are replaced; human decisions are preserved
    db.flush()
    keep = {(a.edited_text or a.text).lower() for a in db.query(RecommendedAction).filter(RecommendedAction.alert_id == alert.id)}
    _store_actions(db, alert, ms, org_id, [a for a in actions if str(a.get("text", "")).lower() not in keep])


def _notify(ms: MonitoredService, org_id: uuid.UUID, alert: PreIncidentAlert, event_type: str, external: bool) -> None:
    payload = {
        "type": event_type, "org_id": str(org_id), "project_id": str(ms.project_id), "external": external, "dedupe_key": f"{alert.id}:{alert.level}",
        "alert": {"id": str(alert.id), "service": ms.name, "level": alert.level, "risk_score": round(alert.risk_score, 1), "text": alert.alert_text,
                  "eta_minutes_low": alert.eta_minutes_low, "eta_minutes_high": alert.eta_minutes_high, "degraded": alert.degraded,
                  "actions": [a.get("text") for a in (alert.recommended_actions or [])][:5]},
    }
    try:
        clients.notification.post("/notify", json=payload, timeout=10)
    except ServiceError:
        publish_event(event_type, payload["alert"], project_id=ms.project_id, org_id=org_id, interrupt=True)  # in-app fallback


def _dispatch_pre_mortem(alert_id: uuid.UUID) -> None:
    from app.loop.scheduler import celery_app

    celery_app.send_task("forecasting.draft_pre_mortem", args=[str(alert_id)], queue="forecasting")
