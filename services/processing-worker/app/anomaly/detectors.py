"""Anomaly detection (tool 13): statistical baseline comparison + ML-based outlier detection.

Detects: error spikes, latency spikes, traffic spikes, new unseen error types, sudden service
silences. Each anomaly carries a plain-language explanation and feeds the forecasting loop.

Statistics use the median / MAD (robust z-score) so the very spike being hunted does not inflate
the baseline it is compared against.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.anomaly.iforest import IsolationForest
from shared.config import settings
from shared.utils.analytics import MinuteSeries, minute_series, robust_stats

MAX_LOOKBACK = timedelta(hours=48)
GAP_MINUTES = 2  # merge flagged minutes separated by <= this many quiet minutes
SILENCE_MINUTES = 10


@dataclass
class Detected:
    kind: str
    service: str
    window_start: datetime
    window_end: datetime
    score: float
    observed: float | None
    expected: float | None
    method: str = "baseline_zscore"
    severity: str = "medium"
    evidence: dict = field(default_factory=dict)
    explanation: str = ""


def _windows(flags: np.ndarray) -> list[tuple[int, int]]:
    idx = np.flatnonzero(flags)
    if len(idx) == 0:
        return []
    out, s, p = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - p > GAP_MINUTES + 1:
            out.append((int(s), int(p)))
            s = i
        p = i
    out.append((int(s), int(p)))
    return out


def _z(x: np.ndarray, med: float, mad: float, floor: float) -> np.ndarray:
    return (x - med) / max(mad, floor)


def _severity(z: float, ratio: float) -> str:
    return "high" if z >= 10 or ratio >= 10 else "medium" if z >= 6 or ratio >= 4 else "low"


def statistical_anomalies(service: str, s: MinuteSeries) -> list[Detected]:
    out: list[Detected] = []
    if len(s) < 10:
        return out
    # error spikes ------------------------------------------------------------------------------------
    med, mad = robust_stats(s.errors)
    z = _z(s.errors, med, mad, floor=max(1.0, 0.25 * med))
    flags = (z >= 4.0) & (s.errors >= max(5.0, 3.0 * med))
    for a, b in _windows(flags):
        seg = s.errors[a : b + 1]
        peak = float(seg.max())
        out.append(Detected(
            "error_spike", service, s.minutes[a], s.minutes[b], float(z[a : b + 1].max()), peak, med,
            severity=_severity(float(z[a : b + 1].max()), peak / max(med, 0.5)),
            evidence={"errors_in_window": int(seg.sum()), "minutes": b - a + 1, "typical_per_min": round(med, 2)},
        ))
    # traffic spikes ------------------------------------------------------------------------------------
    med_t, mad_t = robust_stats(s.total)
    zt = _z(s.total, med_t, mad_t, floor=max(1.0, 0.25 * med_t))
    flags = (zt >= 5.0) & (s.total >= 2.0 * max(med_t, 1.0)) & (s.total >= 20)
    for a, b in _windows(flags):
        peak = float(s.total[a : b + 1].max())
        out.append(Detected(
            "traffic_spike", service, s.minutes[a], s.minutes[b], float(zt[a : b + 1].max()), peak, med_t,
            severity=_severity(float(zt[a : b + 1].max()), peak / max(med_t, 1.0)),
            evidence={"records_in_window": int(s.total[a : b + 1].sum()), "typical_per_min": round(med_t, 1)},
        ))
    # latency spikes --------------------------------------------------------------------------------------
    lat = s.latency
    valid = ~np.isnan(lat)
    if valid.sum() >= 30:
        med_l, mad_l = robust_stats(lat[valid])
        filled = np.where(valid, lat, med_l)
        zl = _z(filled, med_l, mad_l, floor=max(5.0, 0.15 * med_l))
        flags = (zl >= 4.0) & valid & (filled >= 2.0 * med_l) & (filled - med_l >= 100)
        for a, b in _windows(flags):
            peak = float(np.nanmax(lat[a : b + 1]))
            out.append(Detected(
                "latency_spike", service, s.minutes[a], s.minutes[b], float(zl[a : b + 1].max()), peak, med_l,
                severity=_severity(float(zl[a : b + 1].max()), peak / max(med_l, 1.0)),
                evidence={"typical_ms": round(med_l, 1), "peak_ms": round(peak, 1)},
            ))
    # service silence -------------------------------------------------------------------------------------
    nz = np.flatnonzero(s.total > 0)
    if len(nz) >= 30:
        trailing = len(s) - 1 - int(nz[-1])
        recent_rate = float(np.median(s.total[max(0, int(nz[-1]) - 30) : int(nz[-1]) + 1]))
        if trailing >= SILENCE_MINUTES and recent_rate >= 1.0:
            a = int(nz[-1]) + 1
            out.append(Detected(
                "service_silence", service, s.minutes[a], s.minutes[-1], float(trailing), 0.0, recent_rate,
                severity="high" if trailing >= 30 else "medium",
                evidence={"silent_minutes": trailing, "previous_rate_per_min": round(recent_rate, 1)},
            ))
    return out


def outlier_anomalies(service: str, s: MinuteSeries, already: list[Detected]) -> list[Detected]:
    """ML outlier detection (IsolationForest) on [errors, total, error-ratio, latency] per minute.
    Catches multivariate shifts the univariate z-scores miss, e.g. a rising error *ratio* at flat volume."""
    if len(s) < 120:
        return []
    ratio = np.divide(s.errors, np.maximum(s.total, 1.0))
    lat = np.where(np.isnan(s.latency), np.nanmedian(s.latency) if (~np.isnan(s.latency)).any() else 0.0, s.latency)
    X = np.column_stack([s.errors, s.total, ratio, lat])
    med, mad = np.median(X, axis=0), np.median(np.abs(X - np.median(X, axis=0)), axis=0) * 1.4826
    Xn = (X - med) / np.where(mad < 1e-9, 1.0, mad)  # robust standardisation so no feature dominates
    forest = IsolationForest(n_estimators=60, max_samples=256, contamination=0.01, seed=42).fit(Xn)
    scores = forest.score_samples(Xn)  # higher = more anomalous
    labels = scores >= forest.threshold_
    med_e, _ = robust_stats(s.errors)
    med_r, _ = robust_stats(ratio)
    covered = np.zeros(len(s), dtype=bool)
    for d in already:
        for i, m in enumerate(s.minutes):
            if d.window_start <= m <= d.window_end:
                covered[i] = True
    flags = labels & ~covered & (s.errors >= max(3.0, 2 * med_e)) & (ratio > 2 * max(med_r, 0.005))
    out = []
    for a, b in _windows(flags):
        if s.errors[a : b + 1].sum() < 6:  # ignore tiny blips the forest happens to isolate
            continue
        out.append(Detected(
            "error_spike", service, s.minutes[a], s.minutes[b], float(scores[a : b + 1].max() * 10), float(s.errors[a : b + 1].max()),
            med_e, method="isolation_forest", severity="medium",
            evidence={"outlier_minutes": int(flags[a : b + 1].sum()), "error_ratio_peak": round(float(ratio[a : b + 1].max()), 3)},
        ))
    return out


def new_error_types(db: Session, project_id: uuid.UUID, ref_end: datetime, recent: timedelta) -> list[Detected]:
    """Error templates never seen before the recent window, in a project that has prior history."""
    history_end = ref_end - recent
    prior = db.execute(
        text("SELECT count(*) FROM log_templates WHERE project_id = :p AND first_seen < :h"), {"p": project_id, "h": history_end}
    ).scalar()
    if (prior or 0) < 5:
        return []
    rows = db.execute(
        text(
            """
            SELECT id, template, sample_message, occurrence_count, first_seen, last_seen, services
            FROM log_templates
            WHERE project_id = :p AND is_error AND first_seen >= :h AND is_new_in_last_session
            ORDER BY occurrence_count DESC LIMIT 25
            """
        ),
        {"p": project_id, "h": history_end},
    ).all()
    out = []
    for r in rows:
        svc = (r[6] or ["unknown"])[0]
        out.append(Detected(
            "new_error_type", svc, r[4], r[5], float(min(r[3], 100)), float(r[3]), 0.0,
            severity="high" if r[3] >= 20 else "medium",
            evidence={"template_id": str(r[0]), "message": r[2][:300], "occurrences": r[3], "services": r[6] or []},
        ))
    return out


def explain(d: Detected) -> str:
    t0, t1 = d.window_start.strftime("%H:%M"), d.window_end.strftime("%H:%M")
    if d.kind == "error_spike":
        exp = d.expected or 0
        ratio = f"{d.observed / exp:.1f}x its typical {exp:.1f}/min" if exp >= 0.5 else f"well above its near-zero baseline ({exp:.1f}/min)"
        return f"{d.service} logged up to {d.observed:.0f} errors per minute between {t0} and {t1} - {ratio}."
    if d.kind == "traffic_spike":
        return (f"Traffic to {d.service} jumped to {d.observed:.0f} log lines/min between {t0} and {t1}, "
                f"{d.observed / max(d.expected or 1, 1):.1f}x its typical {d.expected:.0f}/min.")
    if d.kind == "latency_spike":
        return (f"{d.service} latency spiked to {d.observed:.0f}ms between {t0} and {t1} "
                f"(typically {d.expected:.0f}ms).")
    if d.kind == "service_silence":
        return (f"{d.service} stopped logging at {t0} ({d.evidence.get('silent_minutes', 0)} minutes of silence) "
                f"after averaging {d.expected:.0f} lines/min - it may be down or disconnected.")
    if d.kind == "new_error_type":
        return (f"A new error type appeared in {d.service} at {t0}, never seen before: "
                f"\"{d.evidence.get('message', '')[:160]}\" ({d.evidence.get('occurrences', 0)} occurrences).")
    return f"Unusual behaviour in {d.service} between {t0} and {t1}."


def detect_project_window(
    db: Session, project_id: uuid.UUID, start: datetime, end: datetime, services: list[tuple[str, str]]
) -> list[Detected]:
    start = max(start, end - MAX_LOOKBACK)
    results: list[Detected] = []
    for svc, env in services:
        s = minute_series(db, project_id, svc, start, end, env)
        stat = statistical_anomalies(svc, s)
        results.extend(stat)
        results.extend(outlier_anomalies(svc, s, stat))
    results.extend(new_error_types(db, project_id, end, timedelta(minutes=settings.anomaly_recent_minutes)))
    for d in results:
        d.explanation = explain(d)
    return results
