"""Error velocity tracking (PRD 4.1).

A rolling time-series of error counts per service, sampled in configurable windows (default 60s),
with the first and second derivatives - so the agent sees not just "errors are rising" but "errors
are accelerating". Exponential growth in a normally stable service is a strong leading indicator.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy.orm import Session

from shared.config import settings
from shared.utils.analytics import MinuteSeries, minute_series, robust_stats


@dataclass
class VelocityResult:
    errors_per_min: float = 0.0  # smoothed current rate
    baseline_per_min: float = 0.0  # robust typical rate over the look-back
    ratio: float = 0.0  # current / baseline
    velocity: float = 0.0  # d(errors/min)/dt  (errors/min per minute)
    acceleration: float = 0.0  # d2(errors/min)/dt2
    growth_rate: float = 0.0  # exponential growth rate per minute (ln-space slope)
    score: float = 0.0  # 0-100
    window_seconds: int = 60
    series: list[dict] = field(default_factory=list)  # last windows for charts
    warnings_per_min: float = 0.0

    def as_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def _aggregate(s: MinuteSeries, minutes_per_window: int) -> tuple[list[datetime], np.ndarray, np.ndarray]:
    if minutes_per_window <= 1:
        return s.minutes, s.errors, s.warns
    n = len(s) // minutes_per_window * minutes_per_window
    if n == 0:
        return s.minutes, s.errors, s.warns
    off = len(s) - n
    e = s.errors[off:].reshape(-1, minutes_per_window).sum(axis=1)
    w = s.warns[off:].reshape(-1, minutes_per_window).sum(axis=1)
    stamps = s.minutes[off::minutes_per_window][: len(e)]
    return stamps, e, w


def compute_velocity(
    db: Session, project_id: uuid.UUID, service: str, environment: str | None, now: datetime,
    window_seconds: int | None = None, lookback_minutes: int | None = None,
) -> VelocityResult:
    window_seconds = window_seconds or settings.forecast_window_seconds
    lookback = lookback_minutes or settings.forecast_lookback_minutes
    s = minute_series(db, project_id, service, now - timedelta(minutes=lookback), now, environment)
    mpw = max(1, round(window_seconds / 60))
    stamps, err, warns = _aggregate(s, mpw)
    per_min = err / mpw  # normalise to errors per minute regardless of window size
    res = VelocityResult(window_seconds=window_seconds)
    if len(per_min) < 4:
        return res

    # robust baseline from the earlier part of the look-back (excludes the most recent ~10 minutes)
    hist = per_min[: max(len(per_min) - max(10 // mpw, 3), 3)]
    med, mad = robust_stats(hist)
    res.baseline_per_min = med

    # exponential smoothing damps single-window noise
    sm = np.empty_like(per_min)
    alpha = 0.5
    sm[0] = per_min[0]
    for i in range(1, len(per_min)):
        sm[i] = alpha * per_min[i] + (1 - alpha) * sm[i - 1]
    res.errors_per_min = float(sm[-1])
    res.warnings_per_min = float(warns[-3:].mean() / mpw) if len(warns) else 0.0
    res.ratio = res.errors_per_min / max(med, 0.25)

    k = min(len(sm), max(8, 8 // mpw + 4))
    x = np.arange(k, dtype=float) * mpw  # minutes
    y = sm[-k:]
    if np.ptp(y) > 1e-9:
        a, b, _ = np.polyfit(x, y, 2)
        res.acceleration = float(2 * a)
        res.velocity = float(2 * a * x[-1] + b)  # instantaneous slope at the newest point
        lin = np.polyfit(x, np.log1p(y), 1)
        res.growth_rate = float(lin[0])

    scale = max(res.baseline_per_min + 2.0, 3.0)
    level = np.clip((res.errors_per_min - res.baseline_per_min) / max(res.baseline_per_min * 3.0, 5.0), 0, 1)
    vel = np.clip(res.velocity / scale, 0, 1)
    acc = np.clip(res.acceleration / max(scale / 4.0, 1.0), 0, 1) if res.acceleration > 0 else 0.0
    exp = np.clip(res.growth_rate / 0.30, 0, 1) if res.errors_per_min >= 2 else 0.0
    raw = 100.0 * (0.30 * vel + 0.25 * acc + 0.30 * level + 0.15 * exp)
    res.score = float(raw * min(1.0, res.errors_per_min / 3.0))  # need real activity, not noise

    res.series = [
        {"t": stamps[i].isoformat(), "errors_per_min": round(float(per_min[i]), 2), "smoothed": round(float(sm[i]), 2)}
        for i in range(max(0, len(per_min) - 30), len(per_min))
    ]
    return res
