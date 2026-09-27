"""LogPilot Agent API Server (FastAPI): REST + WebSocket, fronting the agent's tool router.

Endpoints cover: log upload, search, health-state queries, agent chat, RCA, incident reports,
forecasting risk scores, and alert/policy management (PRD 7.2). OpenAPI docs at /docs.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import anyio
from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.auth.seed import seed
from app.config import API_PREFIX, SERVICE_NAME
from app.middleware import observability
from app.routers import alerts, apikeys, audit, auth, chat, deployments, feed, forecasting, glossary, health, logs, metrics, policy, projects, rca, reports, search, system, users
from app.websocket import hub, router as ws_router
from shared.config import settings
from shared.utils.db import SessionLocal, init_db
from shared.utils.logsetup import setup_logging

setup_logging(SERVICE_NAME)
log = logging.getLogger("logpilot.gateway")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        seed(db)
        db.commit()
    finally:
        db.close()
    anyio.to_thread.current_default_thread_limiter().total_tokens = 200  # many concurrent supervisors + slow AI calls
    await hub.start()
    yield
    await hub.stop()


app = FastAPI(
    title="LogPilot Agent API",
    version="2.0.0",
    description="Cloud-native, autonomous log-intelligence agent: ingestion, search, chat, RCA, reports, proactive failure forecasting and autonomy policy.",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=False, allow_methods=["*"], allow_headers=["*"], expose_headers=["Content-Disposition", "X-Request-ID"])
app.middleware("http")(observability)

api = APIRouter(prefix=API_PREFIX)
for r in (auth, users, projects, logs, search, health, forecasting, alerts, chat, rca, reports, deployments, policy, feed, glossary, audit, metrics, system, apikeys):
    api.include_router(r.router)
app.include_router(api)
app.include_router(ws_router)
app.include_router(system.router)  # /health at the root for load balancers


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "internal server error"}, status_code=500)
