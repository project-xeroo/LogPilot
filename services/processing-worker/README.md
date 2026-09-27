# processing-worker

The async enrichment pipeline (Celery, `processing` queue), triggered per upload as a chain with per-step retry + exponential backoff:

`embed_templates → deduplicate → cluster → detect_anomalies → finalize`

* `embeddings/` per-template embeddings via the AI service into the vector store
* `deduplication/` semantic grouping of repeated errors (canonical + counts + first/last seen + affected services)
* `clustering/` density-based (NumPy DBSCAN) clusters, auto-labelled, confidence-scored, tracked over time (`cluster_history` feeds pattern drift)
* `anomaly/` robust-z statistics + Isolation Forest: error/latency/traffic spikes, new error types, service silences, each explained
* `tasks/deployment.py` autonomous deployment comparison when a new version appears

Run: `celery -A app.main:celery_app worker -Q processing`
