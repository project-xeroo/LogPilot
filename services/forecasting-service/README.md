# forecasting-service

The flagship **Proactive Failure Forecasting** loop.

* `velocity/` error velocity + acceleration per service · `drift/` pattern drift via embeddings · `indicators/` learned failure signatures + feedback learning
* `baseline/` normal behaviour per service per day-of-week/hour · `scoring/` 30/40/30 weighted risk, trend, ETA
* `loop/` `scheduler.py` (Celery beat tick + tasks), `cycle.py` (one perceive-reason-act cycle: score → explain → alert → pre-mortem), alert lifecycle with policy checks

Processes: API `uvicorn app.main:app --port 8003` · worker `celery -A app.loop.scheduler:celery_app worker -Q forecasting` · scheduler `celery -A app.loop.scheduler:celery_app beat` (Windows: run worker and beat separately)
