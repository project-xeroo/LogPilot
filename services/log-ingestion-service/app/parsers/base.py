"""Parser primitives: normalized record, context, timestamp parsing, key=value extraction."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from dateutil import parser as dateparser

from shared.models.base import normalize_severity

FILE_MARKER = "\x1eLOGPILOT-FILE\x1e"  # inserted between files of a multi-file upload (zip)
MAX_MESSAGE = 8000


@dataclass
class ParsedRecord:
    timestamp: datetime
    message: str
    severity: str = "INFO"
    service: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    environment: str | None = None
    deployment_version: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    line_no: int = 0


@dataclass
class Malformed:
    line_no: int
    raw: str
    reason: str


@dataclass
class ParseContext:
    default_service: str | None = None
    environment: str | None = None
    base_time: datetime = field(default_factory=lambda: datetime.now(UTC))  # for year-less syslog timestamps
    custom_pattern: str | None = None


# ---------------------------------------------------------------------------------------------------
_MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
_ISO_FIX = re.compile(r",(\d{1,6})")


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def parse_timestamp(value: Any, ctx: ParseContext | None = None) -> datetime | None:
    """Best-effort timestamp parsing; naive values are treated as UTC. Returns None if unparseable."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, datetime):
            return _aware(value)
        if isinstance(value, (int, float)) or (isinstance(value, str) and re.fullmatch(r"\d{9,19}(\.\d+)?", value.strip())):
            n = float(value)
            if n > 1e17:  # ns
                n /= 1e9
            elif n > 1e14:  # us
                n /= 1e6
            elif n > 1e11:  # ms
                n /= 1e3
            return datetime.fromtimestamp(n, UTC)
        s = str(value).strip()
        # apache: 10/Oct/2026:13:55:36 +0000
        m = re.fullmatch(r"(\d{1,2})/([A-Za-z]{3})/(\d{4}):(\d{2}):(\d{2}):(\d{2})(?: ([+-]\d{4}))?", s)
        if m:
            d, mon, y, hh, mm, ss, tz = m.groups()
            dt = datetime(int(y), _MONTHS[mon.title()], int(d), int(hh), int(mm), int(ss))
            if tz:
                off = timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5])) * (1 if tz[0] == "+" else -1)
                return (dt - off).replace(tzinfo=UTC)
            return dt.replace(tzinfo=UTC)
        # syslog 3164: Sep 27 10:00:00 (no year)
        m = re.fullmatch(r"([A-Z][a-z]{2})\s+(\d{1,2}) (\d{2}):(\d{2}):(\d{2})", s)
        if m:
            mon, d, hh, mm, ss = m.groups()
            base = (ctx.base_time if ctx else datetime.now(UTC))
            dt = datetime(base.year, _MONTHS[mon], int(d), int(hh), int(mm), int(ss), tzinfo=UTC)
            if dt > base + timedelta(days=1):  # log from "last year"
                dt = dt.replace(year=dt.year - 1)
            return dt
        # nginx error: 2026/09/27 10:00:00
        m = re.fullmatch(r"(\d{4})/(\d{2})/(\d{2}) (\d{2}):(\d{2}):(\d{2})", s)
        if m:
            return datetime(*(int(x) for x in m.groups()), tzinfo=UTC)
        s2 = _ISO_FIX.sub(lambda mm_: "." + mm_.group(1), s)
        try:
            return _aware(datetime.fromisoformat(s2.replace("Z", "+00:00")))
        except ValueError:
            return _aware(dateparser.parse(s2))
    except (ValueError, OverflowError, KeyError, TypeError):
        return None


# ---- key=value extraction (request_id, trace_id, env, version, latency ...) ---------------------------
_KV = re.compile(
    r"(?<![\w.-])(?P<k>request[_-]?id|req[_-]?id|x-request-id|rid|trace[_-]?id|traceid|trace|env|environment|"
    r"deployment[_-]?version|app[_-]?version|version|release|build|service|svc|"
    r"latency[_-]?ms|duration[_-]?ms|elapsed[_-]?ms|took[_-]?ms|response[_-]?time[_-]?ms|request[_-]?time|rt|status|user[_-]?id)"
    r"\s*[=:]\s*\"?(?P<v>[^\s,;\"\]\)}]+)",
    re.I,
)
_LATENCY_TEXT = re.compile(r"(?:took|latency|duration|elapsed|in|response time)[=: ]+(\d+(?:\.\d+)?)\s?(ms|s)\b", re.I)
_KEY_MAP = {
    "request_id": "request_id", "requestid": "request_id", "req_id": "request_id", "reqid": "request_id",
    "x-request-id": "request_id", "rid": "request_id",
    "trace_id": "trace_id", "traceid": "trace_id", "trace": "trace_id",
    "env": "environment", "environment": "environment",
    "deployment_version": "deployment_version", "app_version": "deployment_version", "version": "deployment_version",
    "release": "deployment_version", "build": "deployment_version",
    "service": "service", "svc": "service",
}
_LATENCY_KEYS = {"latency_ms", "duration_ms", "elapsed_ms", "took_ms", "response_time_ms"}


def extract_fields(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    attrs: dict[str, Any] = {}
    for m in _KV.finditer(text):
        key = m.group("k").lower().replace("-", "_") if m.group("k").lower() != "x-request-id" else "x-request-id"
        val = m.group("v")
        if key in _KEY_MAP:
            out.setdefault(_KEY_MAP[key], val)
        elif key in _LATENCY_KEYS or key.replace("-", "_") in _LATENCY_KEYS:
            _set_num(attrs, "latency_ms", val, 1.0)
        elif key in ("request_time", "rt"):
            _set_num(attrs, "latency_ms", val, 1000.0)  # nginx: seconds
        elif key == "status":
            attrs.setdefault("status", val)
        elif key in ("user_id", "userid"):
            attrs.setdefault("user_id", val)
    if "latency_ms" not in attrs:
        m = _LATENCY_TEXT.search(text)
        if m:
            _set_num(attrs, "latency_ms", m.group(1), 1000.0 if m.group(2).lower() == "s" else 1.0)
    if attrs:
        out["attributes"] = attrs
    return out


def _set_num(attrs: dict, key: str, val: str, mult: float) -> None:
    try:
        attrs.setdefault(key, round(float(val) * mult, 3))
    except ValueError:
        pass


_ERR_WORDS = re.compile(r"\b(fatal|panic|exception|traceback|critical|error|failed|failure|refused|timeout|timed out|unavailable|denied|crash)", re.I)
_WARN_WORDS = re.compile(r"\b(warn|warning|retry|retrying|deprecated|slow|degraded)", re.I)


def infer_severity(message: str) -> str:
    if _ERR_WORDS.search(message):
        return "ERROR"
    if _WARN_WORDS.search(message):
        return "WARN"
    return "INFO"


def finalize(rec: ParsedRecord, ctx: ParseContext) -> ParsedRecord:
    """Fill service/environment defaults, pull request/trace ids from the message, clamp size."""
    rec.message = rec.message.strip()[:MAX_MESSAGE] or "(empty message)"
    fields = extract_fields(rec.message)
    for k in ("request_id", "trace_id", "environment", "deployment_version"):
        if getattr(rec, k) is None and fields.get(k):
            setattr(rec, k, str(fields[k])[:200])
    if not rec.service and fields.get("service"):
        rec.service = fields["service"]
    for k, v in (fields.get("attributes") or {}).items():
        rec.attributes.setdefault(k, v)
    rec.service = (rec.service or ctx.default_service or "unknown")[:200]
    rec.environment = rec.environment or ctx.environment
    rec.severity = normalize_severity(rec.severity)
    return rec
