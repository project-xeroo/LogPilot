"""Notification Service: alert delivery (PRD 4.4: in-app real-time via WebSocket; webhook endpoints
for external integration). Slack/PagerDuty delivery is explicit future scope (PRD 14)."""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.channels import in_app, webhook
from app.config import SERVICE_NAME
from app.policies import endpoints_for, is_duplicate, rate_limited
from app.templates import render
from shared.models import NotificationDelivery, WebhookEndpoint
from shared.utils.db import SessionLocal, db_healthy, get_db, init_db
from shared.utils.logsetup import setup_logging
from shared.utils.web import require_internal

setup_logging(SERVICE_NAME)
log = logging.getLogger("logpilot.notify")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="LogPilot Notification Service", version="2.0.0", lifespan=lifespan)
_dep = [Depends(require_internal)]


@app.get("/health")
def health():
    return {"service": SERVICE_NAME, "status": "ok" if db_healthy() else "degraded"}


class NotifyBody(BaseModel):
    type: str  # alert.created | alert.escalated | alert.pending_review | alert.resolved | report.pre_mortem ...
    org_id: uuid.UUID
    project_id: uuid.UUID
    alert: dict[str, Any]
    external: bool = True  # False when the alert is awaiting human review (in-app only)
    dedupe_key: str | None = None


def _deliver_webhooks(org_id: uuid.UUID, event_type: str, level: str, payload: dict, message: dict) -> None:
    db = SessionLocal()
    try:
        eps = endpoints_for(event_type, level, db.execute(select(WebhookEndpoint).where(WebhookEndpoint.org_id == org_id)).scalars().all())
        for ep in eps:
            ok, code, err, attempts = webhook.deliver(ep.url, ep.secret, event_type, {**payload, "message": message})
            db.add(NotificationDelivery(org_id=org_id, channel="webhook", endpoint_id=ep.id, event_type=event_type, status="delivered" if ok else "failed",
                                        attempts=attempts, response_code=code, error=err, payload={"alert_id": payload.get("alert", {}).get("id")}))
            db.commit()
    finally:
        db.close()


@app.post("/notify", dependencies=_dep, status_code=202)
def notify(body: NotifyBody, bg: BackgroundTasks, db=Depends(get_db)):
    alert = body.alert
    level = alert.get("level", "warning")
    if is_duplicate(body.dedupe_key):
        db.add(NotificationDelivery(org_id=body.org_id, channel="in_app", event_type=body.type, status="suppressed", dedupe_key=body.dedupe_key))
        return {"delivered": [], "suppressed": "duplicate"}
    if rate_limited(str(body.project_id), alert.get("service", "?")):
        db.add(NotificationDelivery(org_id=body.org_id, channel="in_app", event_type=body.type, status="suppressed", error="rate limited"))
        return {"delivered": [], "suppressed": "rate_limited"}
    message = render(body.type, alert)
    channels = ["in_app"]
    in_app.deliver(body.type, {**alert, "message": message}, project_id=str(body.project_id), org_id=str(body.org_id), interrupt=True)
    db.add(NotificationDelivery(org_id=body.org_id, channel="in_app", event_type=body.type, status="delivered", attempts=1, dedupe_key=body.dedupe_key,
                                payload={"alert_id": alert.get("id")}))
    if body.external:
        bg.add_task(_deliver_webhooks, body.org_id, body.type, level, {"project_id": str(body.project_id), "alert": alert}, message)
        channels.append("webhook")
    return {"delivered": channels}


# ---- webhook endpoint management (called by the gateway after RBAC: Admin/SRE) --------------------------------------------
class WebhookBody(BaseModel):
    org_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    url: str
    secret: str | None = None
    events: list[str] = Field(default_factory=lambda: ["alert.created", "alert.escalated", "report.pre_mortem"])
    min_level: str = "warning"
    enabled: bool = True


def _wh(e: WebhookEndpoint) -> dict:
    return {"id": str(e.id), "name": e.name, "url": e.url, "events": e.events, "min_level": e.min_level, "enabled": e.enabled,
            "has_secret": bool(e.secret), "created_at": e.created_at.isoformat()}


@app.get("/webhooks", dependencies=_dep)
def list_webhooks(org_id: uuid.UUID, db=Depends(get_db)):
    return [_wh(e) for e in db.execute(select(WebhookEndpoint).where(WebhookEndpoint.org_id == org_id)).scalars()]


@app.post("/webhooks", dependencies=_dep, status_code=201)
def create_webhook(body: WebhookBody, db=Depends(get_db)):
    try:
        webhook.validate_url(body.url)
    except webhook.WebhookRejected as exc:
        raise HTTPException(422, str(exc)) from exc
    e = WebhookEndpoint(**body.model_dump())
    db.add(e)
    db.flush()
    return _wh(e)


@app.delete("/webhooks/{webhook_id}", dependencies=_dep, status_code=204)
def delete_webhook(webhook_id: uuid.UUID, db=Depends(get_db)):
    e = db.get(WebhookEndpoint, webhook_id)
    if not e:
        raise HTTPException(404, "webhook not found")
    db.delete(e)


@app.post("/webhooks/{webhook_id}/test", dependencies=_dep)
def test_webhook(webhook_id: uuid.UUID, db=Depends(get_db)):
    e = db.get(WebhookEndpoint, webhook_id)
    if not e:
        raise HTTPException(404, "webhook not found")
    sample = {"id": "test", "service": "test-service", "level": "warning", "risk_score": 65, "text": "This is a test notification from LogPilot."}
    ok, code, err, attempts = webhook.deliver(e.url, e.secret, "test", {"alert": sample, "message": render("alert.created", sample)})
    return {"ok": ok, "status_code": code, "error": err, "attempts": attempts}


@app.get("/deliveries", dependencies=_dep)
def deliveries(org_id: uuid.UUID, limit: int = 50, db=Depends(get_db)):
    rows = db.execute(select(NotificationDelivery).where(NotificationDelivery.org_id == org_id).order_by(NotificationDelivery.created_at.desc()).limit(min(limit, 200))).scalars()
    return [{"id": str(r.id), "channel": r.channel, "event_type": r.event_type, "status": r.status, "attempts": r.attempts, "response_code": r.response_code,
             "error": r.error, "created_at": r.created_at.isoformat()} for r in rows]
