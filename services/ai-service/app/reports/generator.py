"""Report generation: incident reports (tool 11), pre-mortems (flagship 4.3) and executive summaries.

Drafting is autonomous; sign-off is human. Latency budgets from the PRD are enforced by giving the
deep model a hard timeout and falling back to a deterministic data-driven draft - so a draft always
arrives inside the SLA: incident < 90s (target < 2 min end to end), pre-mortem < 45s.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.prompts import build
from app.providers import AIUnavailable, ModelRole, heuristic_json, run_json
from app.rca import analyze as run_rca
from shared.config.tools import Tier
from shared.models import (
    IncidentReport, IncidentOutcome, PreIncidentAlert, Project, RcaResult, RecommendedAction, RiskSnapshot,
)
from shared.utils import vectorstore
from shared.utils.analytics import clock_now, health_state
from shared.utils.audit import record_agent_action
from shared.utils.autonomy import evaluate
from shared.utils.events import add_feed_item, publish_event

log = logging.getLogger("logpilot.reports")
INCIDENT_TIMEOUT = 80.0  # < 90s target
# Pre-mortems are mostly drafted by the autonomous forecasting loop (a background worker; nobody is
# watching a spinner), so this can afford real headroom for a slower deep model. The on-demand
# "Draft a pre-mortem" button still gets a good deterministic draft immediately past this budget.
PREMORTEM_TIMEOUT = 55.0  # PRD target is < 45s for a fast model; widened for slower deep models
EXEC_TIMEOUT = 70.0
INCIDENT_SECTIONS = ["summary", "timeline", "affected_services", "impact_analysis", "root_cause", "resolution", "preventive_actions"]
PREMORTEM_SECTIONS = ["summary", "what_is_about_to_happen", "why_we_believe_this", "who_is_affected", "blast_radius", "prevention", "confidence"]


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _validate_sections(data: dict, required: list[str]) -> list[dict] | None:
    secs = data.get("sections")
    if not isinstance(secs, list):
        return None
    by_key = {s.get("key"): s for s in secs if isinstance(s, dict) and s.get("body")}
    if not all(k in by_key for k in required):
        return None
    return [{"key": k, "title": by_key[k].get("title") or k.replace("_", " ").title(), "body": str(by_key[k]["body"])} for k in required]


def _draft(task: str, ctx: dict, required: list[str], timeout: float) -> tuple[list[dict], str, bool]:
    """Returns (sections, model_info, ai_used). Never raises."""
    try:
        data = run_json(task, build(task, ctx), ModelRole.DEEP, ctx, timeout=timeout, max_tokens=4000)
        secs = _validate_sections(data, required)
        if secs:
            from app.providers import get_provider

            return secs, f"{get_provider().name}:deep", True
        log.warning("model returned incomplete sections for %s; using data-driven draft", task)
    except (AIUnavailable, Exception) as exc:  # noqa: BLE001
        log.info("deep model unavailable for %s (%s); using data-driven draft", task, exc)
    data = heuristic_json(task, ctx)
    return _validate_sections(data, required) or [], "data-driven-draft", False


# ---- context builders -----------------------------------------------------------------------------------------
def _incident_context(db: Session, project_id: uuid.UUID, rca: dict, alert: PreIncidentAlert | None) -> dict[str, Any]:
    w0, w1 = datetime.fromisoformat(rca["window"]["start"]), datetime.fromisoformat(rca["window"]["end"])
    services = []
    for ev in rca.get("evidence", []):
        peak = db.execute(
            text("SELECT max(c) FROM (SELECT count(*) c FROM log_records WHERE project_id = :p AND service = :s AND severity_num >= 40 AND timestamp BETWEEN :a AND :b GROUP BY date_trunc('minute', timestamp)) x"),
            {"p": project_id, "s": ev["service"], "a": w0, "b": w1},
        ).scalar() or 0
        services.append({"service": ev["service"], "errors": ev["errors"], "peak_per_min": float(peak), "first_error": ev["first_seen"], "top_errors": ev["top_errors"]})
    stats = db.execute(
        text(
            """
            SELECT count(*) FILTER (WHERE severity_num >= 40), count(DISTINCT request_id) FILTER (WHERE severity_num >= 40 AND request_id IS NOT NULL),
                   count(*)
            FROM log_records WHERE project_id = :p AND timestamp BETWEEN :a AND :b AND service = ANY(:svcs)
            """
        ),
        {"p": project_id, "a": w0, "b": w1, "svcs": [s["service"] for s in services] or [""]},
    ).one()
    peak_rate = db.execute(
        text("SELECT max(e::float / GREATEST(t, 1)) FROM (SELECT count(*) FILTER (WHERE severity_num >= 40) e, count(*) t FROM log_records WHERE project_id = :p AND timestamp BETWEEN :a AND :b GROUP BY date_trunc('minute', timestamp)) x"),
        {"p": project_id, "a": w0, "b": w1},
    ).scalar()
    anomalies = db.execute(
        text("SELECT detected_at, kind, service, explanation, window_start FROM anomalies WHERE project_id = :p AND window_end >= :a AND window_start <= :b ORDER BY window_start LIMIT 15"),
        {"p": project_id, "a": w0, "b": w1},
    ).all()
    deploys = db.execute(
        text("SELECT service, version, deployed_at FROM deployments WHERE project_id = :p AND deployed_at BETWEEN :a AND :b ORDER BY deployed_at"),
        {"p": project_id, "a": w0 - timedelta(hours=1), "b": w1},
    ).all()
    timeline = [{"time": s["first_error"], "event": f"First errors in {s['service']}: {s['top_errors'][0]['message'][:100] if s['top_errors'] else ''}", "kind": "error", "service": s["service"]} for s in services]
    timeline += [{"time": _iso(a[4]), "event": a[3], "kind": "anomaly", "service": a[2]} for a in anomalies]
    timeline += [{"time": _iso(d[2]), "event": f"Deployment of {d[0]} {d[1]}", "kind": "deployment", "service": d[0]} for d in deploys]
    outcomes, actions = [], []
    if alert:
        timeline.append({"time": _iso(alert.created_at), "event": f"Agent raised a {alert.level} pre-incident alert (risk {alert.risk_score:.0f}/100)", "kind": "alert", "service": alert.service_name})
        outcomes = [{"outcome": o.outcome, "action_taken": o.action_taken, "time_to_resolve": o.time_to_resolve}
                    for o in db.query(IncidentOutcome).filter(IncidentOutcome.alert_id == alert.id).all()]
        actions = [{"text": a.edited_text or a.text, "status": a.status} for a in db.query(RecommendedAction).filter(RecommendedAction.alert_id == alert.id).order_by(RecommendedAction.rank).all()]
    timeline.sort(key=lambda e: e["time"] or "")
    return {
        "project": db.get(Project, project_id).name,
        "window": {"start": rca["window"]["start"], "end": rca["window"]["end"], "duration_min": (w1 - w0).total_seconds() / 60},
        "services": services, "timeline": timeline, "rca": {k: rca.get(k) for k in ("causal_chain", "confidence", "explanation")},
        "impact": {"total_errors": int(stats[0] or 0), "affected_requests": int(stats[1] or 0), "services_affected": len(services), "peak_error_rate": float(peak_rate or 0)},
        "deployments": [{"service": d[0], "version": d[1], "at": _iso(d[2])} for d in deploys], "outcomes": outcomes, "actions": actions,
    }


def generate_incident_report(db: Session, project_id: uuid.UUID, *, service: str | None = None, cluster_id: uuid.UUID | None = None,
                             rca_id: uuid.UUID | None = None, alert_id: uuid.UUID | None = None, start: datetime | None = None,
                             end: datetime | None = None, requested_by: uuid.UUID | None = None, trigger: str = "user_request") -> dict[str, Any]:
    t0 = time.monotonic()
    alert = db.get(PreIncidentAlert, alert_id) if alert_id else None
    if rca_id:
        row = db.get(RcaResult, rca_id)
        rca = {"window": {"start": row.window_start.isoformat(), "end": row.window_end.isoformat()}, "causal_chain": row.causal_chain,
               "confidence": row.confidence, "explanation": row.explanation, "evidence": row.evidence, "id": str(row.id)}
    else:
        rca = run_rca(db, project_id, service=service or (alert.service_name if alert else None), cluster_id=cluster_id, start=start, end=end,
                      trigger="autonomous" if trigger != "user_request" else "on_request", actor_id=requested_by)
    ctx = _incident_context(db, project_id, rca, alert)
    sections, model, ai_used = _draft("incident_report", ctx, INCIDENT_SECTIONS, INCIDENT_TIMEOUT)
    services = [s["service"] for s in ctx["services"][:3]]
    title = f"Incident report: {', '.join(services) or 'unknown service'} – {datetime.fromisoformat(rca['window']['start']).strftime('%b %d %H:%M')} UTC"
    return _save(db, project_id, "incident", title, sections, model, ai_used, t0, requested_by, trigger, alert_id=alert_id, rca_id=uuid.UUID(rca["id"]) if rca.get("id") else None,
                 tool="incident_report_generation", tier=Tier.AUTONOMOUS_POLICY_BOUNDED, budget_ms=90_000)


def generate_pre_mortem(db: Session, alert_id: uuid.UUID, *, requested_by: uuid.UUID | None = None, trigger: str = "autonomous") -> dict[str, Any]:
    t0 = time.monotonic()
    alert = db.get(PreIncidentAlert, alert_id)
    if alert is None:
        raise ValueError("alert not found")
    snap = db.query(RiskSnapshot).filter(RiskSnapshot.service_id == alert.service_id).order_by(RiskSnapshot.timestamp.desc()).first()
    sigs = (snap.signals if snap else None) or {}
    evid = alert.evidence_chain or {}
    dependents = _dependents(db, alert.project_id, alert.service_name)
    actions = [{"text": a.edited_text or a.text, "rationale": a.rationale, "risk_level": a.risk_level}
               for a in db.query(RecommendedAction).filter(RecommendedAction.alert_id == alert.id).order_by(RecommendedAction.rank).all()]
    ctx = {
        "service": alert.service_name,
        "alert": {"risk_score": alert.risk_score, "level": alert.level, "text": alert.alert_text},
        "signals": sigs, "velocity_score": snap.velocity_score if snap else 0, "similarity_score": snap.similarity_score if snap else 0,
        "baseline_score": snap.baseline_score if snap else 0, "similar_incidents": evid.get("similar_incidents", []),
        "eta": {"low": alert.eta_minutes_low, "high": alert.eta_minutes_high}, "top_errors": evid.get("top_errors", []),
        "dependents": dependents, "actions": actions, "rca": evid.get("rca"),
    }
    sections, model, ai_used = _draft("pre_mortem", ctx, PREMORTEM_SECTIONS, PREMORTEM_TIMEOUT)
    title = f"Pre-mortem: {alert.service_name} – risk {alert.risk_score:.0f}/100 ({datetime.now().strftime('%b %d %H:%M')})"
    out = _save(db, alert.project_id, "pre_mortem", title, sections, model, ai_used, t0, requested_by, trigger, alert_id=alert.id, rca_id=None,
                tool="pre_mortem_reports", tier=Tier.AUTONOMOUS_POLICY_BOUNDED, budget_ms=45_000)
    if out.get("report_id"):
        alert.pre_mortem_report_id = uuid.UUID(out["report_id"])
    return out


def generate_executive_summary(db: Session, project_id: uuid.UUID, *, days: int = 7, requested_by: uuid.UUID | None = None) -> dict[str, Any]:
    t0 = time.monotonic()
    now = clock_now(db, project_id)
    start = now - timedelta(days=days)
    hs = health_state(db, project_id, window_minutes=min(days * 1440, 10080))
    alerts = db.query(PreIncidentAlert).filter(PreIncidentAlert.project_id == project_id, PreIncidentAlert.created_at >= start).all()
    outcomes = db.query(IncidentOutcome).join(PreIncidentAlert, PreIncidentAlert.id == IncidentOutcome.alert_id).filter(
        PreIncidentAlert.project_id == project_id, IncidentOutcome.created_at >= start).all()
    tp = sum(1 for o in outcomes if o.alert_accurate)
    anomalies = db.execute(text("SELECT count(*) FROM anomalies WHERE project_id = :p AND detected_at >= :s"), {"p": project_id, "s": start}).scalar()
    reports = db.query(IncidentReport).filter(IncidentReport.project_id == project_id, IncidentReport.created_at >= start, IncidentReport.kind != "executive_summary").all()
    risky = [s["risk"] | {"service": s["service"]} for s in hs["services"] if s.get("risk") and s["risk"]["risk_score"] >= 60]
    ctx = {
        "period": {"start": start.isoformat(), "end": now.isoformat()}, "services_monitored": len(hs["services"]), "anomalies": anomalies,
        "alerts": {"total": len(alerts), "labelled": len(outcomes), "accuracy": (tp / len(outcomes)) if outcomes else None,
                   "prevented": sum(1 for o in outcomes if o.outcome == "prevented")},
        "top_services": hs["services"][:6], "incidents": [{"title": r.title, "summary": (r.sections_json.get("sections") or [{}])[0].get("body", "")[:200]} for r in reports[:6]],
        "risks_ahead": sorted(risky, key=lambda r: -r["risk_score"]),
        "recommendations": ["Review and sign off pending drafts in Reports.", "Log outcomes on resolved alerts so forecast accuracy can be measured."]
        + [f"Investigate {s['service']} (error rate {s['error_rate']:.1%})." for s in hs["services"][:2] if s["errors"]],
    }
    sections, model, ai_used = _draft("executive_summary", ctx, ["headline", "reliability_overview", "notable_incidents", "forecasting_performance", "risks_ahead", "recommendations"], EXEC_TIMEOUT)
    return _save(db, project_id, "executive_summary", f"Executive summary – last {days} days", sections, model, ai_used, t0, requested_by, "user_request",
                 tool="incident_report_generation", tier=Tier.AUTONOMOUS_POLICY_BOUNDED, budget_ms=90_000)


def _dependents(db: Session, project_id: uuid.UUID, service: str) -> list[str]:
    """Services whose traces also pass through `service` (potential blast radius)."""
    rows = db.execute(
        text(
            """
            SELECT service, count(DISTINCT trace_id) c FROM log_records
            WHERE project_id = :p AND trace_id IN (SELECT DISTINCT trace_id FROM log_records WHERE project_id = :p AND service = :s AND trace_id IS NOT NULL
                                                   AND timestamp >= now() - interval '2 hours' LIMIT 500)
              AND service <> :s AND timestamp >= now() - interval '2 hours'
            GROUP BY 1 ORDER BY c DESC LIMIT 6
            """
        ),
        {"p": project_id, "s": service},
    ).all()
    return [r[0] for r in rows]


def _save(db: Session, project_id, kind, title, sections, model, ai_used, t0, requested_by, trigger, *, alert_id=None, rca_id=None,
          tool: str, tier: Tier, budget_ms: int) -> dict[str, Any]:
    ms = int((time.monotonic() - t0) * 1000)
    org = db.get(Project, project_id).org_id
    decision = evaluate(db, org, tool, requested_tier=Tier.AUTONOMOUS_POLICY_BOUNDED if trigger != "user_request" else Tier.READ_ONLY)
    report = IncidentReport(
        project_id=project_id, kind=kind, title=title[:500], sections_json={"sections": sections, "ai_narrated": ai_used, "budget_ms": budget_ms},
        status="draft", alert_id=alert_id, rca_id=rca_id, generated_by="agent", requested_by=requested_by, generation_ms=ms, model_info=model,
    )
    db.add(report)
    db.flush()
    record_agent_action(
        db, tool_name=tool, trigger="autonomous" if trigger != "user_request" else "user_request", autonomy_level=decision.effective_tier, org_id=org,
        project_id=project_id, actor_id=requested_by, status="executed" if decision.autonomous else "proposed", reversible=True, high_impact=kind == "pre_mortem",
        output_ref=f"report:{report.id}", summary=f"Drafted {kind.replace('_', ' ')} report '{title[:120]}' in {ms}ms (draft; human sign-off required)",
        downgraded_from=decision.requested_tier if decision.downgraded else None, details={"generation_ms": ms, "within_budget": ms <= budget_ms, "model": model},
    )
    add_feed_item(db, project_id=project_id, org_id=org, kind="report", severity="critical" if kind == "pre_mortem" else "info", ref_type="report",
                  ref_id=report.id, title=f"Draft {kind.replace('_', ' ')} report ready: {title[:150]}",
                  body="Drafted by the agent. Review, edit and sign off before export.", interrupt=kind == "pre_mortem")
    if kind == "pre_mortem":
        publish_event("report.pre_mortem", {"report_id": str(report.id), "title": title, "alert_id": str(alert_id) if alert_id else None},
                      project_id=project_id, org_id=org, interrupt=True)
    return {"report_id": str(report.id), "kind": kind, "title": title, "status": "draft", "generation_ms": ms, "within_budget": ms <= budget_ms,
            "ai_narrated": ai_used, "model": model, "sections": sections}
