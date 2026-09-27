# audit-service

Hash-chained audit log for user/system events (Redis Streams consumer group), query/export (CSV/JSONL, streamed), chain verification, and **reversal of agent actions** (`handlers/reverts.py`).

Run: `uvicorn app.main:app --port 8005`
