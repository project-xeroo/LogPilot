"""In-app real-time channel: pushes to the event bus; the API gateway fans it out over WebSocket.
Proactive alerts and pre-mortems *interrupt* the console (interrupt=True), they are not just logged."""
from __future__ import annotations

from typing import Any

from shared.utils.events import publish_event


def deliver(event_type: str, alert: dict[str, Any], *, project_id: str, org_id: str, interrupt: bool) -> None:
    publish_event(event_type, alert, project_id=project_id, org_id=org_id, interrupt=interrupt)
