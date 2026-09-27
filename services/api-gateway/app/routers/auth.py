"""Login (JWT with configurable expiry), current user, guided-mode toggle."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import Principal, accessible_project_ids, current_user
from app.util import audit, iso
from shared.config.roles import permissions_for
from shared.models import Project, User
from shared.models.base import utcnow
from shared.utils.db import get_db
from shared.utils.events import get_redis
from shared.utils.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])
MAX_FAILURES = 8
LOCK_SECONDS = 900


class LoginBody(BaseModel):
    email: str
    password: str


def user_payload(u: User | Principal, db: Session, p: Principal | None = None) -> dict:
    pr = p or Principal(u.id, u.org_id, u.email, u.name, u.role, u.guided_mode)
    projects = db.execute(select(Project).where(Project.id.in_(accessible_project_ids(db, pr)))).scalars().all()
    return {
        "id": str(u.id), "email": u.email, "name": u.name, "role": u.role, "guided_mode": u.guided_mode, "org_id": str(u.org_id),
        "permissions": permissions_for(u.role), "cross_project": pr.is_cross_project,
        "projects": [{"id": str(x.id), "name": x.name, "environment": x.environment} for x in projects],
    }


@router.post("/login")
def login(body: LoginBody, request: Request, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    key = f"auth:fail:{email}"
    try:
        if int(get_redis().get(key) or 0) >= MAX_FAILURES:
            raise HTTPException(429, "too many failed sign-in attempts; try again in 15 minutes")
    except HTTPException:
        raise
    except Exception:
        pass
    u = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    ok = bool(u and u.is_active and verify_password(body.password, u.password_hash))
    if not ok:
        try:
            r = get_redis()
            r.incr(key)
            r.expire(key, LOCK_SECONDS)
        except Exception:
            pass
        from shared.utils.audit import emit_audit

        emit_audit("auth.login_failed", actor_label=email, ip=request.client.host if request.client else None, details={"reason": "bad credentials"})
        raise HTTPException(401, "invalid email or password")
    try:
        get_redis().delete(key)
    except Exception:
        pass
    u.last_login_at = utcnow()
    token, ttl = create_access_token(u.id, u.org_id, u.role)
    p = Principal(u.id, u.org_id, u.email, u.name, u.role, u.guided_mode, request.client.host if request.client else None)
    audit(request, p, "auth.login")
    return {"access_token": token, "token_type": "bearer", "expires_in": ttl, "user": user_payload(u, db, p)}


@router.get("/me")
def me(p: Principal = Depends(current_user), db: Session = Depends(get_db)):
    u = db.get(User, p.id)
    return user_payload(u, db, p)


class GuidedBody(BaseModel):
    enabled: bool


@router.patch("/me/guided-mode")
def set_guided(body: GuidedBody, request: Request, p: Principal = Depends(current_user), db: Session = Depends(get_db)):
    """Any account can toggle guided mode; it is on by default for the Junior Engineer role."""
    u = db.get(User, p.id)
    u.guided_mode = body.enabled
    audit(request, p, "user.guided_mode", resource_type="user", resource_id=p.id, details={"enabled": body.enabled})
    return {"guided_mode": u.guided_mode}


class PasswordBody(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=128)


@router.post("/change-password", status_code=204)
def change_password(body: PasswordBody, request: Request, p: Principal = Depends(current_user), db: Session = Depends(get_db)):
    u = db.get(User, p.id)
    if not verify_password(body.current_password, u.password_hash):
        raise HTTPException(400, "current password is incorrect")
    u.password_hash = hash_password(body.new_password)
    audit(request, p, "auth.password_changed", resource_type="user", resource_id=p.id)


_ = (EmailStr, iso)
