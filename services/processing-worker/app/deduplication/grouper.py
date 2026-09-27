"""Error deduplication (tool 06).

Repeated errors collapse into single grouped events - with occurrence counts, first/last seen and
affected services - using *semantic similarity* rather than exact text equality, so minor
variations of the same error ("timed out after 5s" vs "timeout reached (5000ms)") group together.

Algorithm: greedy nearest-canonical assignment on embedding cosine similarity.
  * existing canonicals (one per dedup_events row) are loaded from the vector store
  * new error templates are processed most-frequent-first; each joins the most similar canonical
    at or above DEDUP_THRESHOLD, otherwise becomes a new canonical itself
  * group aggregates are then recomputed from member templates
"""
from __future__ import annotations

import logging
import uuid

import numpy as np
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from shared.config import settings
from shared.models import DedupEvent, LogTemplate
from shared.utils import vectorstore

log = logging.getLogger("logpilot.dedup")


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.clip(n, 1e-12, None)


def deduplicate_project(db: Session, project_id: uuid.UUID, threshold: float | None = None) -> dict:
    threshold = threshold if threshold is not None else settings.dedup_threshold
    pending = db.execute(
        select(LogTemplate)
        .where(LogTemplate.project_id == project_id, LogTemplate.is_error.is_(True), LogTemplate.dedup_id.is_(None),
               LogTemplate.embedded_at.is_not(None))
        .order_by(LogTemplate.occurrence_count.desc())
    ).scalars().all()
    if not pending:
        _refresh_aggregates(db, project_id)
        return {"new_templates": 0, "groups_created": 0, "merged": 0}

    events = db.execute(select(DedupEvent).where(DedupEvent.project_id == project_id)).scalars().all()
    vecs = vectorstore.retrieve(vectorstore.LOGS, [t.id for t in pending] + [e.canonical_id for e in events])

    canon_ids: list[uuid.UUID] = []  # dedup_event ids aligned with canon_mat rows
    canon_rows: list[np.ndarray] = []
    for e in events:
        pt = vecs.get(str(e.canonical_id))
        if pt and pt["vector"] is not None:
            canon_ids.append(e.id)
            canon_rows.append(np.asarray(pt["vector"], dtype=np.float32))
    canon_mat = _unit(np.vstack(canon_rows)) if canon_rows else np.zeros((0, settings.embedding_dim), dtype=np.float32)

    created = merged = 0
    for t in pending:
        pt = vecs.get(str(t.id))
        if pt is None or pt["vector"] is None:
            continue
        v = _unit(np.asarray(pt["vector"], dtype=np.float32))
        target: uuid.UUID | None = None
        if len(canon_mat):
            sims = canon_mat @ v
            j = int(np.argmax(sims))
            if sims[j] >= threshold:
                target = canon_ids[j]
        if target is None:
            ev = DedupEvent(
                project_id=project_id, canonical_id=t.id, canonical_message=t.template,
                occurrence_count=t.occurrence_count, first_seen=t.first_seen, last_seen=t.last_seen,
                affected_services=t.services or [], variant_count=1,
            )
            db.add(ev)
            db.flush()
            target = ev.id
            canon_ids.append(ev.id)
            canon_mat = np.vstack([canon_mat, v[None, :]]) if len(canon_mat) else v[None, :]
            created += 1
        else:
            merged += 1
        t.dedup_id = target
    db.flush()
    _refresh_aggregates(db, project_id)
    return {"new_templates": len(pending), "groups_created": created, "merged": merged}


def _refresh_aggregates(db: Session, project_id: uuid.UUID) -> None:
    """Recompute counts / first / last seen / services / variants of every group from member templates."""
    db.execute(
        text(
            """
            UPDATE dedup_events d SET
              occurrence_count = a.occ, first_seen = a.first_seen, last_seen = a.last_seen,
              variant_count = a.variants, affected_services = a.services
            FROM (
              SELECT t.dedup_id AS id, SUM(t.occurrence_count) AS occ, MIN(t.first_seen) AS first_seen,
                     MAX(t.last_seen) AS last_seen, COUNT(*) AS variants,
                     (SELECT COALESCE(jsonb_agg(DISTINCT s), '[]'::jsonb)
                        FROM log_templates t2, LATERAL jsonb_array_elements_text(COALESCE(t2.services, '[]'::jsonb)) s
                       WHERE t2.dedup_id = t.dedup_id) AS services
              FROM log_templates t
              WHERE t.project_id = :p AND t.dedup_id IS NOT NULL
              GROUP BY t.dedup_id
            ) a
            WHERE d.id = a.id AND d.project_id = :p
            """
        ),
        {"p": project_id},
    )


def group_stats(db: Session, project_id: uuid.UUID) -> dict:
    n_events, total = db.execute(
        select(func.count(DedupEvent.id), func.coalesce(func.sum(DedupEvent.occurrence_count), 0)).where(DedupEvent.project_id == project_id)
    ).one()
    return {"groups": n_events, "collapsed_occurrences": int(total)}
