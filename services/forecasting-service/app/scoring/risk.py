"""Risk scoring (PRD 4.2 / 4.4): a weighted 0-100 failure risk score per service, recalculated each cycle.

    risk = w_velocity * velocity + w_similarity * similarity + w_baseline * baseline_deviation
    defaults: 30% / 40% / 30%; refined per service by the feedback loop (see indicators.feedback)

`similarity` blends the leading-indicator match against learned pre-failure signatures with the
pattern-drift signal; with no incident history yet, drift carries the whole similarity weight.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import datetime

import numpy as np
from sqlalchemy.orm import Session

from app.baseline import BaselineResult
from app.drift import DriftResult
from app.indicators import SimilarityResult
from app.velocity import VelocityResult
from shared.models import ForecastWeights, RiskSnapshot


@dataclass
class RiskAssessment:
    score: float
    velocity_score: float
    similarity_score: float
    baseline_score: float
    trend: str
    level: str | None
    eta_low: float | None
    eta_high: float | None
    weights: dict

    def as_dict(self) -> dict:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def similarity_component(sim: SimilarityResult, drift: DriftResult) -> float:
    d = 100.0 * drift.score
    if sim.signatures_known == 0 or not sim.available:
        return d
    return float(0.7 * sim.score + 0.3 * d)


def combine(vel: VelocityResult, sim: SimilarityResult, base: BaselineResult, drift: DriftResult, w: ForecastWeights) -> tuple[float, float, float, float]:
    vs, bs = float(vel.score), float(base.score)
    ss = similarity_component(sim, drift)
    total = w.w_velocity + w.w_similarity + w.w_baseline
    risk = (w.w_velocity * vs + w.w_similarity * ss + w.w_baseline * bs) / total
    return float(np.clip(risk, 0, 100)), vs, ss, bs


def level_for(score: float, warning: int, critical: int) -> str | None:
    return "critical" if score >= critical else "warning" if score >= warning else None


def trend_for(db: Session, service_id: uuid.UUID, score: float) -> str:
    prev = [r.risk_score for r in db.query(RiskSnapshot.risk_score).filter(RiskSnapshot.service_id == service_id).order_by(RiskSnapshot.timestamp.desc()).limit(3)]
    if not prev:
        return "steady"
    delta = score - float(np.mean(prev))
    return "rising" if delta >= 4 else "falling" if delta <= -4 else "steady"


def estimate_eta(vel: VelocityResult, failure_level: float, risk: float, warning: int) -> tuple[float, float] | None:
    """Minutes until the error rate is expected to reach the level at which past incidents became
    user-visible. Exponential extrapolation when growth is exponential, linear otherwise."""
    if risk < warning * 0.85:
        return None
    cur = max(vel.errors_per_min, 0.01)
    if cur >= failure_level:
        return (0.0, 3.0)  # already at/over the failure level: impact is imminent or underway
    t: float | None = None
    if vel.growth_rate > 0.02:
        t = float(np.log((failure_level + 1.0) / (cur + 1.0)) / vel.growth_rate)
    if (t is None or t > 240) and vel.velocity > 0.05:
        t = (failure_level - cur) / vel.velocity
    if t is None or t <= 0 or t > 240:
        return None
    return (round(max(1.0, 0.75 * t), 1), round(max(2.0, 1.3 * t), 1))


def assess(db: Session, service_id: uuid.UUID, vel: VelocityResult, sim: SimilarityResult, base: BaselineResult, drift: DriftResult,
           w: ForecastWeights, warning: int, critical: int, failure_level: float, now: datetime) -> RiskAssessment:
    risk, vs, ss, bs = combine(vel, sim, base, drift, w)
    eta = estimate_eta(vel, failure_level, risk, warning)
    return RiskAssessment(
        score=risk, velocity_score=vs, similarity_score=ss, baseline_score=bs, trend=trend_for(db, service_id, risk),
        level=level_for(risk, warning, critical), eta_low=eta[0] if eta else None, eta_high=eta[1] if eta else None,
        weights={"velocity": round(w.w_velocity, 3), "similarity": round(w.w_similarity, 3), "baseline": round(w.w_baseline, 3)},
    )
