# Runbook: operating LogPilot and responding to what it tells you

## 1. When the agent raises an alert

1. Open **Alerts and approvals** (or the interrupt banner). Read the agent's headline, then *Why the agent is saying this*: score drivers, what is failing, similar past incidents and what resolved them.
2. Decide on each **proposed action**: approve, edit, or dismiss (a reason is required). The agent never executes actions; approval records the decision for your team to carry out.
3. Work the incident. If useful, ask the agent in chat: "What is the root cause?", "What should we do about redis-cache?".
4. **Log the outcome** on the alert (prevented / incident happened / false alarm, what was done, minutes to resolve). This is how the agent improves: weights and leading-indicator signatures are updated from it.
5. Review the drafted **pre-mortem** (critical) or ask for an incident report; edit and sign off before exporting.

Junior Engineers can read and discuss every action but cannot approve or dismiss; those decisions go to a Developer, SRE or Admin until an SRE/Admin promotes the account.

## 2. Degraded modes (nothing should go silent)

| Symptom | What LogPilot does | What you do |
|---|---|---|
| AI provider unreachable / slow | Forecast alerts fall back to threshold rules (marked "Threshold-based"); chat answers are composed from retrieved evidence with lower confidence; reports use a data-driven draft | Check *Settings → AI provider* usage and errors; verify egress allowlist and credentials |
| Vector store down | Similarity and drift signals drop to 0; velocity + baseline still score; semantic search returns 503 (keyword search still works) | Restore Qdrant; signatures are in the store, so verify collections `log_embeddings` and `failure_signatures` |
| Redis down | Workers retry with exponential backoff; real-time updates pause; audit writes fall back to direct DB writes | Restore Redis; queued work resumes |
| Worker backlog | Sessions stay in `processing`; upload progress shows the stage | Scale `processing-worker`/`ingestion-worker`; check queue length in Redis |
| Postgres failover | Services reconnect (pool pre-ping) | Confirm multi-AZ failover completed |

`GET /api/v1/system/health` (Admin/SRE) aggregates every component; `GET /health` on each service is for probes.

## 3. Reversing an autonomous action

*Settings → Autonomy and policy → Audit trail → Agent actions → Reverse*. Withdraws an alert (and dismisses its undecided proposals), discards a drafted report, rejects an RCA result, returns an action to the queue, or withdraws a regression flag. The original entry stays, marked reversed. `Verify` recomputes the audit hash chain.

## 4. Tuning

* Too noisy? Raise the warning threshold (Settings → thresholds) globally or per service; log *false alarm* outcomes so the agent raises that service's similarity threshold.
* Too quiet? Lower thresholds or the scoring interval; make sure history exists (the agent learns leading indicators from past incidents in uploaded logs).
* Stricter in production: add a per-environment override of a tool's autonomy tier (e.g. `pre_incident_alerts` → propose-only in `production`).

## 5. Operations

* **Secrets**: rotate `JWT_SECRET` (signs users out), `INTERNAL_API_TOKEN` (service-to-service), provider key. Never commit `.env`.
* **Backups**: managed database snapshots (RDS/Timescale Cloud), S3 versioning, Qdrant volume snapshots. Restore order: Postgres → vector store → object storage.
* **Changing the embedding model**: set `EMBEDDING_DIM`, recreate the two Qdrant collections, then re-run the pipeline (embeddings are re-created per template; signatures re-learn from history).
* **Upgrades**: rolling (`maxUnavailable: 0`); the forecasting scheduler uses `Recreate` so only one beat runs.
* **Demo data**: `make sample-logs` then upload `data/sample-logs/logpilot-demo-logs.zip`; `make e2e` exercises the whole product over the API.
