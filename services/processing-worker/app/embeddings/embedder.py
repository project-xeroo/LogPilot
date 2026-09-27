"""Embedding generation (cloud embedding API via the AI service) for message templates.

Embedding is per distinct *template* rather than per record, so cost and latency scale with the
number of distinct message shapes, not raw log volume (PRD cost metric: AI API cost per 1,000
events stays flat as volume grows). Target: < 5s per 1,000 records/templates in batch."""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from shared.models import LogTemplate
from shared.models.base import utcnow
from shared.utils import vectorstore
from shared.utils.clients import embed_texts

log = logging.getLogger("logpilot.embeddings")
BATCH = 500


def embed_pending_templates(db: Session, project_id: uuid.UUID, max_templates: int = 50_000) -> dict:
    rows = db.execute(
        select(LogTemplate)
        .where(LogTemplate.project_id == project_id, LogTemplate.embedded_at.is_(None))
        .order_by(LogTemplate.occurrence_count.desc())
        .limit(max_templates)
    ).scalars().all()
    embedded = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i : i + BATCH]
        vectors = embed_texts([_text_for(t) for t in chunk])
        vectorstore.upsert(
            vectorstore.LOGS,
            [
                (
                    t.id,
                    vec,
                    {
                        "project_id": str(project_id),
                        "template_hash": t.template_hash,
                        "is_error": bool(t.is_error),
                        "severity": t.max_severity,
                        "services": t.services or [],
                        "template": t.template[:300],
                        "sample": t.sample_message[:300],
                    },
                )
                for t, vec in zip(chunk, vectors, strict=True)
            ],
        )
        db.execute(
            update(LogTemplate).where(LogTemplate.id.in_([t.id for t in chunk])).values(embedding_id=LogTemplate.id, embedded_at=utcnow())
        )
        db.commit()
        embedded += len(chunk)
    return {"embedded": embedded}


def _text_for(t: LogTemplate) -> str:
    return t.template[:400]
