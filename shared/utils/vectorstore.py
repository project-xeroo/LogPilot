"""Managed vector store client (Qdrant) - semantic search, clustering, forecasting similarity.

Collections
  log_embeddings      one point per distinct message *template* (payload: project, service, severity...)
  failure_signatures  learned leading-indicator signatures ("the agent's vector memory")
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from shared.config import settings

log = logging.getLogger("logpilot.vectors")

LOGS = "log_embeddings"
SIGNATURES = "failure_signatures"
NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

_client: QdrantClient | None = None
_ensured: set[str] = set()


def client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key, timeout=30, prefer_grpc=False)
    return _client


def point_id(*parts: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, "|".join(parts))


def ensure_collection(name: str, dim: int | None = None) -> None:
    dim = dim or settings.embedding_dim
    if name in _ensured:
        return
    c = client()
    existing = {col.name for col in c.get_collections().collections}
    if name not in existing:
        c.create_collection(
            collection_name=name,
            vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
            hnsw_config=qm.HnswConfigDiff(m=16, ef_construct=100),
        )
        for field, schema in (
            ("project_id", qm.PayloadSchemaType.KEYWORD),
            ("service", qm.PayloadSchemaType.KEYWORD),
            ("severity", qm.PayloadSchemaType.KEYWORD),
            ("is_error", qm.PayloadSchemaType.BOOL),
            ("service_id", qm.PayloadSchemaType.KEYWORD),
        ):
            try:
                c.create_payload_index(name, field_name=field, field_schema=schema)
            except Exception:  # pragma: no cover
                pass
    else:
        info = c.get_collection(name)
        size = info.config.params.vectors.size  # type: ignore[union-attr]
        if size != dim:
            raise RuntimeError(
                f"Vector collection '{name}' has dimension {size} but EMBEDDING_DIM={dim}. "
                "Re-create the collection or align EMBEDDING_DIM with the embedding model."
            )
    _ensured.add(name)


def _filter(must: dict[str, Any] | None = None, must_not: dict[str, Any] | None = None) -> qm.Filter | None:
    conds = []
    for k, v in (must or {}).items():
        if v is None:
            continue
        if isinstance(v, (list, tuple, set)):
            conds.append(qm.FieldCondition(key=k, match=qm.MatchAny(any=[str(x) if not isinstance(x, bool) else x for x in v])))
        elif isinstance(v, bool):
            conds.append(qm.FieldCondition(key=k, match=qm.MatchValue(value=v)))
        else:
            conds.append(qm.FieldCondition(key=k, match=qm.MatchValue(value=str(v))))
    nots = [
        qm.FieldCondition(key=k, match=qm.MatchValue(value=str(v))) for k, v in (must_not or {}).items() if v is not None
    ]
    if not conds and not nots:
        return None
    return qm.Filter(must=conds or None, must_not=nots or None)


def upsert(collection: str, points: list[tuple[uuid.UUID | str, list[float], dict]], batch: int = 256) -> None:
    ensure_collection(collection)
    c = client()
    for i in range(0, len(points), batch):
        chunk = points[i : i + batch]
        c.upsert(
            collection_name=collection,
            points=[qm.PointStruct(id=str(pid), vector=vec, payload=payload) for pid, vec, payload in chunk],
            wait=True,
        )


def search(
    collection: str,
    vector: list[float],
    *,
    limit: int = 10,
    must: dict[str, Any] | None = None,
    must_not: dict[str, Any] | None = None,
    score_threshold: float | None = None,
    with_vectors: bool = False,
) -> list[dict]:
    ensure_collection(collection)
    res = client().query_points(
        collection_name=collection,
        query=vector,
        limit=limit,
        query_filter=_filter(must, must_not),
        score_threshold=score_threshold,
        with_payload=True,
        with_vectors=with_vectors,
    )
    return [{"id": str(p.id), "score": p.score, "payload": p.payload or {}, "vector": p.vector} for p in res.points]


def retrieve(collection: str, ids: list[str | uuid.UUID], with_vectors: bool = True) -> dict[str, dict]:
    if not ids:
        return {}
    ensure_collection(collection)
    out: dict[str, dict] = {}
    c = client()
    for i in range(0, len(ids), 500):
        pts = c.retrieve(collection, ids=[str(x) for x in ids[i : i + 500]], with_payload=True, with_vectors=with_vectors)
        for p in pts:
            out[str(p.id)] = {"vector": p.vector, "payload": p.payload or {}}
    return out


def set_payload(collection: str, ids: list[str | uuid.UUID], payload: dict) -> None:
    if ids:
        client().set_payload(collection, payload=payload, points=[str(i) for i in ids], wait=True)


def delete(collection: str, ids: list[str | uuid.UUID]) -> None:
    if ids:
        client().delete(collection, points_selector=qm.PointIdsList(points=[str(i) for i in ids]), wait=True)


def count(collection: str) -> int:
    ensure_collection(collection)
    return client().count(collection, exact=False).count


def healthy() -> bool:
    try:
        client().get_collections()
        return True
    except Exception:
        return False
