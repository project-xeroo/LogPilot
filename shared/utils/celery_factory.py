"""Celery app factory: managed Redis as broker + result backend, retry with exponential backoff."""
from __future__ import annotations

from celery import Celery

from shared.config import settings

QUEUE_INGESTION = "ingestion"
QUEUE_PROCESSING = "processing"
QUEUE_FORECASTING = "forecasting"

# Task-name prefix -> queue. Services enqueue by *name* so no service imports another's code.
ROUTES = {
    "ingestion.*": {"queue": QUEUE_INGESTION},
    "processing.*": {"queue": QUEUE_PROCESSING},
    "deployment.*": {"queue": QUEUE_PROCESSING},
    "forecasting.*": {"queue": QUEUE_FORECASTING},
}

# Failed processing jobs retry with exponential backoff (PRD 10.4).
RETRY_KWARGS = dict(
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
)


def make_celery(name: str, include: list[str] | None = None, beat_schedule: dict | None = None) -> Celery:
    app = Celery(name, broker=settings.celery_broker_url, backend=settings.celery_result_backend, include=include or [])
    app.conf.update(
        task_routes=ROUTES,
        task_default_queue=QUEUE_PROCESSING,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        result_expires=3600,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        broker_connection_retry_on_startup=True,
        beat_schedule=beat_schedule or {},
    )
    return app
