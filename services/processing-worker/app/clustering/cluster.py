"""Error clustering (tool 07): density-based clustering of error-template embeddings.

* DBSCAN (cosine metric) over error templates, weighted by occurrence so a hot error is a core point.
* Each cluster is auto-labelled and confidence-scored (weighted mean similarity to its centroid).
* Cluster identity is carried across runs by centroid matching, and every run appends a
  `cluster_history` row (centroid, size, drift) - that history is the *pattern-drift* signal the
  forecasting loop consumes.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.clustering.dbscan import dbscan, silhouette_cosine
from app.clustering.labeler import label_cluster
from shared.config import settings
from shared.models import ClusterHistory, ErrorCluster, LogTemplate
from shared.models.base import utcnow
from shared.utils import vectorstore

log = logging.getLogger("logpilot.cluster")
MAX_TEMPLATES = 20_000
MAX_SINGLETONS = 200


@dataclass
class _Group:
    idx: list[int]
    centroid: np.ndarray
    confidence: float
    occurrence: int
    services: list[str]
    first_seen: object
    last_seen: object


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.clip(n, 1e-12, None)


def cluster_project(db: Session, project_id: uuid.UUID) -> dict:
    templates = db.execute(
        select(LogTemplate)
        .where(LogTemplate.project_id == project_id, LogTemplate.is_error.is_(True), LogTemplate.embedded_at.is_not(None))
        .order_by(LogTemplate.occurrence_count.desc())
        .limit(MAX_TEMPLATES)
    ).scalars().all()
    if not templates:
        return {"clusters": 0, "templates": 0}
    pts = vectorstore.retrieve(vectorstore.LOGS, [t.id for t in templates])
    templates = [t for t in templates if pts.get(str(t.id)) and pts[str(t.id)]["vector"] is not None]
    if not templates:
        return {"clusters": 0, "templates": 0}
    X = _unit(np.vstack([np.asarray(pts[str(t.id)]["vector"], dtype=np.float32) for t in templates]))
    occ = np.array([max(t.occurrence_count, 1) for t in templates], dtype=np.float64)
    weights = np.ones(len(templates))  # density over *distinct* message shapes; occurrence only weights centroids

    labels = dbscan(X, eps=settings.cluster_eps, min_samples=settings.cluster_min_samples, sample_weight=weights)

    groups: list[_Group] = []
    for lab in sorted(set(labels) - {-1}):
        groups.append(_make_group(templates, X, occ, [i for i, l in enumerate(labels) if l == lab], singleton=False))
    noise = [i for i, l in enumerate(labels) if l == -1]  # sorted by occurrence already
    for i in noise[:MAX_SINGLETONS]:
        groups.append(_make_group(templates, X, occ, [i], singleton=True))

    # silhouette over the density-based (non-noise) assignment
    mask = labels != -1
    silhouette = silhouette_cosine(X[mask], labels[mask]) if mask.sum() >= 3 else None

    existing = db.execute(select(ErrorCluster).where(ErrorCluster.project_id == project_id)).scalars().all()
    matches = _match(existing, groups)
    now = utcnow()
    seen_existing: set[uuid.UUID] = set()
    created = updated = 0
    for gi, g in enumerate(groups):
        members = [templates[i] for i in g.idx]
        cl = matches.get(gi)
        if cl is None:
            top = sorted(members, key=lambda t: -t.occurrence_count)
            cl = ErrorCluster(
                project_id=project_id,
                label=label_cluster([(t.template, t.occurrence_count) for t in top], g.services),
                centroid_embedding=g.centroid.tolist(), confidence=g.confidence, member_count=len(members),
                occurrence_count=g.occurrence, services=g.services, first_seen=g.first_seen, last_seen=g.last_seen,
                drift_score=0.0, status="active",
            )
            db.add(cl)
            db.flush()
            drift = 0.0
            created += 1
        else:
            old = np.asarray(cl.centroid_embedding, dtype=np.float32)
            drift = float(max(0.0, 1.0 - float(_unit(old) @ g.centroid)))
            cl.centroid_embedding = g.centroid.tolist()
            cl.confidence, cl.member_count, cl.occurrence_count = g.confidence, len(members), g.occurrence
            cl.services, cl.first_seen, cl.last_seen = g.services, g.first_seen, g.last_seen
            cl.drift_score, cl.status, cl.updated_at = drift, "active", now
            updated += 1
        seen_existing.add(cl.id)
        db.add(ClusterHistory(
            cluster_id=cl.id, project_id=project_id, timestamp=now, centroid_embedding=g.centroid.tolist(),
            member_count=len(members), drift_from_previous=drift, silhouette=silhouette,
        ))
        db.execute(update(LogTemplate).where(LogTemplate.id.in_([m.id for m in members])).values(cluster_id=cl.id))
    for cl in existing:
        if cl.id not in seen_existing:
            cl.status = "dormant"
    db.commit()
    return {"clusters": len(groups), "created": created, "updated": updated, "templates": len(templates),
            "noise": len(noise), "silhouette": silhouette}


def _make_group(templates, X, occ, idx: list[int], singleton: bool) -> _Group:
    w = occ[idx]
    centroid = _unit((X[idx] * w[:, None]).sum(axis=0) / w.sum())
    sims = X[idx] @ centroid
    confidence = 0.5 if singleton else float(np.clip((sims * w).sum() / w.sum(), 0.0, 1.0))
    services = sorted({s for i in idx for s in (templates[i].services or [])})
    return _Group(
        idx=idx, centroid=centroid.astype(np.float32), confidence=round(confidence, 4), occurrence=int(w.sum()),
        services=services, first_seen=min(templates[i].first_seen for i in idx), last_seen=max(templates[i].last_seen for i in idx),
    )


def _match(existing: list[ErrorCluster], groups: list[_Group]) -> dict[int, ErrorCluster]:
    """Greedy one-to-one matching of new groups to existing clusters by centroid cosine similarity."""
    if not existing or not groups:
        return {}
    E = _unit(np.vstack([np.asarray(c.centroid_embedding, dtype=np.float32) for c in existing]))
    G = np.vstack([g.centroid for g in groups])
    sims = G @ E.T
    pairs = sorted(((sims[i, j], i, j) for i in range(len(groups)) for j in range(len(existing))), reverse=True)
    used_g, used_e, out = set(), set(), {}
    for s, gi, ej in pairs:
        if s < settings.cluster_match_threshold:
            break
        if gi in used_g or ej in used_e:
            continue
        out[gi] = existing[ej]
        used_g.add(gi)
        used_e.add(ej)
    return out
