"""Small helpers shared by gateway routers."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import HTTPException, Request

from app.auth import Principal
from shared.utils.audit import emit_audit
from shared.utils.clients import ServiceError, ServiceUnavailable


def iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def audit(request: Request | None, p: Principal, action: str, *, resource_type: str | None = None, resource_id: Any = None,
          project_id: uuid.UUID | str | None = None, details: dict | None = None) -> None:
    emit_audit(action, org_id=p.org_id, actor_id=p.id, actor_label=p.email, resource_type=resource_type, resource_id=resource_id, project_id=project_id,
               ip=(request.client.host if request and request.client else p.ip), details=details)


def upstream(exc: ServiceError) -> HTTPException:
    """Translate an internal-service failure into a clean API error."""
    if isinstance(exc, ServiceUnavailable):
        return HTTPException(503, f"{exc.service} is currently unavailable")
    status = exc.status if 400 <= exc.status < 600 and exc.status != 401 else 502
    return HTTPException(status, exc.detail)
