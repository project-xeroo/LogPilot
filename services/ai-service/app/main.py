"""AI Service: embeddings, semantic/keyword search, chat, RCA, reports, forecast explanations.

Every call that leaves for a cloud model goes through `GuardedProvider` (PII redaction + egress
allowlist). Internal endpoints require the internal service token; user identity is forwarded by the
API gateway in X-Actor-* headers.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app import chat as chat_mod
from app import reports as reports_mod
from app.config import SERVICE_NAME
from app.prompts import build
from app.providers import AIUnavailable, Message, ModelRole, get_provider, heuristic_json, run_json, run_text
from app.rca import analyze as run_rca
from app.search import Filters, SearchError, keyword_search, semantic_search
from shared.config import settings
from shared.models import IncidentReport
from shared.utils import vectorstore
from shared.utils.db import SessionLocal, db_healthy, get_db, init_db
from shared.utils.logsetup import setup_logging
from shared.utils.web import actor_from_request, require_internal

setup_logging(SERVICE_NAME)
log = logging.getLogger("logpilot.ai")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    try:
        vectorstore.ensure_collection(vectorstore.LOGS)
        vectorstore.ensure_collection(vectorstore.SIGNATURES)
    except Exception as exc:
        log.warning("vector store not ready at startup: %s", exc)
    get_provider()  # fail fast on bad provider config
    yield


app = FastAPI(title="LogPilot AI Service", version="2.0.0", lifespan=lifespan)
_dep = [Depends(require_internal)]


@app.get("/health")
def health():
    p = get_provider()
    return {"service": SERVICE_NAME, "status": "ok" if db_healthy() and vectorstore.healthy() else "degraded", "provider": p.name,
            "database": db_healthy(), "vector_store": vectorstore.healthy(), "egress_allowlist": sorted(settings.ai_egress_hosts)}


@app.get("/internal/usage", dependencies=_dep)
def usage():
    return get_provider().usage.snapshot()


# ---- embeddings ------------------------------------------------------------------------------------------------
class EmbedBody(BaseModel):
    texts: list[str] = Field(max_length=512)


@app.post("/internal/embeddings", dependencies=_dep)
def embeddings(body: EmbedBody):
    t0 = time.monotonic()
    try:
        vecs = get_provider().embed(body.texts)
    except AIUnavailable as exc:
        raise HTTPException(503, f"embedding provider unavailable: {exc}") from exc
    return {"embeddings": vecs, "count": len(vecs), "took_ms": int((time.monotonic() - t0) * 1000)}


# ---- small fast-model utilities used by the pipeline -----------------------------------------------------------------
class LabelBody(BaseModel):
    templates: list[str]
    services: list[str] = []


@app.post("/internal/label", dependencies=_dep)
def label(body: LabelBody):
    ctx = {"templates": body.templates[:8], "services": body.services[:6]}
    try:
        out = run_text("cluster_label", build("cluster_label", ctx), ModelRole.FAST, ctx, timeout=15, max_tokens=30)
    except (AIUnavailable, Exception) as exc:  # noqa: BLE001
        raise HTTPException(503, str(exc)) from exc
    return {"label": out.strip().strip('"')[:120]}


class AnomalyBody(BaseModel):
    kind: str
    service: str
    explanation: str
    observed: float | None = None
    expected: float | None = None
    evidence: dict = {}


@app.post("/internal/explain-anomaly", dependencies=_dep)
def explain_anomaly(body: AnomalyBody):
    ctx = body.model_dump()
    try:
        out = run_text("anomaly_explanation", build("anomaly_explanation", ctx), ModelRole.FAST, ctx, timeout=15, max_tokens=200)
    except (AIUnavailable, Exception) as exc:  # noqa: BLE001
        raise HTTPException(503, str(exc)) from exc
    return {"explanation": out.strip()}


class NarrateBody(BaseModel):
    comparison: dict
    summary: str = ""


@app.post("/internal/narrate-deployment", dependencies=_dep)
def narrate(body: NarrateBody):
    ctx = body.model_dump()
    try:
        out = run_text("narrate_deployment", build("narrate_deployment", ctx), ModelRole.FAST, ctx, timeout=15, max_tokens=250)
    except (AIUnavailable, Exception) as exc:  # noqa: BLE001
        raise HTTPException(503, str(exc)) from exc
    return {"narrative": out.strip()}


# ---- forecasting: risk explanation + recommended actions (fast model) ---------------------------------------------------
@app.post("/internal/forecast/explain", dependencies=_dep)
def forecast_explain(ctx: dict[str, Any]):
    """Pre-incident explanation. The forecasting loop calls this only above threshold; on any failure it
    falls back to threshold-based text, so this endpoint reports 503 when the model is unavailable.
    FAST, not DEEP: with a real-time forecasting cadence (as low as 5s/service), a service sitting above
    threshold would otherwise queue a new 30-90s deep-model call before the previous one even finishes."""
    try:
        data = run_json("risk_explanation", build("risk_explanation", ctx), ModelRole.FAST, ctx, timeout=15.0, max_tokens=1500)
    except (AIUnavailable, Exception) as exc:  # noqa: BLE001
        raise HTTPException(503, f"reasoning model unavailable: {exc}") from exc
    if not isinstance(data.get("recommended_actions"), list) or not data.get("alert_text"):
        data = heuristic_json("risk_explanation", ctx)
    return {**data, "provider": get_provider().name}


# ---- search ---------------------------------------------------------------------------------------------------------------
class SearchBody(BaseModel):
    project_id: uuid.UUID
    query: str = ""
    mode: str = "keyword"  # keyword | semantic
    regex: bool = False
    exact: bool = False
    start: datetime | None = None
    end: datetime | None = None
    severity: list[str] = []
    min_severity: str | None = None
    services: list[str] = []
    environment: str | None = None
    deployment_version: str | None = None
    trace_id: str | None = None
    request_id: str | None = None
    limit: int = 50
    offset: int = 0
    context_window: int = 2

    def filters(self) -> Filters:
        return Filters(start=self.start, end=self.end, severity=self.severity, min_severity=self.min_severity, services=self.services,
                       environment=self.environment, deployment_version=self.deployment_version, trace_id=self.trace_id, request_id=self.request_id)


@app.post("/search", dependencies=_dep)
def search(body: SearchBody, db=Depends(get_db)):
    try:
        if body.mode == "semantic":
            return semantic_search(db, body.project_id, body.query, filters=body.filters(), limit=min(body.limit, 100), context_window=body.context_window)
        return keyword_search(db, body.project_id, body.query, regex=body.regex, exact=body.exact, filters=body.filters(), limit=body.limit,
                              offset=body.offset, context_window=body.context_window)
    except SearchError as exc:
        raise HTTPException(400, str(exc)) from exc
    except AIUnavailable as exc:
        raise HTTPException(503, f"semantic search unavailable (embedding provider down): {exc}. Keyword search still works.") from exc


# ---- chat -----------------------------------------------------------------------------------------------------------------
class ChatBody(BaseModel):
    project_id: uuid.UUID
    question: str = Field(min_length=1, max_length=4000)
    guided_mode: bool = False
    intent: str | None = None
    tool_results: dict[str, Any] = {}
    history: list[dict[str, str]] = []
    start: datetime | None = None
    end: datetime | None = None
    services: list[str] = []


def _chat_filters(b: ChatBody) -> Filters:
    return Filters(start=b.start, end=b.end, services=b.services)


class AgentStepMessage(BaseModel):
    role: str
    content: str | None = None
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class AgentStepBody(BaseModel):
    messages: list[AgentStepMessage]
    tools: list[dict] = Field(default_factory=list)


@app.post("/internal/agent/step", dependencies=_dep)
def agent_step(body: AgentStepBody):
    """One turn of the agent's own reasoning loop (PRD 8.1 "Reason"): given the running transcript and the
    tool catalogue it may use, the model itself decides whether it needs another tool call or is ready to
    answer - there is no fixed rule mapping a question to a tool. The API gateway owns the loop (and RBAC/
    autonomy/audit for whichever tools get called, via the same ToolRouter every other path uses); this
    endpoint is the stateless "ask the model what's next" step, behind the same redaction + egress gate as
    every other cloud call."""
    msgs = [Message(m.role, m.content, tool_calls=m.tool_calls, tool_call_id=m.tool_call_id, name=m.name) for m in body.messages]
    try:
        # DEEP, not FAST: which tools to call (and in what order) is the highest-leverage decision in the
        # whole chat loop - a wrong or lazy choice here means a wrong answer no matter how good the final
        # composition step is. The deep model is much slower (real-world: tens of seconds) but reasons about
        # tool selection far more carefully, and latency is an accepted tradeoff for that.
        return get_provider().complete_with_tools(task="agent_step", messages=msgs, role=ModelRole.DEEP, tools=body.tools, timeout=90.0)
    except AIUnavailable as exc:
        log.warning("agent planning step unavailable (%s); answering from whatever tool results exist so far", exc)
        return {"tool_calls": [], "content": ""}


@app.post("/chat/answer", dependencies=_dep)
def chat_answer(body: ChatBody, db=Depends(get_db)):
    return chat_mod.answer(db, body.project_id, body.question, guided_mode=body.guided_mode, tool_results=body.tool_results, intent=body.intent,
                           history=body.history, filters=_chat_filters(body))


@app.post("/chat/stream", dependencies=_dep)
def chat_stream(body: ChatBody):
    """Server-sent events: `sources`, many `delta`, then `done`."""

    def gen():
        db = SessionLocal()
        try:
            for ev in chat_mod.stream_answer(db, body.project_id, body.question, guided_mode=body.guided_mode, tool_results=body.tool_results,
                                             intent=body.intent, history=body.history, filters=_chat_filters(body)):
                yield f"data: {json.dumps(ev, default=str)}\n\n"
        except Exception as exc:  # noqa: BLE001
            log.exception("chat stream failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        finally:
            db.close()

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class ExplainBody(BaseModel):
    project_id: uuid.UUID
    message: str
    service: str | None = None
    severity: str | None = None


@app.post("/chat/explain-log", dependencies=_dep)
def explain_log(body: ExplainBody, db=Depends(get_db)):
    return chat_mod.explain_log(db, body.project_id, body.message, service=body.service, severity=body.severity)


@app.get("/chat/suggestions", dependencies=_dep)
def suggestions(project_id: uuid.UUID, n: int = 6, db=Depends(get_db)):
    return {"prompts": chat_mod.suggested_prompts(db, project_id, n)}


# ---- root cause analysis ------------------------------------------------------------------------------------------------------
class RcaBody(BaseModel):
    project_id: uuid.UUID
    service: str | None = None
    cluster_id: uuid.UUID | None = None
    start: datetime | None = None
    end: datetime | None = None
    trigger: str = "on_request"


@app.post("/rca", dependencies=_dep)
def rca(body: RcaBody, request: Request, db=Depends(get_db)):
    actor = actor_from_request(request)
    return run_rca(db, body.project_id, service=body.service, cluster_id=body.cluster_id, start=body.start, end=body.end, trigger=body.trigger,
                   actor_id=actor.user_id)


# ---- reports --------------------------------------------------------------------------------------------------------------------
class IncidentReportBody(BaseModel):
    project_id: uuid.UUID
    service: str | None = None
    cluster_id: uuid.UUID | None = None
    rca_id: uuid.UUID | None = None
    alert_id: uuid.UUID | None = None
    start: datetime | None = None
    end: datetime | None = None
    trigger: str = "user_request"


@app.post("/reports/incident", dependencies=_dep)
def report_incident(body: IncidentReportBody, request: Request, db=Depends(get_db)):
    actor = actor_from_request(request)
    return reports_mod.generate_incident_report(db, body.project_id, service=body.service, cluster_id=body.cluster_id, rca_id=body.rca_id,
                                                alert_id=body.alert_id, start=body.start, end=body.end, requested_by=actor.user_id, trigger=body.trigger)


class PreMortemBody(BaseModel):
    alert_id: uuid.UUID
    trigger: str = "autonomous"


@app.post("/reports/pre-mortem", dependencies=_dep)
def report_pre_mortem(body: PreMortemBody, request: Request, db=Depends(get_db)):
    actor = actor_from_request(request)
    try:
        return reports_mod.generate_pre_mortem(db, body.alert_id, requested_by=actor.user_id, trigger=body.trigger)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


class ExecBody(BaseModel):
    project_id: uuid.UUID
    days: int = Field(default=7, ge=1, le=90)


@app.post("/reports/executive-summary", dependencies=_dep)
def report_exec(body: ExecBody, request: Request, db=Depends(get_db)):
    actor = actor_from_request(request)
    return reports_mod.generate_executive_summary(db, body.project_id, days=body.days, requested_by=actor.user_id)


def _report_dict(r: IncidentReport) -> dict:
    return {"id": str(r.id), "title": r.title, "kind": r.kind, "status": r.status, "created_at": r.created_at.isoformat(),
            "sections": (r.sections_json or {}).get("sections", [])}


@app.get("/reports/{report_id}/export", dependencies=_dep)
def export_report(report_id: uuid.UUID, format: str = "markdown", db=Depends(get_db)):
    r = db.get(IncidentReport, report_id)
    if not r:
        raise HTTPException(404, "report not found")
    d = _report_dict(r)
    fmt = format.lower()
    if fmt in ("md", "markdown"):
        r.export_format = "markdown"
        return Response(reports_mod.to_markdown(d), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{_slug(r.title)}.md"'})
    if fmt == "pdf":
        r.export_format = "pdf"
        return Response(reports_mod.to_pdf(d), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{_slug(r.title)}.pdf"'})
    raise HTTPException(400, "format must be 'pdf' or 'markdown'")


def _slug(title: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")[:80] or "report"
