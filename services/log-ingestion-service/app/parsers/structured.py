"""Structured formats: JSON (NDJSON, arrays, pretty-printed objects) and CSV."""
from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Iterator
from typing import Any

from app.parsers.base import FILE_MARKER, Malformed, ParseContext, ParsedRecord, finalize, infer_severity, parse_timestamp

_TS_KEYS = ["@timestamp", "timestamp", "ts", "time", "datetime", "date", "@t", "eventTime", "log_time", "created_at", "asctime"]
_MSG_KEYS = ["message", "msg", "@m", "log", "text", "error", "event", "body", "description", "exception"]
_LVL_KEYS = ["level", "severity", "lvl", "log_level", "loglevel", "@l", "levelname", "priority"]
_SVC_KEYS = ["service", "service_name", "serviceName", "app", "application", "component", "logger", "logger_name", "container_name", "kubernetes.container_name", "source", "program"]
_REQ_KEYS = ["request_id", "requestId", "req_id", "x-request-id", "x_request_id", "correlation_id", "correlationId"]
_TRACE_KEYS = ["trace_id", "traceId", "trace.id", "traceid", "dd.trace_id", "otel.trace_id", "trace"]
_ENV_KEYS = ["environment", "env", "deployment.environment"]
_VER_KEYS = ["deployment_version", "deploymentVersion", "version", "app_version", "release", "build", "service.version"]
_USED = set(_TS_KEYS + _MSG_KEYS + _LVL_KEYS + _SVC_KEYS + _REQ_KEYS + _TRACE_KEYS + _ENV_KEYS + _VER_KEYS)


def _flatten(d: dict, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
            out.setdefault(key, v)
        else:
            out[key] = v
    return out


def _pick(flat: dict[str, Any], keys: list[str]) -> Any:
    lower = {k.lower(): v for k, v in flat.items()}
    for k in keys:
        if k in flat and flat[k] not in (None, ""):
            return flat[k]
        v = lower.get(k.lower())
        if v not in (None, ""):
            return v
    return None


def record_from_mapping(obj: dict[str, Any], line_no: int, ctx: ParseContext) -> ParsedRecord | None:
    flat = _flatten(obj)
    ts = parse_timestamp(_pick(flat, _TS_KEYS), ctx)
    if ts is None:
        return None
    msg = _pick(flat, _MSG_KEYS)
    if isinstance(msg, (dict, list)):
        msg = json.dumps(msg, default=str)
    if msg is None:
        return None
    lvl = _pick(flat, _LVL_KEYS)
    svc = _pick(flat, _SVC_KEYS)
    attrs = {k: v for k, v in obj.items() if k not in _USED and not isinstance(v, (dict, list)) and v is not None}
    # keep a bounded amount of nested context too
    for k, v in obj.items():
        if isinstance(v, dict) and k not in _USED and len(json.dumps(v, default=str)) < 1000:
            attrs[k] = v
    for lat in ("latency_ms", "duration_ms", "response_time_ms", "elapsed_ms", "took_ms"):
        if lat in flat and isinstance(flat[lat], (int, float)):
            attrs["latency_ms"] = float(flat[lat])
            break
    rec = ParsedRecord(
        timestamp=ts,
        message=str(msg),
        severity=lvl if lvl is not None else infer_severity(str(msg)),
        service=str(svc) if svc is not None else None,
        request_id=_s(_pick(flat, _REQ_KEYS)),
        trace_id=_s(_pick(flat, _TRACE_KEYS)),
        environment=_s(_pick(flat, _ENV_KEYS)),
        deployment_version=_s(_pick(flat, _VER_KEYS)),
        attributes=attrs,
        line_no=line_no,
    )
    return finalize(rec, ctx)


def _s(v: Any) -> str | None:
    return None if v is None else str(v)[:200]


def iter_json_objects(lines: Iterable[tuple[int, str]]) -> Iterator[tuple[int, Any]]:
    """Yield (line_no, obj | Exception). Handles NDJSON, JSON arrays (one object per line or pretty
    printed) by brace-matching outside strings; never loads the whole file."""
    buf: list[str] = []
    depth = 0
    in_str = esc = False
    start_line = 0
    for line_no, line in lines:
        s = line.strip()
        if depth == 0 and (not s or s in ("[", "]", ",", "],")):
            continue
        if depth == 0:
            s = s.lstrip("[,").lstrip()
            line = s
            if not s:
                continue
            start_line = line_no
        buf.append(line)
        for ch in line:
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
        if depth <= 0 and not in_str:
            text = "\n".join(buf).strip().rstrip(",").rstrip("]").rstrip()
            buf, depth, in_str, esc = [], 0, False, False
            try:
                yield start_line, json.loads(text)
            except json.JSONDecodeError as exc:
                yield start_line, exc
    if buf:
        yield start_line, ValueError("unterminated JSON object at end of file")


def parse_json_lines(lines: Iterable[tuple[int, str]], ctx: ParseContext) -> Iterator[ParsedRecord | Malformed]:
    for line_no, obj in iter_json_objects(lines):
        if isinstance(obj, Exception):
            yield Malformed(line_no, "", f"invalid JSON: {obj}")
        elif not isinstance(obj, dict):
            yield Malformed(line_no, str(obj)[:500], "JSON value is not an object")
        else:
            rec = record_from_mapping(obj, line_no, ctx)
            if rec is None:
                yield Malformed(line_no, json.dumps(obj, default=str)[:1000], "missing timestamp or message field")
            else:
                yield rec


def parse_csv_lines(lines: Iterable[tuple[int, str]], ctx: ParseContext) -> Iterator[ParsedRecord | Malformed]:
    header: list[str] | None = None
    for line_no, line in lines:
        if line.startswith(FILE_MARKER):
            header = None
            continue
        if not line.strip():
            continue
        try:
            row = next(csv.reader([line]))
        except (csv.Error, StopIteration) as exc:
            yield Malformed(line_no, line[:1000], f"csv error: {exc}")
            continue
        if header is None:
            header = [h.strip() for h in row]
            continue
        if len(row) != len(header):
            yield Malformed(line_no, line[:1000], f"expected {len(header)} columns, got {len(row)}")
            continue
        rec = record_from_mapping(dict(zip(header, row, strict=True)), line_no, ctx)
        if rec is None:
            yield Malformed(line_no, line[:1000], "missing timestamp or message column")
        else:
            yield rec
