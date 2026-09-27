"""Agent Feed (home): chronological stream of everything the agent has noticed, said or drafted."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import Principal, project_perm
from app.util import iso
from shared.models import FeedItem
from shared.utils.db import get_db

router = APIRouter(prefix="/projects/{project_id}/feed", tags=["feed"])


@router.get("")
def feed(project_id: uuid.UUID, limit: int = Query(40, ge=1, le=200), before: datetime | None = None, kinds: str | None = None,
         p: Principal = Depends(project_perm("feed.view")), db: Session = Depends(get_db)):
    q = select(FeedItem).where(FeedItem.project_id == project_id)
    if before:
        q = q.where(FeedItem.created_at < before)
    if kinds:
        q = q.where(FeedItem.kind.in_([k.strip() for k in kinds.split(",")]))
    if p.role == "viewer":
        q = q.where(FeedItem.kind != "answer")  # other people's chat answers are not shown to read-only viewers
    rows = db.execute(q.order_by(FeedItem.created_at.desc()).limit(limit)).scalars().all()
    return {"items": [{"id": str(i.id), "kind": i.kind, "title": i.title, "body": i.body, "severity": i.severity, "ref_type": i.ref_type, "ref_id": i.ref_id,
                       "service": i.service, "created_at": iso(i.created_at), "meta": i.meta} for i in rows],
            "next_before": iso(rows[-1].created_at) if len(rows) == limit else None}
