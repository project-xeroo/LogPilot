"""Pattern drift detection (PRD 4.1).

Uses embeddings to catch what keyword monitoring cannot: when error clusters' centroids start to
shift, or new error types appear that are *semantically related to but not identical with* earlier
errors, the system is entering failure modes it hasn't fully expressed yet.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from shared.config import settings
from shared.utils import vectorstore


@dataclass
class DriftResult:
    score: float = 0.0  # 0..1
    cluster_shift: float = 0.0  # mean centroid shift of the service's clusters over the last hour
    drifting_variants: list[dict] = field(default_factory=list)
    available: bool = True

    def as_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def _unit(v: np.ndarray) -> np.ndarray:
    return v / max(float(np.linalg.norm(v)), 1e-12)


def detect_drift(db: Session, project_id: uuid.UUID, service: str, now: datetime, recent_minutes: int = 30) -> DriftResult:
    res = DriftResult()
    # 1) centroid shift of clusters this service participates in (cluster_history is appended on each clustering run)
    shift = db.execute(
        text(
            """
            SELECT avg(h.drift_from_previous) FROM cluster_history h JOIN error_clusters c ON c.id = h.cluster_id
            WHERE c.project_id = :p AND c.services ? :s AND h.timestamp >= :t AND h.drift_from_previous > 0
            """
        ),
        {"p": project_id, "s": service, "t": now - timedelta(hours=1)},
    ).scalar()
    res.cluster_shift = float(shift or 0.0)

    # 2) new-but-related variants: recent error templates vs everything seen before them
    cut = now - timedelta(minutes=recent_minutes)
    rows = db.execute(
        text(
            """
            SELECT id, template, occurrence_count, first_seen FROM log_templates
            WHERE project_id = :p AND is_error AND services ? :s AND embedded_at IS NOT NULL
            """
        ),
        {"p": project_id, "s": service},
    ).all()
    recent = [r for r in rows if r.first_seen >= cut]
    prior = [r for r in rows if r.first_seen < cut]
    try:
        if recent and prior:
            vecs = vectorstore.retrieve(vectorstore.LOGS, [r.id for r in rows])
            P = np.vstack([_unit(np.asarray(vecs[str(r.id)]["vector"], dtype=np.float32)) for r in prior if str(r.id) in vecs])
            related_weight = total_weight = 0.0
            for r in recent:
                pt = vecs.get(str(r.id))
                if not pt:
                    continue
                s = float((P @ _unit(np.asarray(pt["vector"], dtype=np.float32))).max())
                total_weight += r.occurrence_count
                if 0.50 <= s < settings.dedup_threshold:  # related but not identical -> a new failure expression
                    related_weight += r.occurrence_count
                    res.drifting_variants.append({"template": r.template[:200], "similarity_to_known": round(s, 3), "occurrences": r.occurrence_count})
            frac = related_weight / total_weight if total_weight else 0.0
            n = len(res.drifting_variants)
            variant_component = float(np.clip(frac, 0, 1) * min(1.0, n / 2.0))
        else:
            variant_component = 0.0
    except Exception:  # vector store hiccup: drift is one of several signals
        res.available = False
        variant_component = 0.0
    shift_component = float(np.clip(res.cluster_shift / 0.15, 0, 1))
    res.score = float(np.clip(0.6 * variant_component + 0.4 * shift_component, 0, 1))
    return res
