# LogPilot AI Agent

An autonomous, cloud-native agent that watches production log streams, explains incidents in plain language, and
**forecasts failures before they happen**. People supervise it; it proposes, they decide.

Built from `LogPilot_AI_Agent_PRD_v2.docx`. Every PRD section maps to code below.

## Run it

```bash
cp .env.example .env            # optional: defaults work; AI_PROVIDER=mock runs fully offline
make up                         # = docker compose up -d --build   (first build takes a few minutes)
python scripts/generate_sample_logs.py --out data/sample-logs
```

Open **http://localhost:8080**, sign in with one of the demo accounts (`admin@`, `sre@`, `dev@`, `junior@`, `viewer@logpilot.local`, password in `.env.example`),
click **Upload logs**, choose `data/sample-logs/logpilot-demo-logs.zip`. About a minute later the agent will have parsed ~85k multi-format records,
redacted the PII, clustered the errors, flagged a bad deployment, learned the two past outages, and raised a **pre-incident alert** for the outage
that is building right now. Ask it *"Why is redis-cache flagged?"*.

* API + OpenAPI docs: http://localhost:8000/docs · Qdrant: http://localhost:6333/dashboard
* Ports already taken (Postgres 5432, Redis 6379…)? Override in `.env`: `POSTGRES_PORT=55432 REDIS_PORT=56379 …`
* `make e2e` runs ~90 assertions against the running stack through the public API (RBAC, pipeline, search, chat, forecasting, approvals, reports, policy, audit).

**Your `test code/docker-compose.yml`** (TimescaleDB + Redis, unchanged) is now the infrastructure layer: I extended it with a Qdrant vector store and
SeaweedFS (S3-compatible; MinIO no longer publishes container images) and the root `docker-compose.yml` `include`s it. Keep that folder; the stack depends on it.

### Use a real cloud model

```env
AI_PROVIDER=openai_compatible       # IBM Bob or any OpenAI-compatible endpoint
AI_BASE_URL=https://<provider-host>/v1
AI_API_KEY=...
AI_FAST_MODEL=...  AI_DEEP_MODEL=...  AI_EMBEDDING_MODEL=...   EMBEDDING_DIM=<model's vector size>
```

Outbound calls are restricted to that host (plus `AI_EGRESS_ALLOWLIST`), and every prompt and embedding input is PII-redacted first.
Recreate the Qdrant collections if you change `EMBEDDING_DIM`.

### Develop without Docker for the app layer

Start the infra (`docker compose -f "test code/docker-compose.yml" up -d`), then per service:
`pip install ./shared -r <deps>` and `PYTHONPATH=. uvicorn app.main:app` (see each `services/*/README.md`); console: `make console-dev`.

## What was built, by PRD section

| PRD | Where |
|---|---|
| 4 Proactive Failure Forecasting (flagship) | `services/forecasting-service/` velocity · drift · indicators (signatures, feedback loop) · baseline · scoring · loop (scheduler, cycle, alerts) |
| 5 Tools 01-04 ingestion, parsing, PII redaction, storage | `services/log-ingestion-service/`, `shared/utils/redaction.py`, `shared/utils/db.py` |
| 5 Tools 05 search, 09 chat, 10 RCA, 11 reports | `services/ai-service/app/{search,chat,rca,reports}` |
| 5 Tools 06 dedup, 07 clustering, 13 anomalies, 12 deployment comparison | `services/processing-worker/`, `shared/utils/analytics.py` |
| 5 Tool 08 health-state | `shared/utils/analytics.py::health_state`, `GET /projects/{id}/health-state` |
| 6 AI architecture (fast/deep/embedding roles, request flows, latency targets) | `services/ai-service/app/providers/`, timeouts + fallbacks in `reports/`, `rca/` |
| 7 Stack & schema | `shared/models/` (all 11 tables from 7.3, plus supporting tables) |
| 8 Orchestration & autonomy | `services/api-gateway/app/agent/` (tool router, chat loop), `shared/utils/autonomy.py`, audit in `services/audit-service/` |
| 9 Frontend plan | `frontend/` (Agent Feed + chat, Risk Board, Alerts & Approvals, Search, Reports, Deployments, Settings; guided mode, glossary) |
| 10 Roles & permissions | `shared/config/roles.py` (+ enforcement in `app/auth`, `routers/alerts.py`) |
| 11 Non-functional | `docs/`, health checks, retry/backoff (`shared/utils/celery_factory.py`), degraded modes, success-metrics endpoint |
| 12 Success metrics | `GET /projects/{id}/metrics`, *Settings → Success metrics* |
| 13 Cloud deployment | `docker-compose.yml`, `infra/k8s/` (kustomize), `infra/terraform/` (VPC, EKS, RDS, ElastiCache, S3/KMS, Qdrant) |
| 14 Future scope | left out on purpose (Slack/PagerDuty, autonomous execution, cascade prediction, SSO, multi-tenant) |

Docs: `docs/architecture/overview.md`, `docs/runbooks/incident-response.md`, `docs/api/openapi-spec.yml`.

## Decisions worth knowing

* **Home screen.** The PRD says both "chat is the default landing surface" (9.1) and "Agent Feed is the default landing screen" (9.2). Home is the Agent Feed with the chat docked beside it, and answered questions appear in the same stream.
* **Roles.** Follows Section 10 literally: *Viewer* has no chat/upload/approvals; *Junior* is guided by default and cannot approve until promoted; an "Engineering Manager" is a Developer/SRE/Admin who reviews and signs off reports.
* **Approvals are recorded, not executed.** Recommended actions are propose-only; *autonomous execution* is future scope and rejected by the policy API.
* **No scikit-learn.** DBSCAN, silhouette and Isolation Forest are implemented in NumPy (`services/processing-worker/app/{clustering,anomaly}`): smaller images, and it runs on locked-down hosts.
* **Offline provider.** `AI_PROVIDER=mock` is deterministic (feature-hashing embeddings + rule-based composition from the same context a cloud model receives). It exists so the product works without a key; prose quality comes from a real model.
* **Time-series extension.** TimescaleDB hypertables are used when the extension exists (your compose image has it). Amazon RDS lacks it, so `infra/terraform/modules/rds` runs plain PostgreSQL (the app falls back to indexed tables); use Timescale Cloud for hypertables.
* **Webhook egress.** The PRD's "no other egress" and its webhook requirement pull in opposite directions; registered webhook destinations are Admin/SRE-approved and refuse private/loopback addresses.

## Verification status

**Verified on the build machine**

* 59 backend unit tests (ingestion 16, processing 7, forecasting 8, AI 8, gateway 20) and 8 frontend tests; TypeScript strict typecheck and production build.
* `scripts/e2e_smoke.py`: **98 checks pass against the real containerised stack, from empty volumes, through the nginx console proxy** (TimescaleDB with the `log_records` hypertable, Redis, Qdrant, SeaweedFS S3, all services, workers, scheduler). Covers RBAC, upload → parse → redact → embed → dedup → cluster → anomalies, search, all chat intents, RCA, forecasting alerts and approvals (incl. guided-mode/junior guardrails), outcome learning, reports (edit, sign-off, PDF/Markdown), deployment comparison, autonomy policy, audit hash-chain, reversal, metrics.
* The cloud-provider code path (OpenAI-compatible embeddings, chat, JSON tasks, SSE streaming) was exercised against a recording fake server: the provider received **no** email, card number, password or token, and a non-allowlisted host is blocked with nothing sent.

**Not verified**

* `terraform validate/plan` and `kubectl apply -k`: no Terraform binary or cluster was available. Treat `infra/terraform` and `infra/k8s` as reviewed-but-unrun; YAML parses and the Terraform is written against pinned provider/module versions.
* A live call to IBM Bob or any real hosted model: the provider is configured purely by env vars (`AI_BASE_URL`, models, key) and was only tested against the fake server above. Check the model names and `EMBEDDING_DIM` for your account.
* Browser-level visual QA was limited to the desktop layout (feed, risk board, alerts, deployments, policy) in dark theme; the phone/tablet layouts and light theme are implemented but were not reviewed on screen.
