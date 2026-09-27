"""Chat: the primary human interface to the agent (tool 09)."""
from __future__ import annotations

import logging
import uuid
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import loop
from app.auth import Principal, project_perm
from app.util import audit, iso, upstream
from shared.models import ChatMessage, ChatThread
from shared.utils import clients
from shared.utils.clients import ServiceError
from shared.utils.db import get_db

router = APIRouter(prefix="/projects/{project_id}/chat", tags=["chat"])
log = logging.getLogger("logpilot.chat.router")


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    thread_id: uuid.UUID | None = None


@router.post("")
def chat(project_id: uuid.UUID, body: ChatBody, request: Request, background: BackgroundTasks, p: Principal = Depends(project_perm("chat.use")), db: Session = Depends(get_db)):
    """Acks fast: persists the question and an empty "thinking" placeholder for the reply, then hands the
    actual planning/composition (which can genuinely take minutes - see `app.agent.loop`) to a background
    task. The client polls `GET .../threads/{thread_id}` and watches the placeholder message's `status` and
    `steps` update - that keeps working across a refresh or a dropped connection, since the work runs on the
    server independent of this specific request."""
    prep = loop.prepare(db, p, project_id, body.thread_id, body.message)
    thread_id, agent_message_id, history = prep["thread"].id, prep["agent_message_id"], prep["history"]
    db.commit()
    background.add_task(loop.run_in_background, p, project_id, thread_id, agent_message_id, body.message, p.guided_mode, history)
    audit(request, p, "chat.message", resource_type="chat_thread", resource_id=str(thread_id), project_id=project_id, details={"status": "thinking"})
    return {"thread_id": str(thread_id), "message_id": str(agent_message_id), "status": "thinking"}


def _msg(m: ChatMessage) -> dict:
    return {"id": str(m.id), "role": m.role, "content": m.content, "sources": m.sources, "cards": m.cards, "tool_calls": m.tool_calls, "confidence": m.confidence,
            "confidence_label": m.confidence_label, "rating": m.rating, "followups": m.suggested_followups, "latency_ms": m.latency_ms, "created_at": iso(m.created_at),
            "status": m.status, "steps": m.steps or []}


@router.get("/threads")
def threads(project_id: uuid.UUID, limit: int = 30, p: Principal = Depends(project_perm("chat.use")), db: Session = Depends(get_db)):
    rows = db.execute(select(ChatThread).where(ChatThread.project_id == project_id, ChatThread.user_id == p.id).order_by(ChatThread.updated_at.desc()).limit(min(limit, 100))).scalars()
    return [{"id": str(t.id), "title": t.title, "updated_at": iso(t.updated_at), "created_at": iso(t.created_at)} for t in rows]


@router.get("/threads/{thread_id}")
def thread(project_id: uuid.UUID, thread_id: uuid.UUID, p: Principal = Depends(project_perm("chat.use")), db: Session = Depends(get_db)):
    t = db.get(ChatThread, thread_id)
    if not t or t.user_id != p.id or t.project_id != project_id:
        raise HTTPException(404, "conversation not found")
    msgs = db.execute(select(ChatMessage).where(ChatMessage.thread_id == t.id).order_by(ChatMessage.created_at)).scalars()
    return {"id": str(t.id), "title": t.title, "messages": [_msg(m) for m in msgs]}


class RatingBody(BaseModel):
    rating: Literal[1, -1]


@router.post("/messages/{message_id}/rating")
def rate(project_id: uuid.UUID, message_id: uuid.UUID, body: RatingBody, request: Request, p: Principal = Depends(project_perm("chat.use")), db: Session = Depends(get_db)):
    """Thumbs rating on an agent answer (feeds the >80% helpful metric)."""
    m = db.get(ChatMessage, message_id)
    t = db.get(ChatThread, m.thread_id) if m else None
    if not m or not t or t.user_id != p.id or t.project_id != project_id or m.role != "agent":
        raise HTTPException(404, "message not found")
    m.rating = body.rating
    audit(request, p, "chat.rating", resource_type="chat_message", resource_id=m.id, project_id=project_id, details={"rating": body.rating})
    return {"rating": m.rating}


@router.get("/suggestions")
def suggestions(project_id: uuid.UUID, n: int = 6, p: Principal = Depends(project_perm("chat.use"))):
    """4-6 example questions relevant to what the agent is currently seeing (never a blank input box)."""
    try:
        return clients.ai.get("/chat/suggestions", params={"project_id": str(project_id), "n": n})
    except ServiceError:
        return {"prompts": ["How is everything doing right now?", "Which services are most likely to fail next?", "Show me the top errors from the last hour",
                            "Summarise what happened today"]}


class ExplainBody(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    service: str | None = None
    severity: str | None = None


@router.post("/explain")
def explain(project_id: uuid.UUID, body: ExplainBody, p: Principal = Depends(project_perm("chat.use"))):
    """'What does this error mean?' in plain language - powers the click-to-explain affordance on log lines."""
    try:
        return clients.ai.post("/chat/explain-log", json={"project_id": str(project_id), **body.model_dump(exclude_none=True)}, actor=p.actor_headers(), timeout=30)
    except ServiceError as exc:
        raise upstream(exc) from exc
