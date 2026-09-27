# ai-service

Everything that talks to a model. **All egress goes through `providers/guarded.py`**: PII redaction on every message, context and embedding input, then an egress allowlist check.

* `providers/` OpenAI-compatible cloud client (IBM Bob or equivalent), offline heuristic provider, registry + JSON retry/fallback
* `search/` keyword (full-text, exact phrase, regex, trigram-indexed) and semantic search with filters, source refs and context windows
* `chat/` retrieval-grounded answers with citations + confidence, log explanations, suggested prompts, SSE streaming
* `rca/` temporal correlation + trace-precedence dependency inference + LLM reasoning → causal chain, confidence, evidence
* `reports/` incident / pre-mortem / executive-summary drafting (hard timeouts with data-driven fallback) and PDF/Markdown export
* `prompts/` prompt templates for cloud models

Run: `uvicorn app.main:app --port 8002`
