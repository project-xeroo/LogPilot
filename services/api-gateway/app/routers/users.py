"""Settings - Roles & Onboarding: user management, role assignment, promoting Junior accounts."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import Principal, require
from app.util import audit, iso
from shared.config.roles import Role
from shared.models import Project, User, UserProject
from shared.models.base import utcnow
from shared.utils.db import get_db
from shared.utils.security import hash_password

router = APIRouter(prefix="/users", tags=["users"])


def _dict(u: User, projects: list[uuid.UUID]) -> dict:
    return {"id": str(u.id), "email": u.email, "name": u.name, "role": u.role, "guided_mode": u.guided_mode, "is_active": u.is_active,
            "created_at": iso(u.created_at), "last_login_at": iso(u.last_login_at), "promoted_at": iso(u.promoted_at), "project_ids": [str(x) for x in projects]}


def _members(db: Session, org_id: uuid.UUID) -> dict[uuid.UUID, list[uuid.UUID]]:
    out: dict[uuid.UUID, list[uuid.UUID]] = {}
    for uid, pid in db.execute(select(UserProject.user_id, UserProject.project_id).join(Project, Project.id == UserProject.project_id).where(Project.org_id == org_id)):
        out.setdefault(uid, []).append(pid)
    return out


@router.get("")
def list_users(p: Principal = Depends(require("users.promote")), db: Session = Depends(get_db)):
    m = _members(db, p.org_id)
    return [_dict(u, m.get(u.id, [])) for u in db.execute(select(User).where(User.org_id == p.org_id).order_by(User.created_at)).scalars()]


class CreateUser(BaseModel):
    email: str
    name: str = Field(min_length=1, max_length=200)
    role: Role
    password: str = Field(min_length=10, max_length=128)
    project_ids: list[uuid.UUID] = []


@router.post("", status_code=201)
def create_user(body: CreateUser, request: Request, p: Principal = Depends(require("users.manage")), db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if db.execute(select(User).where(User.email == email)).scalar_one_or_none():
        raise HTTPException(409, "a user with that email already exists")
    u = User(org_id=p.org_id, email=email, name=body.name, role=body.role.value, password_hash=hash_password(body.password),
             guided_mode=body.role == Role.JUNIOR)  # guided mode defaults ON for Junior, OFF otherwise
    db.add(u)
    db.flush()
    _set_projects(db, p, u, body.project_ids)
    audit(request, p, "user.create", resource_type="user", resource_id=u.id, details={"role": body.role.value, "email": email})
    return _dict(u, body.project_ids)


def _set_projects(db: Session, p: Principal, u: User, ids: list[uuid.UUID]) -> None:
    valid = {i for (i,) in db.execute(select(Project.id).where(Project.org_id == p.org_id, Project.id.in_(ids)))} if ids else set()
    for row in db.execute(select(UserProject).where(UserProject.user_id == u.id)).scalars():
        db.delete(row)
    db.flush()
    for pid in valid:
        db.add(UserProject(user_id=u.id, project_id=pid))


class UpdateUser(BaseModel):
    name: str | None = None
    role: Role | None = None
    is_active: bool | None = None
    guided_mode: bool | None = None
    project_ids: list[uuid.UUID] | None = None


@router.patch("/{user_id}")
def update_user(user_id: uuid.UUID, body: UpdateUser, request: Request, p: Principal = Depends(require("users.manage")), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if not u or u.org_id != p.org_id:
        raise HTTPException(404, "user not found")
    changes = {}
    if body.role is not None and body.role.value != u.role:
        if u.id == p.id:
            raise HTTPException(400, "you cannot change your own role")
        changes["role"] = (u.role, body.role.value)
        u.role = body.role.value
        u.guided_mode = body.role == Role.JUNIOR if body.guided_mode is None else body.guided_mode
    if body.is_active is not None and body.is_active != u.is_active:
        if u.id == p.id:
            raise HTTPException(400, "you cannot deactivate your own account")
        changes["is_active"] = body.is_active
        u.is_active = body.is_active
    if body.name:
        u.name = body.name
    if body.guided_mode is not None:
        u.guided_mode = body.guided_mode
    if body.project_ids is not None:
        _set_projects(db, p, u, body.project_ids)
        changes["projects"] = [str(x) for x in body.project_ids]
    audit(request, p, "user.update", resource_type="user", resource_id=u.id, details=changes)
    return _dict(u, [x for x in (_members(db, p.org_id).get(u.id, []))])


@router.post("/{user_id}/promote")
def promote(user_id: uuid.UUID, request: Request, p: Principal = Depends(require("users.promote")), db: Session = Depends(get_db)):
    """Promote a Junior Engineer out of guided mode: role -> Developer, guided mode off, approval rights granted."""
    u = db.get(User, user_id)
    if not u or u.org_id != p.org_id:
        raise HTTPException(404, "user not found")
    if u.role != Role.JUNIOR.value:
        raise HTTPException(409, "only Junior Engineer accounts can be promoted")
    u.role, u.guided_mode, u.promoted_at, u.promoted_by = Role.DEVELOPER.value, False, utcnow(), p.id
    audit(request, p, "user.promote", resource_type="user", resource_id=u.id, details={"from": "junior_engineer", "to": "developer"})
    return _dict(u, _members(db, p.org_id).get(u.id, []))
