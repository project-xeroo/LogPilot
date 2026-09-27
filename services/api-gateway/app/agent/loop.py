"""The perceive-reason-act chat loop (PRD 8.1).

  Perceive  read the question, the project's monitored services and the agent's current state
  Reason    the model itself decides which tools (if any) it needs, one step at a time - see
            `app.agent.planner` - rather than a fixed rule mapping a question to one tool
  Act       run whatever it asks for through the tool router (RBAC, autonomy policy, audit), then ask
            the AI service to compose a grounded answer (retrieval + tool results -> fast model)

Chat is the conversational front-end to *every* agent tool in Section 5.

`prepare()` is deliberately the only fast, synchronous part: it persists the user's message and an empty
"thinking" placeholder for the agent's reply and returns immediately. Planning + composition can now take
minutes (quality over latency - the model reasons with a slow, careful deep model and may chain several
tool calls), so everything from `plan()` onward is meant to run in a background task with its own DB
session, driven by `run_in_background()` - not held open on the original request/socket, which a tab
switch, refresh or network blip would otherwise sever. The placeholder row is the single source of truth
a client resumes from: whatever status/steps/content it holds is exactly what a fresh page load would see.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import planner
from app.agent.tool_router import TOOL_DEFS
from app.auth import Principal
from shared.config.tools import TOOLS_BY_NAME
from shared.models import ChatMessage, ChatThread, Project
from shared.models.base import utcnow
from shared.utils import clients
from shared.utils.audit import record_agent_action
from shared.utils.autonomy import resolve_policy
from shared.utils.clients import ServiceError
from shared.utils.db import SessionLocal
from shared.utils.events import add_feed_item

log = logging.getLogger("logpilot.agent")


def _agent_info(p: Principal) -> dict[str, Any]:
    """Static, always-present grounding for meta questions ("what can you do", "who are you"): the model is
    told never to invent things it wasn't given, so without this, a question about the agent itself - which no
    log-search or risk tool can answer - would get an empty context and a useless non-answer."""
    return {
        "summary": "LogPilot is an autonomous reliability agent: it watches your services' logs continuously, "
                   "forecasts failures before they happen, and answers every question grounded in your real log "
                   "data - it never invents information it does not have.",
        "tools_available_to_you": [
            {"name": n, "title": TOOLS_BY_NAME[n].title, "does": TOOLS_BY_NAME[n].description}
            for n, d in TOOL_DEFS.items() if p.can(d.permission)
        ],
        "autonomy_model": "Every tool is either read-only, runs automatically in the background, or proposes an "
                          "action for a human to approve first - nothing irreversible happens without someone "
                          "clicking Approve.",
    }


def _on_step(db: Session, agent_message_id: uuid.UUID) -> Any:
    """Appends a human-readable progress line to the placeholder message and commits immediately, so a
    concurrent poll of the same row sees it right away - this is the "thinking..." trail a client renders."""

    def notify(text: str) -> None:
        row = db.get(ChatMessage, agent_message_id)
        if row is None:
            return
        row.steps = [*(row.steps or []), text]
        db.commit()

    return notify


def _plan_and_run(db: Session, p: Principal, project_id: uuid.UUID, text: str, history: list[dict[str, str]], on_step) -> tuple[str, dict[str, Any], list[dict], list[dict]]:
    """Returns (intent_label, tool_results, cards, tool_calls). The model decides which tools to call - zero,
    one, or several, in whatever order the question needs (`planner.run`) - instead of a fixed rule mapping a
    question to a single tool, so the agent can chain evidence-gathering steps the way an on-call engineer
    would. `intent_label` is now just a summary of what actually ran, kept for audit/labelling; it no longer
    drives the decision. Tool failures degrade to notes, never crash the chat."""
    results, cards, calls = planner.run(db, p, project_id, text, history, on_step)
    results["agent_info"] = _agent_info(p)
    intent = ", ".join(dict.fromkeys(c["tool"] for c in calls if c.get("ok"))) or "conversation"
    return intent, results, cards, calls


def _history(db: Session, thread_id: uuid.UUID) -> list[dict[str, str]]:
    rows = db.execute(select(ChatMessage).where(ChatMessage.thread_id == thread_id).order_by(ChatMessage.created_at.desc()).limit(6)).scalars().all()
    return [{"role": "user" if m.role == "user" else "assistant", "content": m.content[:1500]} for m in reversed(rows)]


def get_thread(db: Session, p: Principal, project_id: uuid.UUID, thread_id: uuid.UUID | None, first_message: str) -> ChatThread:
    if thread_id:
        t = db.get(ChatThread, thread_id)
        if not t or t.user_id != p.id or t.project_id != project_id:
            raise HTTPException(404, "conversation not found")
        return t
    t = ChatThread(project_id=project_id, user_id=p.id, title=first_message.strip()[:80] or "New conversation")
    db.add(t)
    db.flush()
    return t


def prepare(db: Session, p: Principal, project_id: uuid.UUID, thread_id: uuid.UUID | None, text: str) -> dict[str, Any]:
    """Fast path only: persist the user turn and an empty "thinking" placeholder for the agent's reply, then
    return immediately. Callers hand the rest (`plan` -> compose -> `finish`) to a background task."""
    policy = resolve_policy(db, p.org_id, "conversational_chat", db.get(Project, project_id).environment)
    if not policy["enabled"]:
        raise HTTPException(403, "Chat has been disabled by your organization's autonomy policy")
    thread = get_thread(db, p, project_id, thread_id, text)
    hist = _history(db, thread.id)
    db.add(ChatMessage(thread_id=thread.id, role="user", content=text))
    agent_msg = ChatMessage(thread_id=thread.id, role="agent", content="", status="thinking", steps=[])
    db.add(agent_msg)
    db.flush()
    return {"thread": thread, "history": hist, "agent_message_id": agent_msg.id, "t0": time.monotonic()}


def plan(db: Session, p: Principal, project_id: uuid.UUID, text: str, prep: dict[str, Any]) -> dict[str, Any]:
    """Runs the tool-calling loop, streaming progress into the placeholder row as it goes. Returns `prep`
    extended with everything `ai_payload`/`finish` need."""
    intent, results, cards, calls = _plan_and_run(db, p, project_id, text, prep["history"], _on_step(db, prep["agent_message_id"]))
    return {**prep, "intent": intent, "tool_results": results, "cards": cards, "tool_calls": calls}


def ai_payload(project_id: uuid.UUID, text: str, prep: dict[str, Any], guided: bool) -> dict[str, Any]:
    return {"project_id": str(project_id), "question": text, "guided_mode": guided, "intent": prep["intent"], "history": prep["history"],
            "tool_results": _slim(prep["tool_results"])}


def _slim(tr: dict[str, Any]) -> dict[str, Any]:
    """Keep model context bounded: drop bulky fields that the answer does not need."""
    out = dict(tr)
    h = out.get("health")
    if h:
        out["health"] = {**h, "error_timeline": []}
    r = out.get("risk")
    if r and r.get("detail"):
        d = dict(r["detail"])
        d["signals"] = {k: ({kk: vv for kk, vv in v.items() if kk != "series"} if isinstance(v, dict) else v) for k, v in (d.get("signals") or {}).items() if k in ("velocity", "baseline", "similarity")}
        d.pop("alert", None)
        out["risk"] = {**r, "detail": d}
    d = out.get("deployment")
    if d:
        out["deployment"] = {**d, "from": {k: v for k, v in d["from"].items() if k != "error_templates"}, "to": {k: v for k, v in d["to"].items() if k != "error_templates"}}
    rc = out.get("rca")
    if rc:
        out["rca"] = {k: rc.get(k) for k in ("causal_chain", "confidence", "explanation")}
    return out


def finish(db: Session, p: Principal, project_id: uuid.UUID, prep: dict[str, Any], text: str, ans: dict[str, Any]) -> dict[str, Any]:
    """Phase 3: fill in the placeholder agent message, audit the chat tool, and add the answered question to
    the Agent Feed. Updates the row `prepare()` already created rather than inserting a new one, so a client
    that has been polling that same message id the whole time sees it flip straight from thinking to done."""
    thread: ChatThread = prep["thread"]
    ms = int((time.monotonic() - prep["t0"]) * 1000)
    msg = db.get(ChatMessage, prep["agent_message_id"])
    msg.content, msg.sources, msg.cards, msg.tool_calls = ans["answer"], ans.get("sources"), prep["cards"], prep["tool_calls"]
    msg.confidence, msg.confidence_label = ans.get("confidence"), ans.get("confidence_label")
    msg.latency_ms, msg.suggested_followups, msg.status = ms, ans.get("followups"), "done"
    thread.updated_at = utcnow()
    db.flush()
    org = db.get(Project, project_id).org_id
    record_agent_action(db, tool_name="conversational_chat", trigger="user_request", autonomy_level="read_only", org_id=org, project_id=project_id, actor_id=p.id,
                        confidence=ans.get("confidence"), status="executed", input_ref=f"thread:{thread.id}", output_ref=f"chat_message:{msg.id}",
                        summary=f"Answered {p.name}: {text[:100]}", details={"intent": prep["intent"], "latency_ms": ms, "degraded": ans.get("degraded", False)})
    add_feed_item(db, project_id=project_id, org_id=org, kind="answer", severity="info", ref_type="chat_thread", ref_id=thread.id, title=text[:200],
                  body=ans["answer"][:400], meta={"asked_by": p.name, "confidence": ans.get("confidence_label"), "message_id": str(msg.id)})
    return {"thread_id": str(thread.id), "message_id": str(msg.id), "answer": ans["answer"], "sources": ans.get("sources", []), "memory": ans.get("memory", []),
            "cards": prep["cards"], "tool_calls": prep["tool_calls"], "confidence": ans.get("confidence"), "confidence_label": ans.get("confidence_label"),
            "followups": ans.get("followups", []), "degraded": ans.get("degraded", False), "intent": prep["intent"], "latency_ms": ms, "status": "done"}


def _fail(agent_message_id: uuid.UUID, message: str = "Something went wrong while answering. Please try again.") -> None:
    db = SessionLocal()
    try:
        row = db.get(ChatMessage, agent_message_id)
        if row:
            row.status, row.content = "error", message
            db.commit()
    except Exception:
        db.rollback()
        log.exception("could not mark chat message %s as failed", agent_message_id)
    finally:
        db.close()


def run_in_background(p: Principal, project_id: uuid.UUID, thread_id: uuid.UUID, agent_message_id: uuid.UUID,
                      text: str, guided: bool, history: list[dict[str, str]]) -> None:
    """Entry point for background-task execution (FastAPI `BackgroundTasks` or the WebSocket handler's
    detached task): opens its own DB session, since the original request's session is long gone by the time
    this runs, and drives planning -> composition -> finish. Runs to completion on the server regardless of
    what the client's connection does afterward - the placeholder row `prepare()` already created is what a
    client resumes from, by rereading the thread."""
    db = SessionLocal()
    try:
        thread = db.get(ChatThread, thread_id)
        if thread is None:
            return
        prep = {"thread": thread, "history": history, "agent_message_id": agent_message_id, "t0": time.monotonic()}
        prep = plan(db, p, project_id, text, prep)
        db.commit()
        try:
            # Composition runs the deep model for quality; it can legitimately take 30-60s+, and ai-service's
            # own retry-on-timeout loop can multiply one slow attempt by up to 3x, so this needs real headroom.
            ans = clients.ai.post("/chat/answer", json=ai_payload(project_id, text, prep, guided), actor=p.actor_headers(), timeout=300)
        except ServiceError as exc:
            ans = {"answer": ("I couldn't reach the reasoning model just now, but here is what my tools found - see the cards below."
                              if prep["cards"] else "I couldn't reach the reasoning model just now. Please try again in a moment."),
                   "sources": [], "confidence": 0.3, "confidence_label": "low", "followups": [], "degraded": True}
            log.warning("chat answer failed: %s", exc)
        finish(db, p, project_id, prep, text, ans)
        db.commit()
    except Exception:
        log.exception("background chat run failed")
        db.rollback()
        db.close()
        _fail(agent_message_id)
        return
    finally:
        db.close()


def handle_message(db: Session, p: Principal, project_id: uuid.UUID, thread_id: uuid.UUID | None, text: str, guided: bool) -> dict[str, Any]:
    """Fully synchronous convenience wrapper (prepare + plan + compose + finish, all inline) - used only where
    a caller genuinely wants to block for the whole answer (e.g. tests, scripts). The REST route and the
    WebSocket handler both call `prepare()` directly and hand the rest to `run_in_background` instead, so the
    client gets an immediate ack and the work survives a dropped connection."""
    prep = prepare(db, p, project_id, thread_id, text)
    db.commit()
    prep = plan(db, p, project_id, text, prep)
    db.commit()
    try:
        ans = clients.ai.post("/chat/answer", json=ai_payload(project_id, text, prep, guided), actor=p.actor_headers(), timeout=300)
    except ServiceError as exc:
        ans = {"answer": ("I couldn't reach the reasoning model just now, but here is what my tools found - see the cards below."
                          if prep["cards"] else "I couldn't reach the reasoning model just now. Please try again in a moment."),
               "sources": [], "confidence": 0.3, "confidence_label": "low", "followups": [], "degraded": True}
        log.warning("chat answer failed: %s", exc)
    return finish(db, p, project_id, prep, text, ans)
