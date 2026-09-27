"""Processing worker entrypoint.

    celery -A app.main:celery_app worker -Q processing --concurrency 4 --loglevel INFO

Async processing pipeline (PRD 7.2): embedding generation, deduplication, cluster assignment and
anomaly detection - triggered on upload - plus autonomous deployment comparison.
"""
from __future__ import annotations

from celery.signals import worker_process_init

from app.tasks import deployment, pipeline
from shared.utils import vectorstore
from shared.utils.celery_factory import make_celery
from shared.utils.db import init_db
from shared.utils.logsetup import setup_logging

setup_logging("processing-worker")
celery_app = make_celery("processing-worker")
pipeline.register(celery_app)
deployment.register(celery_app)


@worker_process_init.connect
def _init(**_):
    init_db()
    try:
        vectorstore.ensure_collection(vectorstore.LOGS)
    except Exception:  # vector store may still be starting; tasks retry with backoff
        pass


if __name__ == "__main__":  # pragma: no cover
    celery_app.worker_main(["worker", "-Q", "processing", "--loglevel", "INFO"])
