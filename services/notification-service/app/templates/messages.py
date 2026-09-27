"""Message templates for outbound notifications (webhook payload bodies)."""
from __future__ import annotations

from string import Template
from typing import Any

TITLES = {
    "alert.created": Template("[${level}] ${service}: ${risk}/100 failure risk"),
    "alert.escalated": Template("[ESCALATED to CRITICAL] ${service}: ${risk}/100 failure risk"),
    "alert.pending_review": Template("[Needs review] ${service}: ${risk}/100 failure risk"),
    "alert.resolved": Template("[Resolved] ${service}: risk subsided"),
}


def render(event_type: str, alert: dict[str, Any]) -> dict[str, Any]:
    tmpl = TITLES.get(event_type, Template("[${level}] ${service}"))
    title = tmpl.safe_substitute(level=str(alert.get("level", "")).upper(), service=alert.get("service", "service"), risk=int(alert.get("risk_score", 0)))
    eta = ""
    if alert.get("eta_minutes_low"):
        eta = f" Estimated time to impact: {alert['eta_minutes_low']:.0f}-{alert['eta_minutes_high']:.0f} minutes."
    actions = alert.get("actions") or []
    md = f"**{title}**\n\n{alert.get('text', '')}{eta}\n" + ("\nProposed actions (approval required):\n" + "\n".join(f"- {a}" for a in actions) if actions else "")
    return {"title": title, "text": alert.get("text", ""), "markdown": md}
