"""Project-scoped API keys: lets a human (Admin/SRE) issue a narrow machine credential for an external
service - e.g. a team's own webapp pushing its logs in - without that service ever needing a user login.
Managing keys always requires a real user JWT (`project_perm`, not `project_perm_or_api_key`): a key must
never be usable to mint more keys."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import Principal, project_perm
from app.util import audit, iso
from shared.config.roles import API_KEY_SCOPES
from shared.models import ApiKey
from shared.models.base import utcnow
from shared.utils.apikeys import generate_key
from shared.utils.db import get_db

router = APIRouter(prefix="/projects/{project_id}/api-keys", tags=["api-keys"])


def _key(k: ApiKey) -> dict:
    return {"id": str(k.id), "name": k.name, "key_prefix": k.key_prefix, "scopes": k.scopes or [], "created_at": iso(k.created_at),
            "last_used_at": iso(k.last_used_at), "revoked_at": iso(k.revoked_at)}


@router.get("")
def list_keys(project_id: uuid.UUID, p: Principal = Depends(project_perm("api_keys.manage")), db: Session = Depends(get_db)):
    rows = db.execute(select(ApiKey).where(ApiKey.project_id == project_id).order_by(ApiKey.created_at.desc())).scalars().all()
    return [_key(k) for k in rows]


@router.get("/scopes")
def available_scopes(project_id: uuid.UUID, p: Principal = Depends(project_perm("api_keys.manage"))):
    """The fixed menu the create-key UI offers - never the caller's own full permission set, so a key
    can't inherit more than this regardless of who creates it."""
    return {"scopes": API_KEY_SCOPES}


class CreateKeyBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    scopes: list[str] = Field(min_length=1)


@router.post("", status_code=201)
def create_key(project_id: uuid.UUID, body: CreateKeyBody, request: Request, p: Principal = Depends(project_perm("api_keys.manage")), db: Session = Depends(get_db)):
    bad = [s for s in body.scopes if s not in API_KEY_SCOPES]
    if bad:
        raise HTTPException(422, f"unknown scope(s): {', '.join(bad)}. Valid scopes: {', '.join(API_KEY_SCOPES)}")
    raw, prefix, key_hash = generate_key()
    row = ApiKey(project_id=project_id, name=body.name.strip(), key_prefix=prefix, key_hash=key_hash, scopes=body.scopes, created_by=p.id)
    db.add(row)
    db.flush()
    audit(request, p, "api_key.create", resource_type="api_key", resource_id=row.id, project_id=project_id, details={"name": row.name, "scopes": row.scopes})
    # The raw key is returned exactly this once - it is never recoverable again, only its hash is stored.
    return {**_key(row), "key": raw}


@router.post("/{key_id}/revoke")
def revoke_key(project_id: uuid.UUID, key_id: uuid.UUID, request: Request, p: Principal = Depends(project_perm("api_keys.manage")), db: Session = Depends(get_db)):
    row = db.get(ApiKey, key_id)
    if not row or row.project_id != project_id:
        raise HTTPException(404, "API key not found")
    if row.revoked_at is None:
        row.revoked_at = utcnow()
        audit(request, p, "api_key.revoke", resource_type="api_key", resource_id=row.id, project_id=project_id, details={"name": row.name})
    return _key(row)
