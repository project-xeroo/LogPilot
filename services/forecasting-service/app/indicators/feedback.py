"""Feedback loop & self-improvement (PRD 4.3): after every incident - prevented or not - engineers log
the outcome; the agent refines its pattern-matching weights and leading-indicator signatures.

Learning rule (per service, bounded so a handful of labels cannot wreck the model):
  * component weights move by lr * (label - predicted) * component_contribution, then are
    re-normalised to sum to 1 within [0.10, 0.60]. A false positive shrinks the components that drove it.
  * accurate alerts add/strengthen a positive signature (with the action that worked);
    false positives add a *negative* signature and raise the similarity threshold for that service.
"""
from __future__ import annotations

import json
import uuid
from datetime import timedelta

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.indicators.signatures import PRE_MINUTES, POST_MINUTES, _store_signature, window_signature
from shared.config import settings
from shared.models import ForecastWeights, IncidentOutcome, MonitoredService, PreIncidentAlert, RecommendedAction, RiskSnapshot
from shared.utils import vectorstore

LR = 0.08
W_MIN, W_MAX = 0.10, 0.60


def get_weights(db: Session, service_id: uuid.UUID) -> ForecastWeights:
    w = db.get(ForecastWeights, service_id)
    if w is None:
        w = ForecastWeights(service_id=service_id, w_velocity=settings.weight_velocity, w_similarity=settings.weight_similarity,
                            w_baseline=settings.weight_baseline, signature_threshold=0.80)
        db.add(w)
        db.flush()
    return w


def _normalise(v: np.ndarray) -> np.ndarray:
    """Exact projection onto {w : sum(w) = 1, W_MIN <= w_i <= W_MAX}: find the shift t with
    sum(clip(v + t)) = 1 (monotone in t, so bisection converges)."""
    v = np.asarray(v, dtype=float)
    lo_t, hi_t = -1.0, 1.0
    for _ in range(80):
        t = (lo_t + hi_t) / 2
        if np.clip(v + t, W_MIN, W_MAX).sum() < 1.0:
            lo_t = t
        else:
            hi_t = t
    return np.clip(v + (lo_t + hi_t) / 2, W_MIN, W_MAX)


def learn_from_outcome(db: Session, outcome_id: uuid.UUID) -> dict:
    o = db.get(IncidentOutcome, outcome_id)
    if o is None or o.learned:
        return {"skipped": True}
    alert = db.get(PreIncidentAlert, o.alert_id)
    ms = db.get(MonitoredService, alert.service_id)
    w = get_weights(db, ms.id)
    accurate = bool(o.alert_accurate) and o.outcome != "false_positive"

    snap = (db.query(RiskSnapshot).filter(RiskSnapshot.service_id == ms.id, RiskSnapshot.timestamp <= (alert.updated_at or alert.created_at))
            .order_by(RiskSnapshot.risk_score.desc()).limit(1).first())
    contrib = np.array([(snap.velocity_score if snap else 50), (snap.similarity_score if snap else 50), (snap.baseline_score if snap else 50)]) / 100.0
    predicted = (alert.peak_risk_score or alert.risk_score) / 100.0
    before = np.array([w.w_velocity, w.w_similarity, w.w_baseline])
    after = _normalise(before + LR * ((1.0 if accurate else 0.0) - predicted) * contrib)
    w.w_velocity, w.w_similarity, w.w_baseline = (float(x) for x in after)
    w.samples += 1
    if accurate:
        w.true_positives += 1
        w.signature_threshold = float(max(0.70, w.signature_threshold - 0.01))
    else:
        w.false_positives += 1
        w.signature_threshold = float(min(0.92, w.signature_threshold + 0.03))

    # leading-indicator signature from the window around the alert
    t0 = alert.created_at
    vec, top, feats = window_signature(db, ms.project_id, ms.name, t0 - timedelta(minutes=PRE_MINUTES), t0 + timedelta(minutes=POST_MINUTES))
    sig_note = "no error window"
    if vec is not None:
        actions = [a for a in [o.action_taken] if a] + [
            (r.edited_text or r.text) for r in db.query(RecommendedAction).filter(RecommendedAction.alert_id == alert.id, RecommendedAction.status.in_(["approved", "edited", "executed"]))
        ]
        near = vectorstore.search(vectorstore.SIGNATURES, vec.tolist(), limit=1, must={"project_id": str(ms.project_id), "service_id": str(ms.id)})
        if accurate and near and near[0]["score"] >= 0.95 and not near[0]["payload"].get("is_negative"):
            payload = near[0]["payload"]  # strengthen the existing signature instead of duplicating it
            merged = list(dict.fromkeys((payload.get("resolved_actions") or []) + actions))[:6]
            vectorstore.set_payload(vectorstore.SIGNATURES, [near[0]["id"]], {"resolved_actions": merged, "weight": min(float(payload.get("weight", 1.0)) + 0.1, 1.5)})
            db.execute(text("UPDATE failure_signatures SET weight = LEAST(weight + 0.1, 1.5), match_count = match_count + 1, resolved_actions = CAST(:a AS jsonb) WHERE id = :i"),
                       {"a": json.dumps(merged), "i": near[0]["id"]})
            sig_note = "strengthened existing signature"
        else:
            _store_signature(db, ms, vec, incident_start=t0, incident_end=None,
                             label=f"{'Confirmed' if accurate else 'False positive'}: {(top[0]['message'] if top else ms.name)[:80]}",
                             source="outcome", actions=actions, peak=alert.peak_risk_score, features=feats, negative=not accurate)
            sig_note = "added positive signature" if accurate else "added negative signature"
    o.learned = True
    db.commit()
    return {"accurate": accurate, "weights": {"velocity": w.w_velocity, "similarity": w.w_similarity, "baseline": w.w_baseline},
            "signature_threshold": w.signature_threshold, "signature": sig_note}
