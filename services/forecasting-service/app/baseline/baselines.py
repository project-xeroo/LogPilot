"""Historical baseline comparison (PRD 4.1).

A behavioural baseline per service: the normal distribution of error rate, request rate, latency
and error *patterns* across time-of-day and day-of-week ("<dow>:<hour>" windows, plus "all").
Minutes inside past incident episodes are excluded so the baseline describes *normal*.

Deviation from baseline - even at absolute values that seem low - raises risk when the deviation
profile matches historical pre-failure patterns (that matching is done by the indicators module).
"""
from __future__ import annotations

import hashlib
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from shared.models import MonitoredService, ServiceBaseline
from shared.models.base import utcnow
from shared.utils.analytics import MinuteSeries, minute_series, robust_stats

HISTORY_DAYS = 30
MIN_BUCKET_SAMPLES = 30
EPISODE_PAD_BEFORE = 20  # minutes before an incident episode to exclude (the build-up is not "normal")
EPISODE_PAD_AFTER = 10


def detect_episodes(errors: np.ndarray, min_len: int = 4) -> list[tuple[int, int]]:
    """Sustained error bursts (index ranges) - used for baseline exclusion and signature learning."""
    if len(errors) < 30:
        return []
    med, mad = robust_stats(errors)
    thr = max(5.0, med + 6.0 * max(mad, 1.0))
    idx = np.flatnonzero(errors >= thr)
    if len(idx) == 0:
        return []
    # hot minutes separated by <= 5 quiet minutes belong to the same episode
    splits = np.flatnonzero(np.diff(idx) > 6) + 1
    groups = np.split(idx, splits)
    # a real incident has several hot minutes, not two isolated spikes that merely fall close together
    return [(int(g[0]), int(g[-1])) for g in groups if len(g) >= min_len]


def update_baselines(db: Session, project_id: uuid.UUID, services: list[MonitoredService] | None = None) -> int:
    """(Re)compute baselines. Returns number of service_baselines rows written."""
    now = utcnow()
    svcs = services or db.query(MonitoredService).filter(MonitoredService.project_id == project_id, MonitoredService.enabled).all()
    written = 0
    for ms in svcs:
        first = db.execute(text("SELECT min(timestamp), max(timestamp) FROM log_records WHERE project_id = :p AND service = :s"),
                           {"p": project_id, "s": ms.name}).one()
        if not first[0]:
            continue
        end = min(first[1], now) if first[1] else now
        start = max(first[0], end - timedelta(days=HISTORY_DAYS))
        s = minute_series(db, project_id, ms.name, start, end, ms.environment)
        if len(s) < 30:
            continue
        keep = np.ones(len(s), dtype=bool)
        for a, b in detect_episodes(s.errors):
            keep[max(0, a - EPISODE_PAD_BEFORE) : min(len(s), b + EPISODE_PAD_AFTER + 1)] = False
        if keep.sum() < 20:
            keep[:] = True
        rows = _bucketise(s, keep)
        pattern_dist, pattern_hash = _pattern_dist(db, project_id, ms.name, start, end)
        for window, r in rows.items():
            stmt = pg_insert(ServiceBaseline).values(
                service_id=ms.id, window=window, error_rate_mean=r["e_mean"], error_rate_std=r["e_std"], request_rate_mean=r["t_mean"],
                request_rate_std=r["t_std"], latency_mean=r["l_mean"], latency_std=r["l_std"], sample_count=r["n"],
                pattern_hash=pattern_hash, pattern_dist=pattern_dist if window == "all" else None, updated_at=now,
            )
            stmt = stmt.on_conflict_do_update(index_elements=["service_id", "window"], set_={c: stmt.excluded[c] for c in (
                "error_rate_mean", "error_rate_std", "request_rate_mean", "request_rate_std", "latency_mean", "latency_std",
                "sample_count", "pattern_hash", "pattern_dist", "updated_at")})
            db.execute(stmt)
            written += 1
    db.commit()
    return written


def _bucketise(s: MinuteSeries, keep: np.ndarray) -> dict[str, dict]:
    buckets: dict[str, list[int]] = {}
    for i, m in enumerate(s.minutes):
        if keep[i]:
            buckets.setdefault(f"{m.weekday()}:{m.hour}", []).append(i)
    buckets["all"] = [i for i in range(len(s)) if keep[i]]
    out = {}
    for w, idx in buckets.items():
        e, t, lat = s.errors[idx], s.total[idx], s.latency[idx]
        lat = lat[~np.isnan(lat)]
        out[w] = {
            "e_mean": float(e.mean()), "e_std": float(e.std(ddof=1)) if len(e) > 1 else 0.0,
            "t_mean": float(t.mean()), "t_std": float(t.std(ddof=1)) if len(t) > 1 else 0.0,
            "l_mean": float(lat.mean()) if len(lat) else None, "l_std": float(lat.std(ddof=1)) if len(lat) > 1 else None,
            "n": len(idx),
        }
    return out


def _pattern_dist(db: Session, project_id: uuid.UUID, service: str, start: datetime, end: datetime) -> tuple[dict, str]:
    rows = db.execute(
        text("SELECT template_hash, count(*) FROM log_records WHERE project_id = :p AND service = :s AND severity_num >= 40 "
             "AND timestamp BETWEEN :a AND :b GROUP BY 1 ORDER BY 2 DESC LIMIT 25"),
        {"p": project_id, "s": service, "a": start, "b": end},
    ).all()
    total = sum(c for _, c in rows) or 1
    dist = {h: round(c / total, 5) for h, c in rows}
    top = sorted(dist, key=dist.get, reverse=True)[:10]
    return dist, hashlib.sha1("|".join(top).encode()).hexdigest()[:32]


# ---- deviation ---------------------------------------------------------------------------------------------------
@dataclass
class BaselineResult:
    score: float = 0.0  # 0-100
    z: float = 0.0
    ratio: float = 0.0
    expected_per_min: float = 0.0
    std_per_min: float = 0.0
    window: str = "none"
    pattern_divergence: float = 0.0  # Jensen-Shannon, 0..1
    sample_count: int = 0
    available: bool = False
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def _jsd(p: dict[str, float], q: dict[str, float]) -> float:
    keys = set(p) | set(q)
    if not keys:
        return 0.0
    P = np.array([p.get(k, 0.0) for k in keys])
    Q = np.array([q.get(k, 0.0) for k in keys])
    P, Q = P / max(P.sum(), 1e-12), Q / max(Q.sum(), 1e-12)
    M = 0.5 * (P + Q)

    def kl(a, b):
        mask = a > 0
        return float(np.sum(a[mask] * np.log2(a[mask] / b[mask])))

    return float(np.clip(0.5 * kl(P, M) + 0.5 * kl(Q, M), 0, 1))


def deviation(db: Session, ms: MonitoredService, now: datetime, current_per_min: float, current_pattern: dict[str, float] | None = None) -> BaselineResult:
    win = f"{now.weekday()}:{now.hour}"
    rows = {r.window: r for r in db.query(ServiceBaseline).filter(ServiceBaseline.service_id == ms.id, ServiceBaseline.window.in_([win, "all"])).all()}
    row = rows.get(win) if rows.get(win) and rows[win].sample_count >= MIN_BUCKET_SAMPLES else rows.get("all")
    res = BaselineResult()
    if row is None:
        res.notes.append("no baseline yet")
        return res
    res.available, res.window, res.sample_count = True, row.window, row.sample_count
    res.expected_per_min, res.std_per_min = row.error_rate_mean, row.error_rate_std
    std = max(row.error_rate_std, 0.5, 0.1 * row.error_rate_mean)
    res.z = (current_per_min - row.error_rate_mean) / std
    res.ratio = current_per_min / max(row.error_rate_mean, 0.1)
    allrow = rows.get("all")
    if current_pattern and allrow and allrow.pattern_dist:
        res.pattern_divergence = _jsd(current_pattern, allrow.pattern_dist)
    z_part = float(np.clip(res.z / 6.0, 0, 1))
    r_part = float(np.clip((res.ratio - 1.0) / 9.0, 0, 1))
    raw = 0.55 * z_part + 0.30 * r_part + 0.15 * res.pattern_divergence
    # low absolute values still count, but pure silence cannot be a deviation *toward* failure
    res.score = float(100.0 * raw * (1.0 if current_per_min >= 0.5 else current_per_min / 0.5))
    return res


def current_pattern(db: Session, project_id: uuid.UUID, service: str, now: datetime, minutes: int = 15) -> dict[str, float]:
    rows = db.execute(
        text("SELECT template_hash, count(*) FROM log_records WHERE project_id = :p AND service = :s AND severity_num >= 40 "
             "AND timestamp BETWEEN :a AND :b GROUP BY 1"),
        {"p": project_id, "s": service, "a": now - timedelta(minutes=minutes), "b": now},
    ).all()
    total = sum(c for _, c in rows) or 1
    return {h: c / total for h, c in rows}


_ = (Counter, asdict)
