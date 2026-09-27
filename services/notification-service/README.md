# notification-service

Alert delivery: in-app real-time (Redis event bus → WebSocket, marked `interrupt`) and **HMAC-signed webhooks** with retry, dedupe, per-service rate limits and SSRF protection. Slack/PagerDuty are future scope.

Run: `uvicorn app.main:app --port 8004`
