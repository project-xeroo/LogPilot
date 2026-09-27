"""Leading indicators (PRD 4.1): service-specific failure signatures learned from past incidents,
stored in the agent's vector memory and matched against current log patterns by similarity search.

A *signature* is the semantic fingerprint of the errors in the build-up to a past incident: the
weighted mean embedding of the service's error templates in the ~15 minutes before/around onset.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.baseline import detect_episodes
from shared.models import FailureSignature, ForecastWeights, MonitoredService
from shared.utils import vectorstore
from shared.utils.analytics import minute_series
from shared.utils.logtemplate import strip_metadata, template_id

PRE_MINUTES = 15
POST_MINUTES = 3
_RESOLUTION = re.compile(
    r"(operator action|manual(ly)?|runbook|autoscal|rolled back|rollback|restarted|restarting|scal(ed|ing) |failover|failed over|hotfix|flushed|evicted|increased pool)",
    re.I,
)


@dataclass
class SimilarityResult:
    score: float = 0.0  # 0-100
    best_match: float = 0.0  # cosine
    best_label: str | None = None
    best_negative: float = 0.0
    matches: list[dict] = field(default_factory=list)
    signatures_known: int = 0
    available: bool = True
    scope: str = "service"

    def as_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def _unit(v: np.ndarray) -> np.ndarray:
    return v / max(float(np.linalg.norm(v)), 1e-12)


def window_signature(db: Session, project_id: uuid.UUID, service: str, start: datetime, end: datetime):
    """Returns (unit vector | None, top_errors, features)."""
    rows = db.execute(
        text(
            """
            SELECT l.template_hash, count(*) c, max(t.sample_message) sample FROM log_records l
            LEFT JOIN log_templates t ON t.project_id = l.project_id AND t.template_hash = l.template_hash
            WHERE l.project_id = :p AND l.service = :s AND l.severity_num >= 40 AND l.timestamp BETWEEN :a AND :b
            GROUP BY 1 ORDER BY c DESC LIMIT 40
            """
        ),
        {"p": project_id, "s": service, "a": start, "b": end},
    ).all()
    if not rows:
        return None, [], {"errors": 0}
    ids = [template_id(project_id, r[0]) for r in rows]
    pts = vectorstore.retrieve(vectorstore.LOGS, ids)
    vec = np.zeros(len(next(iter(pts.values()))["vector"]), dtype=np.float32) if pts else None
    used = 0
    for r, i in zip(rows, ids, strict=True):
        pt = pts.get(str(i))
        if pt and pt["vector"] is not None:
            vec += _unit(np.asarray(pt["vector"], dtype=np.float32)) * float(r[1])
            used += 1
    top = [{"message": strip_metadata(r[2] or "", 200), "count": int(r[1])} for r in rows[:3]]
    feats = {"errors": int(sum(r[1] for r in rows)), "distinct_errors": len(rows)}
    if vec is None or used == 0:
        return None, top, feats
    return _unit(vec), top, feats


def match_signatures(db: Session, ms: MonitoredService, now: datetime, weights: ForecastWeights, window_minutes: int = 10) -> tuple[SimilarityResult, list[dict]]:
    """Compares the service's current error signature to learned pre-failure signatures.
    Returns (result, current_top_errors)."""
    res = SimilarityResult()
    try:
        vec, top_errors, _ = window_signature(db, ms.project_id, ms.name, now - timedelta(minutes=window_minutes), now)
        res.signatures_known = db.execute(
            text("SELECT count(*) FROM failure_signatures WHERE service_id = :s AND NOT is_negative"), {"s": ms.id}
        ).scalar() or 0
        if vec is None:
            return res, top_errors
        must = {"project_id": str(ms.project_id), "service_id": str(ms.id)}
        hits = vectorstore.search(vectorstore.SIGNATURES, vec.tolist(), limit=8, must=must)
        factor = 1.0
        if not hits:  # nothing service-specific yet: fall back to project-wide signatures, discounted
            hits = vectorstore.search(vectorstore.SIGNATURES, vec.tolist(), limit=8, must={"project_id": str(ms.project_id)})
            factor, res.scope = 0.85, "project"
    except Exception:  # vector memory unavailable -> similarity signal degrades to 0, loop keeps running
        res.available = False
        return res, []
    pos = [h for h in hits if not h["payload"].get("is_negative")]
    neg = [h for h in hits if h["payload"].get("is_negative")]
    if pos:
        best = max(pos, key=lambda h: h["score"] * min(float(h["payload"].get("weight", 1.0)), 1.5))
        res.best_match = float(best["score"]) * factor
        res.best_label = best["payload"].get("label")
    res.best_negative = max((h["score"] for h in neg), default=0.0)
    eff = res.best_match
    if res.best_negative >= res.best_match - 0.02 and res.best_negative > 0.75:
        eff *= 0.6  # this pattern was previously labelled a false positive
    thr = float(np.clip(weights.signature_threshold, 0.7, 0.95))
    res.score = float(100.0 * np.clip((eff - 0.5) / (thr - 0.5), 0, 1))
    res.matches = [
        {"signature_id": h["payload"].get("signature_id"), "label": h["payload"].get("label"),
         "incident_start": h["payload"].get("incident_start"), "similarity": round(float(h["score"]) * factor, 3),
         "resolved_actions": h["payload"].get("resolved_actions") or [], "peak_error_rate": h["payload"].get("peak_error_rate")}
        for h in sorted(pos, key=lambda h: -h["score"])[:3]
    ]
    return res, top_errors


def mine_resolution_actions(db: Session, project_id: uuid.UUID, start: datetime, end: datetime, limit: int = 4) -> list[str]:
    """Mines operational actions from the logs around an incident's recovery (historical resolution logs)."""
    rows = db.execute(
        text(
            "SELECT DISTINCT message FROM log_records WHERE project_id = :p AND severity_num < 40 AND timestamp BETWEEN :a AND :b "
            "AND message ~* :rx ORDER BY message LIMIT 30"
        ),
        {"p": project_id, "a": start, "b": end, "rx": _RESOLUTION.pattern},
    ).all()
    out, seen = [], set()
    for (m,) in rows:
        clean = re.sub(r"\b(request_id|trace_id|version|env)=\S+", "", m)
        clean = re.sub(r"^(operator action|autoscaler|runbook|manual action)\s*[:\-]\s*", "", clean, flags=re.I).strip(" .")
        clean = re.sub(r"\s*\((pid|id)[^)]*\)", "", clean).strip()
        key = re.sub(r"\d+", "#", clean.lower())
        if clean and key not in seen:
            seen.add(key)
            out.append(_imperative(clean[:160]))
    return out[:limit]


_VERBS = [
    (r"^restarted\b", "Restart"), (r"^restarting\b", "Restart"), (r"^scaling\b", "Scale"), (r"^scaled\b", "Scale"),
    (r"^increased\b", "Increase"), (r"^rolled back\b", "Roll back"), (r"^rolling back\b", "Roll back"), (r"^flushed\b", "Flush"),
    (r"^evicted\b", "Evict"), (r"^failed over\b", "Fail over"), (r"^applied\b", "Apply"), (r"^deployed\b", "Deploy"),
]


def _imperative(s: str) -> str:
    """'Restarted worker-2' -> 'Restart worker-2': recommended actions are phrased as steps to take."""
    for pat, verb in _VERBS:
        if re.match(pat, s, re.I):
            return verb + re.sub(pat, "", s, count=1, flags=re.I)
    return s[:1].upper() + s[1:]


def learn_history(db: Session, project_id: uuid.UUID) -> dict:
    """Detect past incident episodes per service and store their pre-failure signatures."""
    created = 0
    services = db.query(MonitoredService).filter(MonitoredService.project_id == project_id, MonitoredService.enabled).all()
    now = datetime.now(tz=None).astimezone()
    for ms in services:
        span = db.execute(text("SELECT min(timestamp), max(timestamp) FROM log_records WHERE project_id = :p AND service = :s"), {"p": project_id, "s": ms.name}).one()
        if not span[0]:
            continue
        end = span[1]
        s = minute_series(db, project_id, ms.name, span[0], end, ms.environment)
        for a, b in detect_episodes(s.errors):
            if len(s) - 1 - b <= 10 and s.errors[-3:].sum() > 0:
                continue  # still happening: not history yet
            start_t, end_t = s.minutes[a], s.minutes[b]
            exists = db.execute(
                text("SELECT 1 FROM failure_signatures WHERE service_id = :s AND abs(extract(epoch FROM (incident_start - :t))) < 1800 LIMIT 1"),
                {"s": ms.id, "t": start_t},
            ).first()
            if exists:
                continue
            vec, top, feats = window_signature(db, project_id, ms.name, start_t - timedelta(minutes=PRE_MINUTES), start_t + timedelta(minutes=POST_MINUTES))
            if vec is None:
                continue
            actions = mine_resolution_actions(db, project_id, end_t - timedelta(minutes=3), end_t + timedelta(minutes=12))
            label = (top[0]["message"] if top else "error build-up")[:90]
            _store_signature(db, ms, vec, incident_start=start_t, incident_end=end_t, label=f"{ms.name}: {label}", source="history",
                             actions=actions, peak=float(s.errors[a : b + 1].max()), features=feats)
            created += 1
    db.commit()
    _ = now
    return {"signatures_created": created}


def _store_signature(db: Session, ms: MonitoredService, vec: np.ndarray, *, incident_start: datetime, incident_end: datetime | None, label: str,
                     source: str, actions: list[str], peak: float | None, features: dict | None, negative: bool = False) -> FailureSignature:
    sig = FailureSignature(
        project_id=ms.project_id, service_id=ms.id, service_name=ms.name, incident_start=incident_start, incident_end=incident_end,
        label=label[:300], source=source, is_negative=negative, weight=1.0, features=features, resolved_actions=actions, peak_error_rate=peak,
    )
    db.add(sig)
    db.flush()
    vectorstore.upsert(vectorstore.SIGNATURES, [(sig.id, vec.tolist(), {
        "project_id": str(ms.project_id), "service_id": str(ms.id), "service_name": ms.name, "label": sig.label, "signature_id": str(sig.id),
        "incident_start": incident_start.isoformat(), "resolved_actions": actions, "is_negative": negative, "weight": 1.0,
        "peak_error_rate": peak, "source": source,
    })])
    return sig


def failure_level(db: Session, service_id: uuid.UUID) -> float:
    """Error rate (errors/min) at which past incidents became user-visible for this service."""
    peaks = [p for (p,) in db.execute(text("SELECT peak_error_rate FROM failure_signatures WHERE service_id = :s AND NOT is_negative AND peak_error_rate IS NOT NULL"), {"s": service_id})]
    return float(max(8.0, 0.6 * float(np.median(peaks)))) if peaks else 12.0
