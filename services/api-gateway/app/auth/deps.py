"""Authentication (JWT) and authorisation (RBAC + project scoping) for every gateway endpoint."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.config.roles import CROSS_PROJECT_ROLES, Role, has_permission
from shared.models import ApiKey, Project, User, UserProject
from shared.models.base import utcnow
from shared.utils.apikeys import verify_key
from shared.utils.db import get_db
from shared.utils.security import decode_token

bearer = HTTPBearer(auto_error=False)


@dataclass
class Principal:
    id: uuid.UUID
    org_id: uuid.UUID
    email: str
    name: str
    role: str
    guided_mode: bool
    ip: str | None = None
    # Set only for an API-key-authenticated caller: its exact granted scopes replace role-based RBAC
    # entirely (see `can()`) rather than mapping to any real Role, since a key is never "a role", just a
    # fixed, narrow permission set someone explicitly typed in when they created it.
    api_key_scopes: list[str] | None = None

    @property
    def is_cross_project(self) -> bool:
        try:
            return Role(self.role) in CROSS_PROJECT_ROLES
        except ValueError:
            return False

    def can(self, permission: str) -> bool:
        if self.api_key_scopes is not None:
            return permission in self.api_key_scopes
        return has_permission(self.role, permission)

    def actor_headers(self) -> dict[str, str]:
        """Identity forwarded to internal services for authorisation-aware behaviour and audit."""
        return {"user_id": str(self.id), "org_id": str(self.org_id), "role": self.role, "email": self.email, "guided_mode": str(self.guided_mode)}


def _principal_from_token(token: str, db: Session, request: Request | None = None) -> Principal:
    try:
        claims = decode_token(token)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token expired", headers={"WWW-Authenticate": "Bearer"}) from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token", headers={"WWW-Authenticate": "Bearer"}) from exc
    user = db.get(User, uuid.UUID(claims["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account disabled or removed")
    return Principal(user.id, user.org_id, user.email, user.name, user.role, user.guided_mode, request.client.host if request and request.client else None)


def current_user(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)) -> Principal:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required", headers={"WWW-Authenticate": "Bearer"})
    p = _principal_from_token(creds.credentials, db, request)
    request.state.principal = p
    return p


def require(permission: str) -> Callable[..., Principal]:
    """Dependency factory: RBAC on every endpoint."""

    def dep(p: Principal = Depends(current_user)) -> Principal:
        if not p.can(permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"your role ({p.role}) does not have the '{permission}' permission")
        return p

    dep.__name__ = f"require_{permission.replace('.', '_')}"
    return dep


def accessible_project_ids(db: Session, p: Principal) -> list[uuid.UUID]:
    if p.is_cross_project:
        return [i for (i,) in db.execute(select(Project.id).where(Project.org_id == p.org_id))]
    return [i for (i,) in db.execute(select(UserProject.project_id).join(Project, Project.id == UserProject.project_id)
                                     .where(UserProject.user_id == p.id, Project.org_id == p.org_id))]


def project_access(project_id: uuid.UUID, p: Principal, db: Session) -> Project:
    """Project-level access: Admin/SRE across the org; everyone else only assigned projects."""
    proj = db.get(Project, project_id)
    if proj is None or proj.org_id != p.org_id:
        raise HTTPException(404, "project not found")
    if not p.is_cross_project and project_id not in set(accessible_project_ids(db, p)):
        raise HTTPException(403, "you do not have access to this project")
    return proj


def can_access_project(db: Session, p: Principal, project_id: uuid.UUID) -> bool:
    try:
        project_access(project_id, p, db)
        return True
    except HTTPException:
        return False


def project_perm(permission: str) -> Callable[..., Principal]:
    """RBAC *and* project-level access in one dependency. The route must have a `{project_id}` path param."""
    perm_dep = require(permission)

    def dep(project_id: uuid.UUID, p: Principal = Depends(perm_dep), db: Session = Depends(get_db)) -> Principal:
        project_access(project_id, p, db)
        return p

    dep.__name__ = f"project_{permission.replace('.', '_')}"
    return dep


def _principal_from_api_key(raw_key: str, project_id: uuid.UUID, permission: str, db: Session, request: Request | None) -> Principal:
    prefix = raw_key[:16]
    row = db.execute(
        select(ApiKey).where(ApiKey.key_prefix == prefix, ApiKey.project_id == project_id, ApiKey.revoked_at.is_(None))
    ).scalar_one_or_none()
    if row is None or not verify_key(raw_key, row.key_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or revoked API key")
    if permission not in (row.scopes or []):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"this API key is not scoped for '{permission}'")
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    row.last_used_at = utcnow()
    db.commit()
    return Principal(
        id=row.id, org_id=project.org_id, email=f"apikey:{row.id}", name=f'API key "{row.name}"', role="api_key",
        guided_mode=False, ip=request.client.host if request and request.client else None, api_key_scopes=row.scopes or [],
    )


def project_perm_or_api_key(permission: str) -> Callable[..., Principal]:
    """Same access grant as `project_perm`, but also accepts an `X-API-Key` header in place of a user JWT -
    for the handful of endpoints an external service might call directly (log ingestion, and a few
    read-only status checks), scoped to exactly the permissions that key was issued with. A key can never
    grant more than `shared.config.roles.API_KEY_SCOPES` regardless of who created it or what their own
    role can do."""

    def dep(project_id: uuid.UUID, request: Request, x_api_key: str | None = Header(default=None),
            authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> Principal:
        if x_api_key:
            return _principal_from_api_key(x_api_key, project_id, permission, db, request)
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "provide a bearer token or an X-API-Key header", headers={"WWW-Authenticate": "Bearer"})
        p = _principal_from_token(authorization.split(" ", 1)[1].strip(), db, request)
        if not p.can(permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"your role ({p.role}) does not have the '{permission}' permission")
        project_access(project_id, p, db)
        return p

    dep.__name__ = f"project_or_key_{permission.replace('.', '_')}"
    return dep
