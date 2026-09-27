"""Root Cause Analysis Tool (tool 10).

Traces secondary failures back to their origin using:
  1. temporal correlation   - per-service error onsets and lagged cross-correlation of error series
  2. dependency inference   - error-precedence within shared trace IDs, service names cited in messages,
                              trace co-occurrence (who is downstream of whom)
  3. cloud LLM reasoning    - the deep model confirms/refines the computed chain and writes the explanation
Output: causal chain, confidence score, plain-language explanation and supporting evidence.
Target < 15s.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.prompts import build
from app.providers import AIUnavailable, ModelRole, heuristic_json, run_json
from shared.config.tools import Tier
from shared.models import ErrorCluster, Project, RcaResult
from shared.utils.analytics import clock_now, robust_stats
from shared.utils.audit import record_agent_action
from shared.utils.autonomy import evaluate
from shared.utils.events import add_feed_item
from shared.utils.logtemplate import strip_metadata

log = logging.getLogger("logpilot.rca")
LLM_TIMEOUT = 70.0  # quality over speed: the computed causal chain (not a canned message) is still a real,
# correct fallback, so this only degrades from "LLM-reasoned" to "rule-computed" on a genuine model outage,
# never as a routine speed trade-off - the deep model's own real-world latency is comfortably under this.
MAX_LAG = 10
INFRA_WORDS = ["redis", "postgres", "postgresql", "mysql", "mongodb", "kafka", "rabbitmq", "memcached", "elasticsearch", "dynamodb", "s3", "cassandra"]


@dataclass
class Node:
    service: str
    errors: np.ndarray
    onset: int | None
    onset_time: datetime | None
    total_errors: int
    peak_per_min: float
    top_errors: list[dict] = field(default_factory=list)
    sample_ids: list[str] = field(default_factory=list)


def _resolve_window(db: Session, project_id: uuid.UUID, service: str | None, cluster_id: uuid.UUID | None,
                    start: datetime | None, end: datetime | None) -> tuple[datetime, datetime, str | None]:
    if start and end:
        return start, end, service
    now = clock_now(db, project_id)
    if cluster_id:
        c = db.get(ErrorCluster, cluster_id)
        if c and c.last_seen:
            e = min(c.last_seen + timedelta(minutes=10), now)
            return e - timedelta(minutes=120), e, service
    lookback = now - timedelta(hours=48)  # no time given: find the most significant error burst in the last two days
    row = db.execute(
        text(
            """
            SELECT date_trunc('minute', timestamp) m, count(*) c FROM log_records
            WHERE project_id = :p AND severity_num >= 40 AND timestamp >= :s AND timestamp <= :e
              AND (CAST(:svc AS text) IS NULL OR service = :svc)
            GROUP BY 1 ORDER BY c DESC, m DESC LIMIT 1
            """
        ),
        {"p": project_id, "s": lookback, "e": now, "svc": service},
    ).first()
    if not row:
        return now - timedelta(hours=1), now, service
    peak = row[0]
    return peak - timedelta(minutes=60), min(peak + timedelta(minutes=30), now), service


def _series(db: Session, project_id: uuid.UUID, start: datetime, end: datetime) -> tuple[list[datetime], dict[str, np.ndarray], dict[str, np.ndarray]]:
    rows = db.execute(
        text(
            """
            SELECT service, date_trunc('minute', timestamp) m, count(*) FILTER (WHERE severity_num >= 40) e, count(*) t
            FROM log_records WHERE project_id = :p AND timestamp >= :s AND timestamp <= :e GROUP BY 1, 2
            """
        ),
        {"p": project_id, "s": start, "e": end},
    ).all()
    t0 = start.replace(second=0, microsecond=0)
    n = int((end - t0).total_seconds() // 60) + 1
    minutes = [t0 + timedelta(minutes=i) for i in range(n)]
    err: dict[str, np.ndarray] = {}
    tot: dict[str, np.ndarray] = {}
    for svc, m, e, t in rows:
        i = int((m - t0).total_seconds() // 60)
        if 0 <= i < n:
            err.setdefault(svc, np.zeros(n))[i] = e
            tot.setdefault(svc, np.zeros(n))[i] = t
    return minutes, err, tot


def _onset(errors: np.ndarray) -> int | None:
    med, mad = robust_stats(errors)
    thr = max(3.0, med + 3.5 * max(mad, 1.0, 0.25 * med))
    hot = errors >= thr
    for i in range(len(errors)):
        if hot[i] and hot[i : i + 3].sum() >= 2:
            return i
    return None


def _xcorr(a: np.ndarray, b: np.ndarray) -> tuple[float, int]:
    """Best Pearson correlation of `a` leading `b` by 0..MAX_LAG minutes."""
    best, best_lag = -1.0, 0
    for lag in range(0, MAX_LAG + 1):
        x, y = (a[: len(a) - lag], b[lag:]) if lag else (a, b)
        if len(x) < 8 or x.std() < 1e-9 or y.std() < 1e-9:
            continue
        c = float(np.corrcoef(x, y)[0, 1])
        if c > best:
            best, best_lag = c, lag
    return best, best_lag


def analyze(db: Session, project_id: uuid.UUID, *, service: str | None = None, cluster_id: uuid.UUID | None = None,
            start: datetime | None = None, end: datetime | None = None, trigger: str = "on_request",
            actor_id: uuid.UUID | None = None) -> dict[str, Any]:
    t_start = time.monotonic()
    w_start, w_end, service = _resolve_window(db, project_id, service, cluster_id, start, end)
    minutes, err, tot = _series(db, project_id, w_start - timedelta(minutes=15), w_end)

    nodes: dict[str, Node] = {}
    for svc, e in err.items():
        if e.sum() < 3:
            continue
        on = _onset(e)
        if on is None:
            continue
        nodes[svc] = Node(svc, e, on, minutes[on], int(e.sum()), float(e.max()))
    result: dict[str, Any] = {"window": {"start": w_start.isoformat(), "end": w_end.isoformat()}, "service": service}

    if not nodes:
        return _persist(db, project_id, cluster_id, service, w_start, w_end, [], 0.0,
                        "No service showed a sustained error burst in this window, so there is no failure to trace.", [], {"nodes": [], "edges": []},
                        trigger, actor_id, t_start, notes="no_errors")

    # ---- evidence: top errors per involved service -----------------------------------------------------
    for n in nodes.values():
        rows = db.execute(
            text(
                """
                SELECT template_hash, min(message) msg, count(*) c, min(timestamp) first, (array_agg(id ORDER BY timestamp))[1:2] ids
                FROM log_records WHERE project_id = :p AND service = :s AND severity_num >= 40 AND timestamp >= :a AND timestamp <= :b
                GROUP BY 1 ORDER BY c DESC LIMIT 3
                """
            ),
            {"p": project_id, "s": n.service, "a": w_start - timedelta(minutes=15), "b": w_end},
        ).all()
        n.top_errors = [{"message": strip_metadata(r[1], 300), "count": r[2], "first_seen": r[3].isoformat()} for r in rows]
        n.sample_ids = [str(i) for r in rows for i in (r[4] or [])][:4]

    # ---- 1) temporal correlation ------------------------------------------------------------------------
    edges: dict[tuple[str, str], dict[str, Any]] = {}
    names = list(nodes)
    for a in names:
        for b in names:
            if a == b:
                continue
            corr, lag = _xcorr(nodes[a].errors, nodes[b].errors)
            onset_gap = (nodes[b].onset - nodes[a].onset)  # minutes; >= 0 means a began first
            if corr >= 0.4 and onset_gap >= 0 and (lag > 0 or onset_gap > 0 or a < b):
                edges[(a, b)] = {"from": a, "to": b, "lag_min": float(max(lag, onset_gap)), "corr": round(corr, 3), "trace_count": 0, "hint": 0}

    # ---- 2) dependency inference ------------------------------------------------------------------------
    trace_rows = db.execute(
        text(
            """
            WITH e AS (SELECT trace_id, service, min(timestamp) t FROM log_records
                       WHERE project_id = :p AND severity_num >= 40 AND trace_id IS NOT NULL AND timestamp >= :a AND timestamp <= :b GROUP BY 1, 2)
            SELECT a.service, b.service, count(*) FROM e a JOIN e b ON a.trace_id = b.trace_id AND a.service <> b.service
              AND (a.t < b.t OR (a.t = b.t AND a.service < b.service)) GROUP BY 1, 2
            """
        ),
        {"p": project_id, "a": w_start - timedelta(minutes=15), "b": w_end},
    ).all()
    for a, b, c in trace_rows:
        if a in nodes and b in nodes:
            e = edges.setdefault((a, b), {"from": a, "to": b, "lag_min": float(max(nodes[b].onset - nodes[a].onset, 0)), "corr": 0.0, "trace_count": 0, "hint": 0})
            e["trace_count"] += int(c)
    for b, nb in nodes.items():  # "upstream payment-service timeout" style references
        blob = " ".join(x["message"].lower() for x in nb.top_errors)
        for a in nodes:
            if a != b and a.lower() in blob:
                e = edges.setdefault((a, b), {"from": a, "to": b, "lag_min": float(max(nodes[b].onset - nodes[a].onset, 0)), "corr": 0.0, "trace_count": 0, "hint": 0})
                e["hint"] += 1

    def weight(e: dict) -> float:
        return min(1.0, max(e["corr"], 0) * 0.6 + min(e["trace_count"], 50) / 50 * 0.5 + e["hint"] * 0.25)

    out_w = {n: sum(weight(e) for (a, _), e in edges.items() if a == n) for n in nodes}
    in_w = {n: sum(weight(e) for (_, b), e in edges.items() if b == n) for n in nodes}
    max_out = max(out_w.values()) or 1.0
    order = sorted(nodes, key=lambda n: (nodes[n].onset, -nodes[n].total_errors))
    N = len(order)

    def root_score(n: str) -> float:
        earliness = 1.0 if N == 1 else 1 - order.index(n) / (N - 1)
        return 0.45 * earliness + 0.35 * max(0.0, out_w[n] / max_out - 0.5 * min(in_w[n] / max_out, 1.0)) + 0.20 * min(nodes[n].peak_per_min / 20.0, 1.0)

    root = max(nodes, key=root_score)
    # chain = root + every involved service reachable through an edge (ordered by onset)
    chain_names = [root]
    frontier = [root]
    while frontier:
        cur = frontier.pop(0)
        for (a, b), e in sorted(edges.items(), key=lambda kv: nodes[kv[0][1]].onset):
            if a == cur and b not in chain_names and nodes[b].onset >= nodes[root].onset:
                chain_names.append(b)
                frontier.append(b)
    chain_names = [root] + sorted([n for n in chain_names if n != root], key=lambda n: nodes[n].onset)
    others = [n for n in nodes if n not in chain_names]

    chain = []
    for n in chain_names:
        nd = nodes[n]
        has_out = any(a == n and b in chain_names for (a, b) in edges)
        role = "root_cause" if n == root else ("contributing" if has_out else "symptom")
        lag = (nd.onset - nodes[root].onset) if n != root else None
        chain.append({"service": n, "role": role, "onset": nd.onset_time.isoformat(), "peak_errors_per_min": nd.peak_per_min,
                      "total_errors": nd.total_errors, "top_errors": nd.top_errors, "lag_from_root_min": lag, "sample_ids": nd.sample_ids})

    # suspected external infrastructure dependency (not a monitored service) named in the root's errors
    blob = " ".join(x["message"].lower() for x in nodes[root].top_errors)
    known = {s.lower() for s in err}
    infra = next((w for w in INFRA_WORDS if w in blob and not any(w in k for k in known)), None)
    if infra:
        chain.insert(0, {"service": f"{infra} (external dependency)", "role": "root_cause", "onset": chain[0]["onset"], "peak_errors_per_min": 0.0,
                         "total_errors": 0, "top_errors": [], "lag_from_root_min": None, "sample_ids": [], "suspected": True})
        chain[1]["role"] = "contributing"

    root_edges = [e for (a, _), e in edges.items() if a == root]
    best_corr = max([e["corr"] for e in root_edges], default=0.0)
    trace_ev = sum(e["trace_count"] for e in root_edges)
    gap = (nodes[order[1]].onset - nodes[order[0]].onset) if N > 1 else 0
    coverage = len(chain_names) / N
    conf = 0.25 + 0.30 * min(1.0, best_corr) + 0.20 * min(1.0, trace_ev / 20) + 0.15 * min(1.0, gap / 5) + 0.10 * coverage
    if N == 1:
        conf = min(conf, 0.55)
    conf = float(np.clip(conf, 0.2, 0.95))

    graph = {"nodes": [{"id": n, "onset": nodes[n].onset_time.isoformat(), "errors": nodes[n].total_errors} for n in nodes],
             "edges": [{**e, "weight": round(weight(e), 3)} for e in edges.values() if e["from"] in chain_names or e["to"] in chain_names]}
    top_edges = sorted(edges.values(), key=lambda e: -weight(e))[:8]

    # ---- 3) LLM reasoning ---------------------------------------------------------------------------------
    ctx = {"focus": service or root, "window": result["window"], "chain": chain, "edges": top_edges,
           "algorithmic_confidence": conf, "unrelated_services": others}
    explanation, final_chain, final_conf, ai_used = "", chain, conf, True
    try:
        data = run_json("rca_reasoning", build("rca_reasoning", ctx), ModelRole.DEEP, ctx, timeout=LLM_TIMEOUT)
    except (AIUnavailable, Exception) as exc:  # noqa: BLE001 - any provider failure degrades to computed result
        log.info("RCA reasoning model unavailable (%s); using computed chain", exc)
        data, ai_used = heuristic_json("rca_reasoning", ctx), False
    if isinstance(data.get("causal_chain"), list) and data["causal_chain"]:
        by_svc = {c["service"]: c for c in chain}
        merged = []
        for c in data["causal_chain"]:
            base = by_svc.get(c.get("service"), {})
            merged.append({**base, "service": c.get("service"), "role": c.get("role") or base.get("role"), "event": c.get("event") or "", "onset": base.get("onset") or c.get("onset")})
        final_chain = merged
    explanation = str(data.get("explanation") or "")
    try:
        llm_conf = float(data.get("confidence", conf))
    except (TypeError, ValueError):
        llm_conf = conf
    final_conf = float(np.clip(conf * 0.6 + llm_conf * 0.4 if ai_used else conf, 0.05, 0.98))

    evidence = [{"service": c["service"], "first_seen": c.get("onset"), "errors": c.get("total_errors", 0), "top_errors": c.get("top_errors", []),
                 "sample_record_ids": c.get("sample_ids", [])} for c in chain if not c.get("suspected")]
    return _persist(db, project_id, cluster_id, service, w_start, w_end, final_chain, final_conf, explanation, evidence, graph,
                    trigger, actor_id, t_start, ai_used=ai_used)


def _persist(db, project_id, cluster_id, service, w_start, w_end, chain, conf, explanation, evidence, graph, trigger, actor_id, t0,
             ai_used: bool = False, notes: str | None = None) -> dict[str, Any]:
    org = db.get(Project, project_id).org_id
    tier = Tier.AUTONOMOUS_POLICY_BOUNDED if trigger == "autonomous" else Tier.READ_ONLY
    decision = evaluate(db, org, "root_cause_analysis", requested_tier=tier, confidence=conf)
    ms = int((time.monotonic() - t0) * 1000)
    row = RcaResult(
        project_id=project_id, cluster_id=cluster_id, service=(chain[0]["service"] if chain else service), window_start=w_start,
        window_end=w_end, causal_chain=chain, confidence=conf, explanation=explanation, evidence=evidence, dependency_graph=graph,
        review_status="pending_review", trigger="autonomous" if trigger == "autonomous" else "on_request", latency_ms=ms,
    )
    db.add(row)
    db.flush()
    record_agent_action(
        db, tool_name="root_cause_analysis", trigger="autonomous" if trigger == "autonomous" else "user_request",
        autonomy_level=decision.effective_tier, org_id=org, project_id=project_id, actor_id=actor_id, confidence=conf,
        status="executed" if decision.autonomous or trigger != "autonomous" else "proposed", output_ref=f"rca:{row.id}",
        downgraded_from=decision.requested_tier if decision.downgraded else None,
        summary=(f"RCA: {' -> '.join(c['service'] for c in chain)} (confidence {conf:.2f}, {ms}ms)" if chain else "RCA: no failure found"),
        details={"latency_ms": ms, "ai_reasoning": ai_used, "notes": notes},
    )
    if trigger == "autonomous" and chain:
        add_feed_item(db, project_id=project_id, org_id=org, kind="rca", severity="info", ref_type="rca", ref_id=row.id,
                      service=chain[0]["service"], title=f"Root cause analysis: {chain[0]['service']} looks like the origin",
                      body=(explanation or "")[:500] + "\n(Awaiting human review.)")
    return {
        "id": str(row.id), "window": {"start": w_start.isoformat(), "end": w_end.isoformat()}, "service": row.service,
        "causal_chain": chain, "confidence": round(conf, 3), "explanation": explanation, "evidence": evidence,
        "dependency_graph": graph, "review_status": row.review_status, "latency_ms": ms, "ai_reasoning": ai_used,
        "trigger": row.trigger, "notes": notes,
    }
