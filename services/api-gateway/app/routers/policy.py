"""Settings - Autonomy & Policy: per-tool autonomy tiers (PRD 8.2), alert thresholds, model/provider settings, webhooks."""
from __future__ import annotations

import uuid
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import Principal, require
from app.util import audit, upstream
from shared.config import settings
from shared.config.roles import SRE_POLICY_TOOLS, Role
from shared.config.tools import ENVIRONMENT_SCOPES, TOOLS, TOOLS_BY_NAME, Tier
from shared.models import AutonomyPolicy, OrgSetting
from shared.models.base import utcnow
from shared.utils import clients
from shared.utils.autonomy import MANDATORY_TOOLS
from shared.utils.clients import ServiceError
from shared.utils.db import get_db

router = APIRouter(tags=["policy"])

TIER_INFO = [
    {"tier": "read_only", "label": "Read-only", "definition": "The agent observes and reports; it never writes or notifies without being asked.", "selectable": True},
    {"tier": "autonomous_background", "label": "Autonomous, background", "definition": "Internal processing runs continuously; nothing is user-facing until requested.", "selectable": True},
    {"tier": "autonomous_policy_bounded", "label": "Autonomous, policy-bounded", "definition": "The agent notifies or drafts unprompted, gated by organization-set thresholds.", "selectable": True},
    {"tier": "propose_only", "label": "Propose-only", "definition": "The agent recommends an action but never executes it itself.", "selectable": True},
    {"tier": "autonomous_execution", "label": "Autonomous execution (opt-in)", "definition": "Future scope (roadmap): direct remediation only where explicitly granted per action type.", "selectable": False},
]


def _row(r: AutonomyPolicy) -> dict:
    return {"scope": r.scope, "tier": r.tier, "min_confidence": r.min_confidence, "requires_approval": r.requires_approval, "enabled": r.enabled,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None}


@router.get("/policy/tools")
def list_policy(p: Principal = Depends(require("policy.view")), db: Session = Depends(get_db)):
    rows = db.execute(select(AutonomyPolicy).where(AutonomyPolicy.org_id == p.org_id)).scalars().all()
    by: dict[str, list[AutonomyPolicy]] = {}
    for r in rows:
        by.setdefault(r.tool_name, []).append(r)
    sre_only = p.role == Role.SRE.value
    return {
        "tiers": TIER_INFO, "scopes": ENVIRONMENT_SCOPES,
        "tools": [{
            "number": t.number, "name": t.name, "title": t.title, "description": t.description, "default_tier": t.default_tier.value, "human_review": t.human_review,
            "flagship": t.flagship, "mandatory": t.name in MANDATORY_TOOLS,
            "editable": t.name not in MANDATORY_TOOLS and (not sre_only or t.name in SRE_POLICY_TOOLS),
            "policies": sorted((_row(r) for r in by.get(t.name, [])), key=lambda x: x["scope"]),
        } for t in TOOLS],
    }


class PolicyBody(BaseModel):
    scope: str = "*"
    tier: Literal["read_only", "autonomous_background", "autonomous_policy_bounded", "propose_only", "autonomous_execution"]
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    requires_approval: bool = False
    enabled: bool = True


@router.put("/policy/tools/{tool_name}")
def set_policy(tool_name: str, body: PolicyBody, request: Request, p: Principal = Depends(require("policy.edit")), db: Session = Depends(get_db)):
    if tool_name not in TOOLS_BY_NAME:
        raise HTTPException(404, "unknown tool")
    if tool_name in MANDATORY_TOOLS:
        raise HTTPException(422, "PII redaction is mandatory and not policy-configurable: it always runs before data is stored or sent to an AI provider.")
    if p.role == Role.SRE.value and tool_name not in SRE_POLICY_TOOLS:
        raise HTTPException(403, "SREs may manage the forecasting autonomy tiers only; other tools need an Admin.")
    if body.scope not in ENVIRONMENT_SCOPES:
        raise HTTPException(422, f"scope must be one of {ENVIRONMENT_SCOPES}")
    if body.tier == Tier.AUTONOMOUS_EXECUTION.value:
        raise HTTPException(422, "Autonomous execution is future scope and cannot be granted in this release; recommended actions stay propose-only.")
    row = db.execute(select(AutonomyPolicy).where(AutonomyPolicy.org_id == p.org_id, AutonomyPolicy.tool_name == tool_name, AutonomyPolicy.scope == body.scope)).scalar_one_or_none()
    before = _row(row) if row else None
    if row is None:
        row = AutonomyPolicy(org_id=p.org_id, tool_name=tool_name, scope=body.scope)
        db.add(row)
    row.tier, row.min_confidence, row.requires_approval, row.enabled = body.tier, body.min_confidence, body.requires_approval, body.enabled
    row.updated_by, row.updated_at = p.id, utcnow()
    db.flush()
    audit(request, p, "policy.update", resource_type="autonomy_policy", resource_id=f"{tool_name}:{body.scope}", details={"before": before, "after": _row(row)})
    return _row(row)


@router.delete("/policy/tools/{tool_name}", status_code=204)
def delete_override(tool_name: str, scope: str, request: Request, p: Principal = Depends(require("policy.edit")), db: Session = Depends(get_db)):
    """Remove an environment-specific override (the '*' row is the base policy and stays)."""
    if scope == "*":
        raise HTTPException(422, "the base ('*') policy cannot be removed")
    if p.role == Role.SRE.value and tool_name not in SRE_POLICY_TOOLS:
        raise HTTPException(403, "SREs may manage the forecasting autonomy tiers only")
    row = db.execute(select(AutonomyPolicy).where(AutonomyPolicy.org_id == p.org_id, AutonomyPolicy.tool_name == tool_name, AutonomyPolicy.scope == scope)).scalar_one_or_none()
    if row:
        db.delete(row)
        audit(request, p, "policy.override_removed", resource_type="autonomy_policy", resource_id=f"{tool_name}:{scope}")


class ForecastSettings(BaseModel):
    warning_threshold: int = Field(ge=1, le=99)
    critical_threshold: int = Field(ge=2, le=100)
    interval_seconds: int = Field(ge=10, le=3600)
    window_seconds: int = Field(default=60, ge=10, le=600)


@router.get("/settings/forecasting")
def get_forecast_settings(p: Principal = Depends(require("settings.thresholds")), db: Session = Depends(get_db)):
    row = db.get(OrgSetting, (p.org_id, "forecasting"))
    return row.value if row else {"warning_threshold": settings.forecast_warning_threshold, "critical_threshold": settings.forecast_critical_threshold,
                                  "interval_seconds": settings.forecast_interval_seconds, "window_seconds": settings.forecast_window_seconds}


@router.put("/settings/forecasting")
def put_forecast_settings(body: ForecastSettings, request: Request, p: Principal = Depends(require("settings.thresholds")), db: Session = Depends(get_db)):
    if body.critical_threshold <= body.warning_threshold:
        raise HTTPException(422, "the critical threshold must be higher than the warning threshold")
    row = db.get(OrgSetting, (p.org_id, "forecasting"))
    before = row.value if row else None
    if row is None:
        row = OrgSetting(org_id=p.org_id, key="forecasting", value={})
        db.add(row)
    row.value, row.updated_by, row.updated_at = body.model_dump(), p.id, utcnow()
    audit(request, p, "settings.forecasting", resource_type="org_setting", resource_id="forecasting", details={"before": before, "after": body.model_dump()})
    return row.value


@router.get("/settings/provider")
def provider_settings(p: Principal = Depends(require("policy.view"))):
    """Model/provider settings (read-only here: provider credentials live in the deployment's secret store)."""
    usage = None
    try:
        usage = clients.ai.get("/internal/usage")
    except ServiceError:
        pass
    host = urlparse(settings.ai_base_url).hostname if settings.ai_base_url else None
    return {
        "provider": settings.ai_provider, "endpoint_host": host, "api_key_configured": bool(settings.ai_api_key),
        "models": {"fast": {"model": settings.ai_fast_model, "used_for": "log classification, chat, search responses, log explanations"},
                   "deep": {"model": settings.ai_deep_model, "used_for": "root cause analysis, incident reports, pre-mortems, executive summaries, failure-risk explanation"},
                   "embedding": {"model": settings.ai_embedding_model, "used_for": "embeddings, semantic search, clustering, forecasting similarity"}},
        "egress_allowlist": sorted(settings.ai_egress_hosts), "embedding_dim": settings.embedding_dim,
        "guardrails": {"pii_redaction": "mandatory - applied before any text leaves for a provider", "egress": "explicit allowlist of approved AI provider endpoints only"},
        "usage": usage, "editable": False,
    }


# ---- webhooks (alert delivery to external systems) ------------------------------------------------------------------------------------
class WebhookBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: str
    secret: str | None = Field(default=None, max_length=200)
    events: list[str] = Field(default_factory=lambda: ["alert.created", "alert.escalated", "report.pre_mortem"])
    min_level: Literal["warning", "critical"] = "warning"
    enabled: bool = True


@router.get("/webhooks")
def list_webhooks(p: Principal = Depends(require("webhooks.manage"))):
    try:
        return clients.notification.get("/webhooks", params={"org_id": str(p.org_id)})
    except ServiceError as exc:
        raise upstream(exc) from exc


@router.post("/webhooks", status_code=201)
def create_webhook(body: WebhookBody, request: Request, p: Principal = Depends(require("webhooks.manage"))):
    try:
        res = clients.notification.post("/webhooks", json={"org_id": str(p.org_id), **body.model_dump()})
    except ServiceError as exc:
        raise upstream(exc) from exc
    audit(request, p, "webhook.create", resource_type="webhook", resource_id=res.get("id"), details={"url": body.url, "events": body.events})
    return res


@router.delete("/webhooks/{webhook_id}", status_code=204)
def delete_webhook(webhook_id: uuid.UUID, request: Request, p: Principal = Depends(require("webhooks.manage"))):
    try:
        clients.notification.delete(f"/webhooks/{webhook_id}")
    except ServiceError as exc:
        raise upstream(exc) from exc
    audit(request, p, "webhook.delete", resource_type="webhook", resource_id=webhook_id)


@router.post("/webhooks/{webhook_id}/test")
def test_webhook(webhook_id: uuid.UUID, request: Request, p: Principal = Depends(require("webhooks.manage"))):
    try:
        res = clients.notification.post(f"/webhooks/{webhook_id}/test", timeout=30)
    except ServiceError as exc:
        raise upstream(exc) from exc
    audit(request, p, "webhook.test", resource_type="webhook", resource_id=webhook_id, details={"ok": res.get("ok")})
    return res


@router.get("/webhooks/deliveries")
def deliveries(p: Principal = Depends(require("webhooks.manage")), limit: int = 50):
    try:
        return clients.notification.get("/deliveries", params={"org_id": str(p.org_id), "limit": limit})
    except ServiceError as exc:
        raise upstream(exc) from exc
