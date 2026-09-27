# LogPilot AI Agent: architecture overview

LogPilot is specified as a **single autonomous agent**, not a multi-module product. It watches log streams, keeps
running state per service, and acts inside a policy the organization controls. People supervise it; they don't operate it.

```
                                   ┌──────────────────────────────────────────────────────────────┐
  browser ──HTTPS/WSS──► console ──►  api-gateway   REST + WebSocket · JWT · RBAC · tool router     │
   (React, static)        (nginx)  │                 perceive → reason → act loop (chat)             │
                                   └──┬───────────┬──────────────┬───────────────┬────────────────┘
                                      │           │              │               │
                    upload/stream     │      search/chat/RCA   risk/alerts     webhooks/in-app     audit
                                      ▼           ▼              ▼               ▼                   ▼
                        ┌─────────────────┐ ┌───────────┐ ┌──────────────┐ ┌───────────────┐ ┌────────────┐
                        │ log-ingestion   │ │ ai-service│ │ forecasting  │ │ notification  │ │ audit      │
                        │ validate·REDACT │ │ PII gate →│ │ scheduler +  │ │ dedupe · rate │ │ hash-chain │
                        │ ·store·parse    │ │ cloud LLM │ │ risk loop    │ │ signed hooks  │ │ export·undo│
                        └───────┬─────────┘ └─────┬─────┘ └──────┬───────┘ └───────────────┘ └────────────┘
                                │ Celery          │ embeddings   │ Celery
                                ▼                 ▼              ▼
                        ┌────────────────────────────────────────────────┐
                        │ processing-worker: embed → dedup → cluster →   │
                        │ anomaly detection → deployment comparison      │
                        └────────────────────────────────────────────────┘
        managed data plane:  PostgreSQL(+TimescaleDB) · Redis (broker, results, event bus, audit stream)
                             Qdrant vector store · S3-compatible object storage · cloud LLM/embedding API
```

## Components (PRD Section 12.2)

| Component | Role | Notes |
|---|---|---|
| `api-gateway` | Agent API server. REST + WebSocket, JWT auth, RBAC, project scoping, the **tool router**, the chat perceive-reason-act loop | Fans Redis events out to WebSockets, streams uploads straight through to ingestion |
| `log-ingestion-service` | Tool 01/02/03/04. Upload (500MB), API and NDJSON-stream ingestion; validation; **redaction before storage**; parsing | API + a Celery worker on the `ingestion` queue |
| `processing-worker` | Tools 06/07/13/12 + embeddings. Runs the enrichment pipeline as a Celery chain with per-step retries | `processing` queue |
| `ai-service` | Embeddings, keyword/semantic search, chat, RCA, reports (+PDF/Markdown), risk explanations | Every call to a model passes the `GuardedProvider` |
| `forecasting-service` | The flagship loop: velocity, drift, leading indicators, baselines, weighted risk score, alert lifecycle, feedback learning | API + workers + a singleton beat scheduler |
| `notification-service` | In-app real-time + HMAC-signed webhooks, dedupe, rate limits, SSRF guard | |
| `audit-service` | Hash-chained audit log, agent-action audit, export, reversal | Redis Streams consumer group |
| `console` | React supervisory console (static build, CDN-ready) | |

## The perceive → reason → act loop (PRD 8.1)

* **Perceive**: log streams, deployment events (detected in logs or registered via API) and human feedback (outcomes, ratings).
* **Reason**: per-service running state (baseline, velocity, signatures, risk history); cloud reasoning models interpret new signals against it.
* **Act**: surface information (read-only), draft/notify within policy, propose an action for approval. Nothing consequential is executed by the agent in this release: recommended actions are **propose-only** and autonomous execution is future scope.

### Autonomy model (PRD 8.2 / 8.3)

Tiers: read-only · autonomous-background · autonomous-policy-bounded · propose-only · autonomous-execution (future).
`shared/utils/autonomy.py::evaluate()` is the single decision point. An action outside the granted tier, below the
policy's minimum confidence, or flagged *requires approval* is **downgraded to propose-only and routed to a human queue**
(never blocked). Every autonomous or approved action lands in `agent_actions` with tool, trigger, autonomy level, confidence
and approver, and the reversible ones can be undone from the audit view. PII redaction is mandatory and outside policy.

## Proactive Failure Forecasting (PRD 4)

Each monitored service is scored every 60s (configurable per service). Three layers feed a weighted 0-100 risk score:

`risk = 0.30 × velocity + 0.40 × similarity + 0.30 × baseline deviation` (weights adapt per service from outcome feedback)

* **Velocity**: rolling per-window error counts with first/second derivatives (quadratic fit) and exponential growth rate.
* **Similarity**: cosine match of the service's current error signature to *learned* pre-failure signatures in the vector store (70%), blended with **pattern drift** (30%: centroid shift of clusters + new-but-related error variants).
* **Baseline deviation**: robust z-score and ratio against the service's normal for this day-of-week/hour (incident minutes excluded), plus Jensen-Shannon divergence of the error mix.
* At ≥60 (warning) / ≥80 (critical) the deep model writes a natural-language alert (probability, matched pattern, similar past events and what resolved them, ETA, ranked actions). At critical it also runs an autonomous RCA and drafts a **pre-mortem** for human sign-off.
* **Learning**: humans log outcomes (prevented / occurred / false positive). The agent moves the component weights, strengthens or adds positive signatures (with the action that worked) or negative signatures for false positives, and raises the similarity threshold for that service.
* **Degradation**: if AI inference or the vector store is down, alerts fall back to threshold rules instead of going silent (`degraded=true`).

## Data flow for an upload

`console → gateway (streams body) → ingestion` validates (type, size, binary, zip-bomb) → **redacts every line** →
writes the *redacted* text to object storage → enqueues `ingestion.parse_session`. The worker parses (JSON/NDJSON/CSV, Apache/Nginx,
syslog 3164/5424, generic + custom regex; stack traces folded) in **one transaction**, upserts message templates, discovers services and
deployment versions, then hands off to `processing.run_pipeline`. Embeddings are computed per distinct *template*, not per record, so AI cost
scales with the number of distinct message shapes rather than raw volume.

## Security model (PRD 10.3)

RBAC on every endpoint · JWT with configurable expiry · AES-256 at rest via cloud KMS · TLS in transit · **PII redaction before storage and
before egress** (enforced in `GuardedProvider`, not policy-configurable) · outbound calls limited to an explicit AI-provider allowlist ·
network isolation and least-privilege IAM (see `infra/`) · audit log for all user and agent actions with tamper evidence.

## Scale

Elastic workers (HPA, or KEDA on queue depth), managed vector store sized for 10M+ vectors, `log_records` as a time-partitioned hypertable with
GIN full-text and trigram indexes, 100+ services forecast concurrently (each cycle takes tens of milliseconds; the budget is 60s).
