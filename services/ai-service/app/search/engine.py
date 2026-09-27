"""Log Search Tool (tool 05): dual-mode search.

keyword   exact-text / full-text / regex matching, backed by a GIN full-text index and a trigram index
semantic  cloud-embedding similarity against the managed vector store, hydrated back to real records

Filters: time range, severity, service, environment, deployment version, trace ID (+ request ID).
Results carry source references (session, file, line) and a context window of neighbouring records.
Target: < 500ms keyword / < 1s semantic across 1M+ records.
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.providers import get_provider
from shared.models.base import SEVERITY_NUM
from shared.utils import vectorstore

MAX_REGEX_LEN = 500
COLS = "id, timestamp, service, severity, message, request_id, trace_id, environment, deployment_version, session_id, line_no, template_hash"


class SearchError(ValueError):
    pass


@dataclass
class Filters:
    start: datetime | None = None
    end: datetime | None = None
    severity: list[str] = field(default_factory=list)  # exact levels
    min_severity: str | None = None
    services: list[str] = field(default_factory=list)
    environment: str | None = None
    deployment_version: str | None = None
    trace_id: str | None = None
    request_id: str | None = None


def _where(f: Filters, params: dict[str, Any], alias: str = "") -> str:
    a = f"{alias}." if alias else ""
    clauses = []
    if f.start:
        clauses.append(f"{a}timestamp >= :f_start")
        params["f_start"] = f.start
    if f.end:
        clauses.append(f"{a}timestamp <= :f_end")
        params["f_end"] = f.end
    if f.severity:
        nums = [SEVERITY_NUM[s.upper()] for s in f.severity if s.upper() in SEVERITY_NUM]
        if nums:
            clauses.append(f"{a}severity_num = ANY(:f_sev)")
            params["f_sev"] = nums
    if f.min_severity and f.min_severity.upper() in SEVERITY_NUM:
        clauses.append(f"{a}severity_num >= :f_minsev")
        params["f_minsev"] = SEVERITY_NUM[f.min_severity.upper()]
    if f.services:
        clauses.append(f"{a}service = ANY(:f_svc)")
        params["f_svc"] = f.services
    if f.environment:
        clauses.append(f"{a}environment = :f_env")
        params["f_env"] = f.environment
    if f.deployment_version:
        clauses.append(f"{a}deployment_version = :f_ver")
        params["f_ver"] = f.deployment_version
    if f.trace_id:
        clauses.append(f"{a}trace_id = :f_trace")
        params["f_trace"] = f.trace_id
    if f.request_id:
        clauses.append(f"{a}request_id = :f_req")
        params["f_req"] = f.request_id
    return (" AND " + " AND ".join(clauses)) if clauses else ""


def _row(r) -> dict[str, Any]:
    return {
        "id": str(r.id), "timestamp": r.timestamp.isoformat(), "service": r.service, "severity": r.severity, "message": r.message,
        "request_id": r.request_id, "trace_id": r.trace_id, "environment": r.environment,
        "deployment_version": r.deployment_version,
        "source": {"session_id": str(r.session_id) if r.session_id else None, "line_no": r.line_no, "filename": None},
    }


def _attach_sources(db: Session, results: list[dict]) -> None:
    ids = {r["source"]["session_id"] for r in results if r["source"]["session_id"]}
    if not ids:
        return
    names = {str(i): n for i, n in db.execute(text("SELECT id, filename FROM log_sessions WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": list(ids)})}
    for r in results:
        r["source"]["filename"] = names.get(r["source"]["session_id"])


def _attach_context(db: Session, project_id: uuid.UUID, results: list[dict], window: int, limit: int = 10) -> None:
    if window <= 0:
        return
    for r in results[:limit]:
        rows = db.execute(
            text(
                f"""
                (SELECT {COLS} FROM log_records WHERE project_id = :p AND service = :svc AND timestamp < :t AND timestamp >= :t - interval '5 minutes'
                 ORDER BY timestamp DESC LIMIT :n)
                UNION ALL
                (SELECT {COLS} FROM log_records WHERE project_id = :p AND service = :svc AND timestamp > :t AND timestamp <= :t + interval '5 minutes'
                 ORDER BY timestamp ASC LIMIT :n)
                """
            ),
            {"p": project_id, "svc": r["service"], "t": datetime.fromisoformat(r["timestamp"]), "n": window},
        ).all()
        t = datetime.fromisoformat(r["timestamp"])
        before = sorted((x for x in rows if x.timestamp < t), key=lambda x: x.timestamp)
        after = sorted((x for x in rows if x.timestamp > t), key=lambda x: x.timestamp)
        r["context"] = {"before": [_row(x) for x in before], "after": [_row(x) for x in after]}


def _highlights(message: str, pattern: re.Pattern | None) -> list[list[int]]:
    if pattern is None:
        return []
    return [[m.start(), m.end()] for m in pattern.finditer(message)][:10]


# ---- keyword ---------------------------------------------------------------------------------------------------
def keyword_search(db: Session, project_id: uuid.UUID, query: str, *, regex: bool = False, exact: bool = False,
                   filters: Filters | None = None, limit: int = 50, offset: int = 0, context_window: int = 2) -> dict[str, Any]:
    t0 = time.monotonic()
    f = filters or Filters()
    params: dict[str, Any] = {"p": project_id, "lim": min(max(limit, 1), 500), "off": max(offset, 0)}
    query = (query or "").strip()
    match_sql, hl = "", None
    if query:
        if regex:
            if len(query) > MAX_REGEX_LEN:
                raise SearchError("regular expression is too long")
            try:
                hl = re.compile(query, re.I)
            except re.error as exc:
                raise SearchError(f"invalid regular expression: {exc}") from exc
            match_sql = " AND message ~* :q"
            params["q"] = query
        elif exact:
            match_sql = " AND message ILIKE :q"
            params["q"] = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            hl = re.compile(re.escape(query), re.I)
        else:
            match_sql = " AND message_tsv @@ websearch_to_tsquery('english', :q)"
            params["q"] = query
            words = [w for w in re.findall(r"[\w.-]+", query) if w.lower() not in ("or", "and")]
            hl = re.compile("|".join(re.escape(w) for w in words), re.I) if words else None
    where = "project_id = :p" + match_sql + _where(f, params)
    db.execute(text("SET LOCAL statement_timeout = '8000'"))
    try:
        rows = db.execute(
            text(f"SELECT {COLS} FROM log_records WHERE {where} ORDER BY timestamp DESC LIMIT :lim OFFSET :off"), params
        ).all()
        total = db.execute(text(f"SELECT count(*) FROM (SELECT 1 FROM log_records WHERE {where} LIMIT 10001) c"), params).scalar()
    except Exception as exc:  # regex / timeout errors from the database
        db.rollback()
        raise SearchError(f"search failed: {str(exc.__cause__ or exc)[:200]}") from exc
    results = [_row(r) for r in rows]
    for r in results:
        r["highlights"] = _highlights(r["message"], hl)
    _attach_sources(db, results)
    _attach_context(db, project_id, results, context_window)
    return {"mode": "keyword", "query": query, "count": len(results), "total": total, "total_capped": total >= 10001,
            "results": results, "took_ms": int((time.monotonic() - t0) * 1000)}


# ---- semantic ---------------------------------------------------------------------------------------------------
def semantic_search(db: Session, project_id: uuid.UUID, query: str, *, filters: Filters | None = None, limit: int = 20,
                    context_window: int = 2, min_score: float = 0.25, errors_only: bool = False) -> dict[str, Any]:
    t0 = time.monotonic()
    f = filters or Filters()
    query = (query or "").strip()
    if not query:
        raise SearchError("semantic search needs a query")
    vec = get_provider().embed([query])[0]
    must: dict[str, Any] = {"project_id": str(project_id)}
    if f.services:
        must["services"] = f.services
    if errors_only or (f.min_severity and f.min_severity.upper() in ("ERROR", "FATAL")):
        must["is_error"] = True
    hits = vectorstore.search(vectorstore.LOGS, vec, limit=max(limit * 3, 30), must=must, score_threshold=min_score)
    if not hits:
        return {"mode": "semantic", "query": query, "count": 0, "total": 0, "results": [], "took_ms": int((time.monotonic() - t0) * 1000)}
    by_hash = {h["payload"]["template_hash"]: h for h in hits if h["payload"].get("template_hash")}
    hashes = list(by_hash)
    params: dict[str, Any] = {"p": project_id, "hashes": hashes}
    where = _where(f, params)
    rows = db.execute(
        text(
            f"""
            SELECT h.ord, r.* FROM unnest(CAST(:hashes AS text[])) WITH ORDINALITY AS h(hash, ord)
            CROSS JOIN LATERAL (
              SELECT {COLS} FROM log_records WHERE project_id = :p AND template_hash = h.hash {where}
              ORDER BY timestamp DESC LIMIT 3
            ) r ORDER BY h.ord, r.timestamp DESC
            """
        ),
        params,
    ).all()
    occ = {h: c for h, c in db.execute(text("SELECT template_hash, occurrence_count FROM log_templates WHERE project_id = :p AND template_hash = ANY(:h)"), {"p": project_id, "h": hashes})}
    results = []
    for r in rows:
        d = _row(r)
        hit = by_hash[r.template_hash]
        d["score"] = round(hit["score"], 4)
        d["template"] = hit["payload"].get("template")
        d["occurrences"] = occ.get(r.template_hash)
        results.append(d)
    results.sort(key=lambda d: d["timestamp"], reverse=True)  # newest first ...
    results.sort(key=lambda d: -d["score"])  # ... then best match first (stable)
    results = results[:limit]
    _attach_sources(db, results)
    _attach_context(db, project_id, results, context_window)
    return {"mode": "semantic", "query": query, "count": len(results), "total": len(results), "results": results,
            "took_ms": int((time.monotonic() - t0) * 1000)}
