# log-ingestion-service

Tools 01-04: **ingestion, parsing, PII redaction, structured storage.**

* Upload `.log .txt .json .csv .zip .gz` up to 500MB, JSON batch (`/ingest/records`), NDJSON stream (`/ingest/stream`)
* `app/validators` type/size/binary/archive-bomb checks; errors are surfaced to the caller and on the session
* `app/redaction` streaming redaction (engine: `shared/utils/redaction.py`). Only **redacted** text is written to object storage
* `app/parsers` JSON/NDJSON/arrays, CSV, Apache/Nginx (access+error), syslog 3164/5424, generic + custom regex; multi-line stack traces fold into one record; malformed lines are stored separately
* `app/storage` bulk inserts, template upserts, service and deployment-version discovery
* `app/worker.py` Celery (`ingestion` queue): parse in one transaction (retry-safe), then hands off to `processing.run_pipeline`

Run API: `uvicorn app.main:app --port 8001` · Worker: `celery -A app.worker:celery_app worker -Q ingestion`
