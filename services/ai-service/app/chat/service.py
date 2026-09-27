"""Conversational Chat Tool (tool 09): retrieval-grounded answers with sources and confidence.

question -> cloud embedding -> vector k-NN over log templates (+ the agent's incident memory)
         -> top-k context (redacted snippets) -> fast reasoning model -> answer with [n] citations.
The API gateway's tool router may also pass `tool_results` (health, risk, RCA, ...) which are
folded into the same grounded answer. Target < 8s.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.prompts import build
from app.providers import AIUnavailable, ModelRole, get_provider, run_text
from app.providers import composers
from app.search import Filters, SearchError, semantic_search
from shared.utils import vectorstore
from shared.utils.logtemplate import strip_metadata
from shared.utils.redaction import redact

log = logging.getLogger("logpilot.chat")
MAX_SOURCES = 6


def retrieve(db: Session, project_id: uuid.UUID, question: str, *, filters: Filters | None = None, k: int = MAX_SOURCES,
             min_score: float = 0.35) -> tuple[list[dict], list[dict]]:
    """Returns (sources, memory). One source per distinct message template, best-match first."""
    sources: list[dict] = []
    try:
        res = semantic_search(db, project_id, question, filters=filters, limit=k * 2, context_window=0, min_score=min_score)
    except SearchError:
        res = {"results": []}
    seen: set[str] = set()
    for r in res["results"]:
        key = r.get("template") or r["message"]
        if key in seen:
            continue
        seen.add(key)
        sources.append({"n": len(sources) + 1, "id": r["id"], "service": r["service"], "severity": r["severity"], "timestamp": r["timestamp"],
                        "message": redact(strip_metadata(r["message"], 400)), "count": r.get("occurrences"), "score": r.get("score", 0.0),
                        "trace_id": r.get("trace_id"), "source": r.get("source")})
        if len(sources) >= k:
            break
    memory: list[dict] = []
    try:
        vec = get_provider().embed([question])[0]
        for h in vectorstore.search(vectorstore.SIGNATURES, vec, limit=2, must={"project_id": str(project_id)}, score_threshold=0.55):
            p = h["payload"]
            memory.append({"label": p.get("label"), "when": p.get("incident_start"), "service": p.get("service_name"),
                           "resolved_actions": p.get("resolved_actions") or [], "similarity": round(h["score"], 3)})
    except Exception:  # memory is a bonus; never fail the answer over it
        pass
    return sources, memory


def confidence_of(sources: list[dict], tool_results: dict, memory: list[dict]) -> tuple[float, str]:
    has_tool = any(tool_results.get(k) for k in ("risk", "health", "rca", "deployment", "search", "explain"))
    tool_base = 0.72 if has_tool else 0.0
    src = 0.0
    if sources:
        top = max(s["score"] for s in sources)
        strong = sum(1 for s in sources if s["score"] >= 0.55)
        src = min(0.9, 0.5 * top + 0.1 * min(strong, 4) + 0.1)
    conf = max(tool_base, src)
    if memory:
        conf += 0.05
    if not sources and not has_tool:
        conf = 0.15
    conf = round(min(conf, 0.97), 2)
    return conf, ("high" if conf >= 0.75 else "medium" if conf >= 0.5 else "low")


def followups(question: str, tool_results: dict, sources: list[dict]) -> list[str]:
    out: list[str] = []
    risk = tool_results.get("risk") or {}
    if risk.get("focus"):
        out += [f"What should we do about {risk['focus']}?", f"Show the pre-mortem for {risk['focus']}"]
    elif risk.get("services"):
        top = max(risk["services"], key=lambda s: s.get("risk_score", 0))
        out += [f"Why is {top['service']} at risk?", f"Show recent errors in {top['service']}"]
    if tool_results.get("health"):
        top = (tool_results["health"].get("top_problematic_services") or [None])[0]
        if top:
            out += [f"Why is {top} failing?", "Which services are most likely to fail next?"]
    if tool_results.get("rca"):
        out += ["Generate an incident report for this", "Which deployments happened around then?"]
    if tool_results.get("deployment"):
        out += ["Show the new error types in this release"]
    if sources:
        out += [f"Explain: {sources[0]['message'][:70]}", f"Search for similar errors in {sources[0]['service']}"]
    return list(dict.fromkeys(out))[:4]


def _context(question: str, sources, memory, tool_results, guided: bool, intent: str | None, history: list[dict] | None) -> dict[str, Any]:
    return {"question": question, "intent": intent, "guided_mode": guided, "sources": sources, "memory": memory, "tool_results": tool_results,
            "history": (history or [])[-6:]}


def answer(db: Session, project_id: uuid.UUID, question: str, *, guided_mode: bool = False, tool_results: dict | None = None,
           intent: str | None = None, history: list[dict] | None = None, filters: Filters | None = None) -> dict[str, Any]:
    t0 = time.monotonic()
    tool_results = tool_results or {}
    k = 3 if tool_results else MAX_SOURCES
    sources, memory = retrieve(db, project_id, question, filters=filters, k=k, min_score=0.5 if tool_results else 0.35)
    ctx = _context(question, sources, memory, tool_results, guided_mode, intent, history)
    degraded = False
    try:
        # DEEP: composing the answer engineers actually act on is worth the extra seconds versus the fast
        # model - it's meaningfully better at synthesizing several tool results into one coherent, precise
        # answer rather than a superficial summary of each in turn.
        text_out = run_text("chat_answer", build("chat_answer", ctx), ModelRole.DEEP, ctx, timeout=70.0)
    except (AIUnavailable, Exception) as exc:  # noqa: BLE001 - degrade to a data-driven answer
        log.warning("fast model unavailable (%s); composing answer from retrieved evidence", exc)
        text_out, degraded = composers.chat_answer(ctx), True
    conf, label = confidence_of(sources, tool_results, memory)
    if degraded:
        conf, label = min(conf, 0.6), ("medium" if conf >= 0.5 else "low")
    return {"answer": text_out, "sources": sources, "memory": memory, "confidence": conf, "confidence_label": label,
            "followups": followups(question, tool_results, sources), "degraded": degraded, "latency_ms": int((time.monotonic() - t0) * 1000)}


def stream_answer(db: Session, project_id: uuid.UUID, question: str, *, guided_mode: bool = False, tool_results: dict | None = None,
                  intent: str | None = None, history: list[dict] | None = None, filters: Filters | None = None) -> Iterator[dict]:
    """Yields events: {'type':'sources',...} then many {'type':'delta','text'} then {'type':'done',...}."""
    t0 = time.monotonic()
    tool_results = tool_results or {}
    sources, memory = retrieve(db, project_id, question, filters=filters, k=3 if tool_results else MAX_SOURCES, min_score=0.5 if tool_results else 0.35)
    conf, label = confidence_of(sources, tool_results, memory)
    yield {"type": "sources", "sources": sources, "memory": memory, "confidence": conf, "confidence_label": label}
    ctx = _context(question, sources, memory, tool_results, guided_mode, intent, history)
    buf: list[str] = []
    degraded = False
    try:
        for piece in get_provider().stream(task="chat_answer", messages=build("chat_answer", ctx), role=ModelRole.DEEP, context=ctx, timeout=70.0):
            buf.append(piece)
            yield {"type": "delta", "text": piece}
    except (AIUnavailable, Exception) as exc:  # noqa: BLE001
        log.warning("stream failed (%s); falling back to composed answer", exc)
        degraded = True
        if not buf:
            for m in re.finditer(r"\S+\s*", composers.chat_answer(ctx)):
                buf.append(m.group(0))
                yield {"type": "delta", "text": m.group(0)}
    yield {"type": "done", "answer": "".join(buf), "sources": sources, "memory": memory, "confidence": conf, "confidence_label": label,
           "followups": followups(question, tool_results, sources), "degraded": degraded, "latency_ms": int((time.monotonic() - t0) * 1000)}


# ---- log explanation (junior engineers: "what does this error mean?") ---------------------------------------
def explain_log(db: Session, project_id: uuid.UUID, message: str, *, service: str | None = None, severity: str | None = None) -> dict[str, Any]:
    from app.providers import heuristic_json, run_json
    from shared.utils.logtemplate import template_of

    _, h = template_of(message)
    count = db.execute(text("SELECT occurrence_count FROM log_templates WHERE project_id = :p AND template_hash = :h"), {"p": project_id, "h": h}).scalar()
    ctx = {"message": redact(message)[:600], "service": service, "severity": severity, "count": count}
    try:
        data = run_json("log_explanation", build("log_explanation", ctx), ModelRole.FAST, ctx, timeout=15.0, max_tokens=800)
    except (AIUnavailable, Exception):  # noqa: BLE001
        data = heuristic_json("log_explanation", ctx)
    return {**data, "occurrences": count}



# ---- suggested prompts (guided mode: never an empty input box) -----------------------------------------------
def suggested_prompts(db: Session, project_id: uuid.UUID, n: int = 6) -> list[str]:
    out: list[str] = []
    flagged = db.execute(
        text(
            """
            SELECT DISTINCT ON (ms.name) ms.name, r.risk_score FROM monitored_services ms JOIN risk_snapshots r ON r.service_id = ms.id
            WHERE ms.project_id = :p ORDER BY ms.name, r.timestamp DESC
            """
        ),
        {"p": project_id},
    ).all()
    for name, score in sorted(flagged, key=lambda r: -r[1])[:2]:
        if score >= 60:
            out += [f"Why is {name} flagged?", f"What should I do about {name}?"]
    anom = db.execute(text("SELECT service, kind FROM anomalies WHERE project_id = :p ORDER BY detected_at DESC LIMIT 2"), {"p": project_id}).all()
    for svc, kind in anom:
        out.append(f"What caused the {kind.replace('_', ' ')} in {svc}?")
    cl = db.execute(text("SELECT label FROM error_clusters WHERE project_id = :p AND status = 'active' ORDER BY occurrence_count DESC LIMIT 1"), {"p": project_id}).scalar()
    if cl:
        out.append(f"Explain the '{cl}' errors in plain language")
    dep = db.execute(text("SELECT service FROM deployments WHERE project_id = :p ORDER BY deployed_at DESC LIMIT 1"), {"p": project_id}).scalar()
    if dep:
        out.append(f"Did the latest deployment of {dep} cause any regressions?")
    out += ["How is everything doing right now?", "Which services are most likely to fail next?", "Show me the top errors from the last hour",
            "Is this error rate normal?", "Summarise what happened today"]
    return list(dict.fromkeys(out))[: max(4, min(n, 6))]


_ = datetime
