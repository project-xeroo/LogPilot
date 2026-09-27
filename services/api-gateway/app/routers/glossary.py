"""Glossary panel: persistent, searchable glossary of log and reliability terms."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth import Principal, require
from app.util import audit
from shared.models import GlossaryTerm
from shared.utils.db import get_db

router = APIRouter(prefix="/glossary", tags=["glossary"])


def _t(t: GlossaryTerm) -> dict:
    return {"id": str(t.id), "term": t.term, "definition": t.definition, "category": t.category, "source": t.seeded_from}


@router.get("")
def search(q: str | None = None, category: str | None = None, p: Principal = Depends(require("glossary.view")), db: Session = Depends(get_db)):
    stmt = select(GlossaryTerm)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(GlossaryTerm.term.ilike(like), GlossaryTerm.definition.ilike(like)))
    if category:
        stmt = stmt.where(GlossaryTerm.category == category)
    return [_t(t) for t in db.execute(stmt.order_by(GlossaryTerm.term)).scalars()]


class TermBody(BaseModel):
    term: str = Field(min_length=1, max_length=120)
    definition: str = Field(min_length=3, max_length=1000)
    category: str = "reliability"


@router.post("", status_code=201)
def add(body: TermBody, request: Request, p: Principal = Depends(require("policy.edit")), db: Session = Depends(get_db)):
    if db.execute(select(GlossaryTerm).where(GlossaryTerm.term == body.term)).scalar_one_or_none():
        raise HTTPException(409, "term already exists")
    t = GlossaryTerm(term=body.term, definition=body.definition, category=body.category, seeded_from="user")
    db.add(t)
    db.flush()
    audit(request, p, "glossary.add", resource_type="glossary_term", resource_id=t.id, details={"term": body.term})
    return _t(t)
