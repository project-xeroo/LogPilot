# api-gateway

The **Agent API server** (FastAPI): REST + WebSocket, JWT auth with configurable expiry, RBAC + project scoping on every endpoint, and the agent's **tool router**.

* `app/auth/` JWT principal, `require(permission)`, `project_perm(permission)`, first-run seed (org, policies, glossary, demo users)
* `app/agent/` `tool_router.py` (RBAC → autonomy policy → execute → audit), `loop.py` (perceive-reason-act chat), `slots.py` (intent + slot extraction)
* `app/routers/` one module per screen/capability: auth, users, projects, logs (500MB streamed upload), search, health, forecasting, alerts (approvals, outcomes), chat, rca, reports, deployments, policy (+settings, webhooks), feed, glossary, audit, metrics, system
* `app/websocket/` Redis event bus → WebSocket fan-out; streamed chat at `/ws`

Run: `PYTHONPATH=../.. uvicorn app.main:app --port 8000` · Docs: http://localhost:8000/docs · Tests: `PYTHONPATH=../.. pytest tests`
