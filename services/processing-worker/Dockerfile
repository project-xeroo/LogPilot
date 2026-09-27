# syntax=docker/dockerfile:1
# Build context is the repository root:  docker build -f services/processing-worker/Dockerfile .
FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl  && rm -rf /var/lib/apt/lists/*

# shared library (models, config, utilities)
COPY shared /tmp/shared
RUN pip install /tmp/shared && rm -rf /tmp/shared

# service dependencies come from pyproject.toml (single source of truth)
COPY services/processing-worker/pyproject.toml /tmp/svc/pyproject.toml
RUN python -c "import tomllib;print('\n'.join(tomllib.load(open('/tmp/svc/pyproject.toml','rb'))['project']['dependencies']))" > /tmp/svc/req.txt  && pip install -r /tmp/svc/req.txt && rm -rf /tmp/svc

COPY services/processing-worker/app /app/app

RUN useradd --system --uid 10001 --no-create-home logpilot && chown -R logpilot /app
USER logpilot

CMD ["celery", "-A", "app.main:celery_app", "worker", "-Q", "processing", "--loglevel", "INFO", "--concurrency", "4"]
