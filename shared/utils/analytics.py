"""Read-side analytics over stored logs (PRD tool 08 Health-State, tool 12 Deployment Comparison).

Pure DB reads shared by the API gateway tool router, the AI service (chat/RCA/report context),
the forecasting loop and the processing worker, so every consumer sees the same numbers.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from shared.config import settings

_LAT = "CASE WHEN jsonb_typeof(attributes->'latency_ms') = 'number' THEN (attributes->>'latency_ms')::float END"


def utcnow() -> datetime:
    return datetime.now(UTC)


def clock_now(db: Session, project_id: uuid.UUID | None = None, service: str | None = None) -> datetime:
    """The agent's notion of 'now'. `wall` (default) = real time. `data` = the latest ingested record,
    which lets historical/replayed logs be analysed as if live (demos, backtests)."""
    if settings.forecast_clock != "data":
        return utcnow()
    q = "SELECT max(timestamp) FROM log_records WHERE 1=1"
    p: dict[str, Any] = {}
    if project_id:
        q += " AND project_id = :p"
        p["p"] = project_id
    if service:
        q += " AND service = :s"
        p["s"] = service
    return db.execute(text(q), p).scalar() or utcnow()


@dataclass
class MinuteSeries:
    minutes: list[datetime]
    total: np.ndarray
    errors: np.ndarray
    warns: np.ndarray
    latency: np.ndarray  # NaN where no latency samples

    def __len__(self) -> int:
        return len(self.minutes)


def minute_series(
    db: Session, project_id: uuid.UUID, service: str, start: datetime, end: datetime, environment: str | None = None
) -> MinuteSeries:
    """Gap-filled per-minute counts: total, errors (>=ERROR), warnings, mean latency."""
    env = "AND environment = :env" if environment else ""
    rows = db.execute(
        text(
            f"""
            WITH g AS (SELECT generate_series(date_trunc('minute', CAST(:s AS timestamptz)),
                                              date_trunc('minute', CAST(:e AS timestamptz)), interval '1 minute') AS minute),
            m AS (
              SELECT date_trunc('minute', timestamp) AS minute, count(*) AS total,
                     count(*) FILTER (WHERE severity_num >= 40) AS errors,
                     count(*) FILTER (WHERE severity_num = 30) AS warns,
                     avg({_LAT}) AS lat
              FROM log_records
              WHERE project_id = :p AND service = :svc AND timestamp >= :s AND timestamp < CAST(:e AS timestamptz) + interval '1 minute' {env}
              GROUP BY 1
            )
            SELECT g.minute, COALESCE(m.total, 0), COALESCE(m.errors, 0), COALESCE(m.warns, 0), m.lat
            FROM g LEFT JOIN m USING (minute) ORDER BY g.minute
            """
        ),
        {"p": project_id, "svc": service, "s": start, "e": end, "env": environment},
    ).all()
    return MinuteSeries(
        minutes=[r[0] for r in rows],
        total=np.array([r[1] for r in rows], dtype=float),
        errors=np.array([r[2] for r in rows], dtype=float),
        warns=np.array([r[3] for r in rows], dtype=float),
        latency=np.array([np.nan if r[4] is None else r[4] for r in rows], dtype=float),
    )


def robust_stats(x: np.ndarray) -> tuple[float, float]:
    """Median and a robust std estimate (MAD * 1.4826) - unaffected by the spikes we are hunting for."""
    if len(x) == 0:
        return 0.0, 0.0
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med))) * 1.4826
    return med, mad


# ---------------------------------------------------------------------------------------------------
def health_state(
    db: Session, project_id: uuid.UUID, window_minutes: int = 60, environment: str | None = None, service: str | None = None
) -> dict[str, Any]:
    """Continuously-updated health state per service (tool 08). One round of indexed aggregates."""
    now = clock_now(db, project_id)
    start = now - timedelta(minutes=window_minutes)
    prev_start = start - timedelta(minutes=window_minutes)
    params: dict[str, Any] = {"p": project_id, "s": start, "e": now, "ps": prev_start}
    filt = ""
    if environment:
        filt += " AND environment = :env"
        params["env"] = environment
    if service:
        filt += " AND service = :svc"
        params["svc"] = service

    services = db.execute(
        text(
            f"""
            SELECT service,
                   count(*) FILTER (WHERE timestamp >= :s) AS total,
                   count(*) FILTER (WHERE timestamp >= :s AND severity_num >= 40) AS errors,
                   count(*) FILTER (WHERE timestamp >= :s AND severity_num = 30) AS warns,
                   count(*) FILTER (WHERE timestamp >= :s AND severity_num = 50) AS fatals,
                   count(*) FILTER (WHERE timestamp < :s AND severity_num >= 40) AS prev_errors,
                   count(*) FILTER (WHERE timestamp < :s) AS prev_total,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY {_LAT}) FILTER (WHERE timestamp >= :s) AS p95,
                   max(timestamp) AS last_seen
            FROM log_records
            WHERE project_id = :p AND timestamp >= :ps AND timestamp <= :e {filt}
            GROUP BY service
            """
        ),
        params,
    ).all()

    risk_rows = db.execute(
        text(
            """
            SELECT DISTINCT ON (ms.name) ms.name, r.risk_score, r.trend, r.eta_minutes_low, r.eta_minutes_high, r.timestamp
            FROM monitored_services ms JOIN risk_snapshots r ON r.service_id = ms.id
            WHERE ms.project_id = :p ORDER BY ms.name, r.timestamp DESC
            """
        ),
        {"p": project_id},
    ).all()
    risk = {r[0]: {"risk_score": round(r[1], 1), "trend": r[2], "eta_minutes_low": r[3], "eta_minutes_high": r[4],
                   "as_of": r[5].isoformat()} for r in risk_rows}

    out_services = []
    for r in services:
        svc, total, errors, warns, fatals, prev_err, prev_tot, p95, last_seen = r
        rate = errors / total if total else 0.0
        prev_rate = prev_err / prev_tot if prev_tot else 0.0
        if total == 0:
            status = "silent"
        elif rate >= 0.10 or fatals:
            status = "unhealthy"
        elif rate >= 0.03 or (prev_rate and rate > 2 * prev_rate and errors >= 5):
            status = "degraded"
        else:
            status = "healthy"
        out_services.append(
            {
                "service": svc, "status": status, "records": total, "errors": errors, "warnings": warns, "fatal": fatals,
                "error_rate": round(rate, 4), "errors_per_min": round(errors / max(window_minutes, 1), 2),
                "previous_error_rate": round(prev_rate, 4),
                "trend": "rising" if rate > prev_rate * 1.25 and errors > 0 else "falling" if rate < prev_rate * 0.75 else "steady",
                "p95_latency_ms": round(p95, 1) if p95 is not None else None,
                "last_seen": last_seen.isoformat() if last_seen else None,
                "risk": risk.get(svc),
            }
        )
    out_services.sort(key=lambda s: (-s["errors"], s["service"]))

    sev = db.execute(
        text(f"SELECT severity, count(*) FROM log_records WHERE project_id = :p AND timestamp >= :s AND timestamp <= :e {filt} GROUP BY 1"),
        params,
    ).all()
    bucket = max(1, window_minutes // 30)
    timeline = db.execute(
        text(
            f"""
            SELECT to_timestamp(floor(extract(epoch FROM timestamp) / ({bucket} * 60)) * ({bucket} * 60)) AS t,
                   count(*) AS total, count(*) FILTER (WHERE severity_num >= 40) AS errors,
                   count(*) FILTER (WHERE severity_num = 30) AS warns
            FROM log_records WHERE project_id = :p AND timestamp >= :s AND timestamp <= :e {filt}
            GROUP BY 1 ORDER BY 1
            """
        ),
        params,
    ).all()

    trending = db.execute(
        text(
            f"""
            WITH cur AS (SELECT template_hash, count(*) AS n FROM log_records
                         WHERE project_id = :p AND severity_num >= 40 AND timestamp >= :s AND timestamp <= :e {filt} GROUP BY 1),
                 prev AS (SELECT template_hash, count(*) AS n FROM log_records
                          WHERE project_id = :p AND severity_num >= 40 AND timestamp >= :ps AND timestamp < :s {filt} GROUP BY 1)
            SELECT cur.template_hash, cur.n, COALESCE(prev.n, 0), t.template, c.label, c.id
            FROM cur LEFT JOIN prev USING (template_hash)
            JOIN log_templates t ON t.project_id = :p AND t.template_hash = cur.template_hash
            LEFT JOIN error_clusters c ON c.id = t.cluster_id
            ORDER BY (cur.n - COALESCE(prev.n, 0)) DESC, cur.n DESC LIMIT 8
            """
        ),
        params,
    ).all()

    return {
        "project_id": str(project_id),
        "as_of": now.isoformat(),
        "window_minutes": window_minutes,
        "services": out_services,
        "top_problematic_services": [s["service"] for s in out_services if s["errors"] > 0][:5],
        "severity_distribution": {r[0]: r[1] for r in sev},
        "error_timeline": [
            {"t": r[0].isoformat(), "total": r[1], "errors": r[2], "warnings": r[3]} for r in timeline
        ],
        "trending_issues": [
            {"template_hash": r[0], "count": r[1], "previous_count": r[2], "growth": round(r[1] / max(r[2], 1), 2),
             "message": r[3], "cluster": r[4], "cluster_id": str(r[5]) if r[5] else None}
            for r in trending
        ],
    }


# ---------------------------------------------------------------------------------------------------
def list_versions(db: Session, project_id: uuid.UUID, service: str | None = None) -> list[dict]:
    rows = db.execute(
        text(
            """
            SELECT service, version, environment, deployed_at, last_seen_at FROM deployments
            WHERE project_id = :p AND (CAST(:svc AS text) IS NULL OR service = :svc) ORDER BY deployed_at DESC LIMIT 200
            """
        ),
        {"p": project_id, "svc": service},
    ).all()
    return [{"service": r[0], "version": r[1], "environment": r[2], "deployed_at": r[3].isoformat(),
             "last_seen_at": r[4].isoformat() if r[4] else None} for r in rows]


def _version_stats(db: Session, project_id: uuid.UUID, service: str, version: str, environment: str | None) -> dict:
    env = "AND environment = :env" if environment else ""
    p = {"p": project_id, "svc": service, "v": version, "env": environment}
    r = db.execute(
        text(
            f"""
            SELECT count(*), count(*) FILTER (WHERE severity_num >= 40), count(*) FILTER (WHERE severity_num = 30),
                   min(timestamp), max(timestamp), avg({_LAT}),
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY {_LAT})
            FROM log_records WHERE project_id = :p AND service = :svc AND deployment_version = :v {env}
            """
        ),
        p,
    ).one()
    total, errors, warns, first, last, lat_mean, lat_p95 = r
    minutes = max(((last - first).total_seconds() / 60.0) if first and last else 0.0, 1.0)
    templates = db.execute(
        text(
            f"""
            SELECT template_hash, count(*) AS n, min(message) AS sample FROM log_records
            WHERE project_id = :p AND service = :svc AND deployment_version = :v AND severity_num >= 40 {env}
            GROUP BY 1 ORDER BY n DESC LIMIT 200
            """
        ),
        p,
    ).all()
    return {
        "version": version, "records": total, "errors": errors, "warnings": warns,
        "error_rate": errors / total if total else 0.0, "errors_per_min": errors / minutes,
        "duration_minutes": round(minutes, 1),
        "first_seen": first.isoformat() if first else None, "last_seen": last.isoformat() if last else None,
        "latency_mean_ms": round(lat_mean, 1) if lat_mean is not None else None,
        "latency_p95_ms": round(lat_p95, 1) if lat_p95 is not None else None,
        "error_templates": {t[0]: {"count": t[1], "sample": t[2]} for t in templates},
    }


def compare_deployments(
    db: Session, project_id: uuid.UUID, service: str, from_version: str, to_version: str, environment: str | None = None
) -> dict[str, Any]:
    """Side-by-side behavioural comparison (tool 12): error-rate change, new error types,
    performance degradation, service-health delta. `regression` flags the new version."""
    a = _version_stats(db, project_id, service, from_version, environment)
    b = _version_stats(db, project_id, service, to_version, environment)
    if a["records"] == 0 or b["records"] == 0:
        raise ValueError("no records found for one of the versions")
    ta, tb = a.pop("error_templates"), b.pop("error_templates")
    new_types = [
        {"template_hash": h, "count": v["count"], "sample": v["sample"]} for h, v in tb.items() if h not in ta
    ]
    resolved_types = [
        {"template_hash": h, "count": v["count"], "sample": v["sample"]} for h, v in ta.items() if h not in tb
    ]
    increased = [
        {"template_hash": h, "sample": v["sample"], "before_per_min": round(ta[h]["count"] / a["duration_minutes"], 2),
         "after_per_min": round(v["count"] / b["duration_minutes"], 2)}
        for h, v in tb.items()
        if h in ta and (v["count"] / b["duration_minutes"]) > 2 * (ta[h]["count"] / a["duration_minutes"]) and v["count"] >= 5
    ]
    er_delta = b["error_rate"] - a["error_rate"]
    er_rel = (b["error_rate"] / a["error_rate"]) if a["error_rate"] else (float("inf") if b["error_rate"] > 0 else 1.0)
    lat_delta = None
    lat_rel = None
    if a["latency_p95_ms"] and b["latency_p95_ms"]:
        lat_delta = round(b["latency_p95_ms"] - a["latency_p95_ms"], 1)
        lat_rel = b["latency_p95_ms"] / a["latency_p95_ms"]
    new_significant = [n for n in new_types if n["count"] >= max(3, 0.005 * b["records"])]
    reasons = []
    if er_delta >= 0.005 and er_rel >= 1.5:
        reasons.append(f"error rate rose from {a['error_rate']:.2%} to {b['error_rate']:.2%}")
    if new_significant:
        reasons.append(f"{len(new_significant)} new error type(s) appeared")
    if lat_rel and lat_rel >= 1.3 and lat_delta and lat_delta >= 50:
        reasons.append(f"p95 latency rose {lat_delta:.0f}ms ({lat_rel:.1f}x)")
    if increased:
        reasons.append(f"{len(increased)} existing error(s) became much more frequent")
    health = lambda s: "unhealthy" if s["error_rate"] >= 0.10 else "degraded" if s["error_rate"] >= 0.03 else "healthy"  # noqa: E731
    return {
        "service": service, "environment": environment, "from": a, "to": b,
        "deltas": {
            "error_rate": round(er_delta, 5), "error_rate_ratio": None if er_rel == float("inf") else round(er_rel, 2),
            "errors_per_min": round(b["errors_per_min"] - a["errors_per_min"], 3),
            "latency_p95_ms": lat_delta, "latency_p95_ratio": round(lat_rel, 2) if lat_rel else None,
        },
        "new_error_types": sorted(new_types, key=lambda n: -n["count"])[:10],
        "resolved_error_types": sorted(resolved_types, key=lambda n: -n["count"])[:10],
        "increased_errors": increased[:10],
        "health": {"from": health(a), "to": health(b)},
        "regression": bool(reasons),
        "regression_reasons": reasons,
    }
