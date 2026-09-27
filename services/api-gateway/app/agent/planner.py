"""The agent's real reasoning step (PRD 8.1 "Reason"): the model itself decides which tools it needs -
zero, one, or several, one at a time, in whatever order the question calls for - instead of a fixed
rule mapping a question to exactly one tool. Each iteration asks the model for its next move via
ai-service's `/internal/agent/step` (native function-calling), executes whatever it asks for through
the same `ToolRouter.invoke` every other caller uses (RBAC, autonomy policy, audit - unchanged), and
feeds the result back until the model is ready to answer or `MAX_STEPS` is reached.

If the model never resolves (offline/mock provider, or a cloud outage), the loop degrades to whatever
tool results it already gathered - possibly none - exactly like every other AI-backed path here.
"""
from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.slots import is_meta_question
from app.agent.tool_router import TOOL_DEFS
from app.agent.tool_router import router as tools
from app.auth import Principal
from shared.models import MonitoredService
from shared.utils import clients
from shared.utils.analytics import clock_now
from shared.utils.clients import ServiceError

log = logging.getLogger("logpilot.agent.planner")

MAX_STEPS = 4

# tool name -> (tool_results key, card type). The key names match what loop.py's `_slim()` and
# ai_payload() already expect, so the composition step downstream is unchanged.
_RESULT_MAP: dict[str, tuple[str, str | None]] = {
    "log_search": ("search", "search"),
    "health_state": ("health", "health"),
    "root_cause_analysis": ("rca", "rca"),
    "incident_report_generation": ("report", "report"),
    "deployment_comparison": ("deployment", "deployment"),
    "proactive_failure_forecasting": ("risk", "risk"),
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "log_search",
        "description": "Search the project's logs. Use 'semantic' mode for meaning-based questions (e.g. "
                       "'errors like a timeout'), 'keyword' mode for an exact phrase or term. Returns matching "
                       "log lines with sources you can cite.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "What to search for"},
            "mode": {"type": "string", "enum": ["keyword", "semantic"]},
            "services": {"type": "array", "items": {"type": "string"}, "description": "Restrict to these service names"},
            "min_severity": {"type": "string", "enum": ["DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"]},
            "start": {"type": "string", "description": "ISO 8601 timestamp, start of the time window"},
            "end": {"type": "string", "description": "ISO 8601 timestamp, end of the time window"},
            "limit": {"type": "integer", "description": "Max results, default 8"},
        }, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "health_state",
        "description": "Current health snapshot for one service or the whole project: error rates, top "
                       "problems, trend. Use this for 'how is X doing' / 'what's the current status' questions.",
        "parameters": {"type": "object", "properties": {
            "service": {"type": "string"},
            "window_minutes": {"type": "integer", "description": "Lookback window in minutes, default 60"},
        }},
    }},
    {"type": "function", "function": {
        "name": "proactive_failure_forecasting",
        "description": "Failure risk scores and forecasts. Use this for ANY question about risk, likelihood of "
                       "failure, what's about to break, or what to keep an eye on.",
        "parameters": {"type": "object", "properties": {"service": {"type": "string"}}},
    }},
    {"type": "function", "function": {
        "name": "root_cause_analysis",
        "description": "Runs a causal-chain root cause analysis for a service over a time window. Use this when "
                       "asked WHY something failed or broke.",
        "parameters": {"type": "object", "properties": {
            "service": {"type": "string"},
            "start": {"type": "string", "description": "ISO 8601 timestamp"},
            "end": {"type": "string", "description": "ISO 8601 timestamp"},
        }},
    }},
    {"type": "function", "function": {
        "name": "deployment_comparison",
        "description": "Compares two deployed versions of a service and flags regressions. Use this for "
                       "questions about whether a release/deploy caused problems.",
        "parameters": {"type": "object", "properties": {
            "service": {"type": "string"},
            "from_version": {"type": "string"},
            "to_version": {"type": "string"},
        }, "required": ["service"]},
    }},
    {"type": "function", "function": {
        "name": "incident_report_generation",
        "description": "Drafts a document: an incident report, a pre-mortem for an open alert, or an executive "
                       "summary. Use this only when the user explicitly asks for a report, write-up or summary "
                       "document, not for a quick question.",
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["incident", "pre_mortem", "executive_summary"]},
            "service": {"type": "string"},
            "alert_id": {"type": "string"},
            "days": {"type": "integer"},
        }, "required": ["kind"]},
    }},
]

def _coerce_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Small models occasionally double-encode an array argument as a JSON string (e.g. `services:
    '["redis-cache"]'` instead of a real list) - the tool call still gets rejected by pydantic without
    this, wasting a whole extra round trip on a retry the model would get right anyway."""
    schema = next((s["function"]["parameters"].get("properties", {}) for s in TOOL_SCHEMAS if s["function"]["name"] == name), {})
    out = dict(args)
    for key, spec in schema.items():
        v = out.get(key)
        if spec.get("type") == "array" and isinstance(v, str):
            try:
                parsed = json.loads(v)
            except json.JSONDecodeError:
                parsed = [v]
            out[key] = parsed if isinstance(parsed, list) else [parsed]
    return out


_SYSTEM = (
    "You are the LogPilot Agent's planning step for project {project_id}. Current time (UTC): {now}. "
    "Monitored services: {services}. "
    "Decide which tools you need to ground your answer in real data - call zero, one, or several, one at a "
    "time, in whatever order makes sense; you are not limited to a single tool per question. There is no time "
    "pressure: take as many steps as you genuinely need, and prefer calling one more tool to confirm something "
    "over guessing. Only call a tool when its result would actually change your answer - not to pad out the "
    "investigation. Never invent service names, numbers, log content or version identifiers that no tool has "
    "given you: if you don't know a specific value a tool wants (e.g. a deployment version, an alert id), omit "
    "that field rather than guessing a plausible-looking one - most tools resolve a sensible default "
    "themselves (deployment_comparison compares the two most recent versions if you leave from_version/"
    "to_version out; incident_report_generation with kind=pre_mortem finds the right open alert itself if you "
    "give it only a service). Never call the same tool with the same arguments twice - reuse the result you "
    "already have instead. For questions about yourself, LogPilot's own capabilities, or general conversation "
    "that needs no live data, call no tools at all. When you have everything you need, reply normally with no "
    "further tool calls - a separate step composes the final answer from what you've gathered, so keep this "
    "reply brief."
)


def _catalogue(p: Principal) -> tuple[set[str], list[dict[str, Any]]]:
    allowed = {n for n, d in TOOL_DEFS.items() if p.can(d.permission)}
    return allowed, [s for s in TOOL_SCHEMAS if s["function"]["name"] in allowed]


_STEP_LABEL: dict[str, str] = {
    "log_search": "Searching the logs",
    "health_state": "Reading service health",
    "root_cause_analysis": "Tracing the root cause",
    "incident_report_generation": "Drafting the report",
    "deployment_comparison": "Comparing deployments",
    "proactive_failure_forecasting": "Checking failure risk",
}


def run(
    db: Session, p: Principal, project_id: uuid.UUID, text: str, history: list[dict[str, str]],
    on_step: Callable[[str], None] | None = None,
) -> tuple[dict[str, Any], list[dict], list[dict]]:
    """Returns (tool_results, cards, tool_calls_log). `on_step` (optional) is called with a short,
    human-readable line each time the model decides what to do next - "thinking" UI grounding, not just a
    generic spinner, for a loop that can genuinely take tens of seconds to minutes per step."""
    notify = on_step or (lambda _msg: None)
    if is_meta_question(text):
        # No tool returns "here is what I am" - every schema above hands back log/service data, never agent
        # self-description - so asking the model to plan tool calls for this is pure wasted latency, and in
        # practice it doesn't reliably decline anyway (small models call tools eagerly even when told not to).
        # loop.py always adds `agent_info` to tool_results regardless, so composition still has real grounding.
        return {}, [], []
    known = [n for (n,) in db.execute(select(MonitoredService.name).where(MonitoredService.project_id == project_id))]
    now = clock_now(db, project_id)
    allowed, schemas = _catalogue(p)

    messages: list[dict[str, Any]] = [{"role": "system", "content": _SYSTEM.format(
        project_id=project_id, now=now.isoformat(), services=", ".join(known) or "none yet - nothing has been ingested",
    )}]
    for h in (history or [])[-6:]:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": text})

    tool_results: dict[str, Any] = {}
    cards: list[dict] = []
    calls: list[dict] = []
    seen: dict[tuple[str, str], Any] = {}  # (tool, sorted-args-json) -> payload already returned this turn

    if not schemas:  # nothing this role is permitted to call - skip straight to composition
        return tool_results, cards, calls

    notify("Thinking about how to answer...")
    for _ in range(MAX_STEPS):
        try:
            # The planning call runs the deep model (see ai-service's /internal/agent/step) for quality, not
            # speed - it routinely takes 30-60s+, and ai-service's own retry-on-timeout loop can multiply a
            # single slow attempt by up to 3x, so this needs real headroom above that, not just one attempt's
            # worth.
            step = clients.ai.post("/internal/agent/step", json={"messages": messages, "tools": schemas}, actor=p.actor_headers(), timeout=300)
        except ServiceError as exc:
            log.warning("agent planning step failed (%s); composing from whatever was gathered so far", exc)
            notify("Having trouble reaching the reasoning model - answering with what I have so far...")
            break
        requested = step.get("tool_calls") or []
        if not requested:
            break
        if step.get("reasoning"):
            log.info("agent reasoning: %s", step["reasoning"][:500])
        notify(", ".join(dict.fromkeys(_STEP_LABEL.get(c["name"], c["name"]) for c in requested)) + "...")
        messages.append({"role": "assistant", "content": step.get("content"), "tool_calls": [
            {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["arguments"] or {})}}
            for c in requested
        ]})
        for c in requested:
            name, args = c["name"], _coerce_args(c["name"], c.get("arguments") or {})
            cache_key = (name, json.dumps(args, sort_keys=True, default=str))
            if cache_key in seen:
                # The model asked for something it already has this turn (despite being told not to) -
                # serve the cached result instead of re-invoking (RBAC/audit) or re-hitting the tool.
                payload = seen[cache_key]
            elif name not in allowed or name not in TOOL_DEFS:
                payload = {"error": "unknown or unauthorized tool"}
                calls.append({"tool": name, "ok": False, "error": payload["error"]})
                seen[cache_key] = payload
            else:
                try:
                    r = tools.invoke(db, p, name, project_id, args)
                    payload = r["result"]
                    calls.append({"tool": name, "ms": r["latency_ms"], "ok": True})
                    key, card_type = _RESULT_MAP.get(name, (name, None))
                    tool_results[key] = payload
                    if card_type:
                        cards.append({"type": card_type, "data": payload})
                except HTTPException as exc:
                    payload = {"error": exc.detail}
                    calls.append({"tool": name, "ok": False, "error": exc.detail})
                    tool_results.setdefault("errors", []).append({"tool": name, "error": exc.detail})
                seen[cache_key] = payload
            messages.append({"role": "tool", "tool_call_id": c["id"], "name": name, "content": json.dumps(payload, default=str)[:4000]})
    notify("Writing the answer...")
    return tool_results, cards, calls
