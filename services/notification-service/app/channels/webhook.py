"""Webhook channel: HMAC-signed JSON POSTs to org-registered endpoints, with retry + SSRF protection.

Webhook destinations are registered by an Admin/SRE (that registration is the approval of the
destination). Because the security model is "tightly governed egress", private/loopback/link-local/
metadata addresses are refused unless WEBHOOK_ALLOW_PRIVATE=true (dev only)."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import time
from urllib.parse import urlparse

import httpx

from shared.config import settings
from shared.models.base import utcnow

log = logging.getLogger("logpilot.notify.webhook")
RETRIES = 3


class WebhookRejected(ValueError):
    pass


def validate_url(url: str) -> None:
    p = urlparse(url)
    if p.scheme not in ("https", "http") or not p.hostname:
        raise WebhookRejected("webhook URL must be http(s)")
    if p.scheme == "http" and not settings.webhook_allow_private and settings.environment == "prod":
        raise WebhookRejected("webhook URL must use https in production")
    if settings.webhook_allow_private:
        return
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise WebhookRejected(f"cannot resolve webhook host: {exc}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or str(ip) == "169.254.169.254":
            raise WebhookRejected(f"webhook host resolves to a non-public address ({ip}); refused")


def sign(secret: str | None, body: bytes) -> str | None:
    return None if not secret else "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def deliver(url: str, secret: str | None, event_type: str, payload: dict) -> tuple[bool, int | None, str | None, int]:
    """Returns (ok, status_code, error, attempts). Exponential backoff between attempts."""
    body = json.dumps({"event": event_type, "sent_at": utcnow().isoformat(), **payload}, default=str).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "LogPilot-Agent/2.0", "X-LogPilot-Event": event_type}
    sig = sign(secret, body)
    if sig:
        headers["X-LogPilot-Signature"] = sig
    last_err, status = None, None
    for attempt in range(1, RETRIES + 1):
        try:
            validate_url(url)  # re-checked on every attempt (DNS rebinding)
            r = httpx.post(url, content=body, headers=headers, timeout=settings.webhook_timeout_seconds, follow_redirects=False)
            status = r.status_code
            if 200 <= status < 300:
                return True, status, None, attempt
            last_err = f"HTTP {status}"
            if 400 <= status < 500 and status != 429:
                break  # client error: retrying will not help
        except (httpx.HTTPError, WebhookRejected) as exc:
            last_err = str(exc)
            if isinstance(exc, WebhookRejected):
                break
        if attempt < RETRIES:
            time.sleep(min(2**attempt * 0.5, 8))
    return False, status, last_err, attempt
