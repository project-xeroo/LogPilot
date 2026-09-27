"""Health check endpoints for every service (PRD 10.4)."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends

from app.auth import Principal, require
from shared.config import settings
from shared.utils import storage, vectorstore
from shared.utils.db import db_healthy
from shared.utils.events import get_redis

router = APIRouter(tags=["system"])


def _redis_ok() -> bool:
    try:
        return bool(get_redis().ping())
    except Exception:
        return False


@router.get("/health")
def health():
    """Liveness/readiness for load balancers."""
    ok = db_healthy()
    return {"service": "api-gateway", "status": "ok" if ok else "degraded", "database": ok}


@router.get("/system/health")
def system_health(p: Principal = Depends(require("policy.view"))):
    """Aggregate view of every component (Admin/SRE)."""
    out: dict[str, dict] = {}
    for name, url in {"ai-service": settings.ai_service_url, "log-ingestion-service": settings.ingestion_service_url, "forecasting-service": settings.forecasting_service_url,
                      "notification-service": settings.notification_service_url, "audit-service": settings.audit_service_url}.items():
        try:
            r = httpx.get(f"{url}/health", timeout=3)
            out[name] = r.json()
        except Exception as exc:  # noqa: BLE001
            out[name] = {"status": "down", "error": str(exc)[:120]}
    out["postgres"] = {"status": "ok" if db_healthy() else "down"}
    out["redis"] = {"status": "ok" if _redis_ok() else "down"}
    out["vector-store"] = {"status": "ok" if vectorstore.healthy() else "down"}
    out["object-storage"] = {"status": "ok" if storage.healthy() else "down"}
    overall = "ok" if all(v.get("status") == "ok" for v in out.values()) else "degraded"
    return {"status": overall, "components": out}
