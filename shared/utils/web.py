"""FastAPI helpers shared by all internal services: internal-token auth and health endpoints."""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Header, HTTPException, Request

from shared.utils.security import internal_token_ok


def require_internal(x_internal_token: str | None = Header(default=None)) -> None:
    """Internal services trust only the API gateway / peer services (plus VPC/network policy)."""
    if not internal_token_ok(x_internal_token):
        raise HTTPException(status_code=401, detail="internal authentication required")


@dataclass
class Actor:
    user_id: uuid.UUID | None
    org_id: uuid.UUID | None
    role: str | None
    email: str | None
    guided_mode: bool = False


def _uuid(v: str | None):
    try:
        return uuid.UUID(v) if v else None
    except ValueError:
        return None


def actor_from_request(request: Request) -> Actor:
    h = request.headers
    return Actor(
        user_id=_uuid(h.get("x-actor-user-id")),
        org_id=_uuid(h.get("x-actor-org-id")),
        role=h.get("x-actor-role"),
        email=h.get("x-actor-email"),
        guided_mode=h.get("x-actor-guided-mode") == "True",
    )
