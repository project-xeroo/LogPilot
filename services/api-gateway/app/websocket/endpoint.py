"""/ws: real-time updates + a best-effort live view of agent chat replies.

Client -> server messages
  {"type":"ping"}
  {"type":"focus","project_id":"..."}                                   only receive events for this project
  {"type":"chat","project_id":"...","thread_id":"...|null","message":"..."}   ask the agent something
Server -> client: bus events ({"type":"feed.item"|"risk.updated"|"alert.created"|...}) and
  chat.start | chat.thread | chat.done | chat.error

The actual work (`loop.run_in_background`) is detached from this connection's lifecycle on purpose: it is
NOT one of the tasks cancelled when the socket closes, because a tab switch, refresh or network blip must
not kill an answer that is genuinely still being worked on. `chat.thread` carries the placeholder message id
the moment it exists, so a client can start polling `GET .../threads/{id}` immediately and get the rest of
the answer that way even if it never sees `chat.done` on this (or any) socket.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool

from app.agent import loop
from app.auth.deps import Principal, _principal_from_token, accessible_project_ids, can_access_project
from app.routers.chat import _msg
from app.websocket.manager import Conn, hub
from shared.models import ChatMessage
from shared.utils.db import SessionLocal

router = APIRouter()
log = logging.getLogger("logpilot.ws")
# Holds references only - asyncio.create_task() results must be kept alive or the task can be garbage
# collected mid-run (a standard asyncio gotcha); this set is never used to cancel anything.
_background: set[asyncio.Task] = set()


def _load_principal(token: str) -> tuple[Principal, list[str]]:
    db = SessionLocal()
    try:
        p = _principal_from_token(token, db)
        return p, [str(i) for i in accessible_project_ids(db, p)]
    finally:
        db.close()


def _prepare(p: Principal, project_id: uuid.UUID, thread_id: uuid.UUID | None, text: str) -> dict:
    db = SessionLocal()
    try:
        if not p.can("chat.use"):
            raise PermissionError("your role cannot use chat")
        if not can_access_project(db, p, project_id):
            raise PermissionError("no access to this project")
        prep = loop.prepare(db, p, project_id, thread_id, text)
        db.commit()
        return {"thread_id": prep["thread"].id, "agent_message_id": prep["agent_message_id"], "history": prep["history"]}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _load_message(message_id: uuid.UUID) -> dict | None:
    db = SessionLocal()
    try:
        m = db.get(ChatMessage, message_id)
        return _msg(m) if m else None
    finally:
        db.close()


async def _safe_send(conn: Conn, payload: dict) -> None:
    try:
        await conn.send(payload)
    except Exception:  # the socket that asked may already be gone - the background work keeps running regardless
        pass


async def _chat(conn: Conn, p: Principal, msg: dict) -> None:
    try:
        project_id = uuid.UUID(msg["project_id"])
        thread_id = uuid.UUID(msg["thread_id"]) if msg.get("thread_id") else None
        text = str(msg.get("message", "")).strip()[:4000]
        if not text:
            return
        await _safe_send(conn, {"type": "chat.start", "client_id": msg.get("client_id")})
        ids = await run_in_threadpool(_prepare, p, project_id, thread_id, text)
        await _safe_send(conn, {"type": "chat.thread", "thread_id": str(ids["thread_id"]), "message_id": str(ids["agent_message_id"]), "client_id": msg.get("client_id")})
        # Deliberately not awaited-and-cancellable-with-this-connection: run_in_background opens its own DB
        # session and keeps going even if this socket closes a second from now.
        await run_in_threadpool(loop.run_in_background, p, project_id, ids["thread_id"], ids["agent_message_id"], text, p.guided_mode, ids["history"])
        final = await run_in_threadpool(_load_message, ids["agent_message_id"])
        if final:
            await _safe_send(conn, {"type": "chat.done", **final, "client_id": msg.get("client_id")})
    except PermissionError as exc:
        await _safe_send(conn, {"type": "chat.error", "message": str(exc)})
    except Exception as exc:  # noqa: BLE001
        detail = getattr(exc, "detail", None) or str(exc)
        log.exception("chat over websocket failed")
        await _safe_send(conn, {"type": "chat.error", "message": str(detail)[:300]})


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str = ""):
    try:
        p, projects = await run_in_threadpool(_load_principal, token)
    except Exception:
        await ws.close(code=4401)
        return
    await ws.accept()
    conn = Conn(ws=ws, user_id=p.id, org_id=p.org_id, role=p.role, cross_project=p.is_cross_project, projects=set(projects))
    hub.add(conn)
    await conn.send({"type": "hello", "user_id": str(p.id), "role": p.role, "guided_mode": p.guided_mode})
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = msg.get("type")
            if kind == "ping":
                await conn.send({"type": "pong"})
            elif kind == "focus":
                conn.project_filter = msg.get("project_id") or None
            elif kind == "chat":
                # Fire-and-forget on purpose: a chat run must survive this socket closing (refresh, tab
                # switch, network blip), so it is deliberately not tied to this connection's lifecycle.
                t = asyncio.create_task(_chat(conn, p, msg))
                _background.add(t)
                t.add_done_callback(_background.discard)
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove(conn)
