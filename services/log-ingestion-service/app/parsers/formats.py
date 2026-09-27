"""Line-oriented log formats: Apache/Nginx (access + error), syslog (3164/5424), generic + custom."""
from __future__ import annotations

import re
from typing import Protocol

from app.parsers.base import ParseContext, ParsedRecord, extract_fields, finalize, infer_severity, parse_timestamp

_LEVELS = r"TRACE|DEBUG|INFO|NOTICE|WARN(?:ING)?|ERROR|ERR|CRIT(?:ICAL)?|FATAL|SEVERE|ALERT|EMERG(?:ENCY)?|PANIC"
_ISO = r"\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|\s?[+-]\d{2}:?\d{2})?"


class LineParser(Protocol):
    name: str

    def parse_line(self, line: str, line_no: int, ctx: ParseContext) -> ParsedRecord | None: ...


# ---- Apache / Nginx access (common + combined) ---------------------------------------------------------
class AccessLogParser:
    name = "access"
    _re = re.compile(
        r'^(?P<ip>\S+) \S+ (?P<user>\S+) \[(?P<ts>[^\]]+)\] "(?P<req>[^"]*)" (?P<status>\d{3}) (?P<size>\S+)'
        r'(?: "(?P<ref>[^"]*)" "(?P<ua>[^"]*)")?(?P<rest>.*)$'
    )

    def parse_line(self, line, line_no, ctx):
        m = self._re.match(line)
        if not m:
            return None
        ts = parse_timestamp(m.group("ts"), ctx)
        if ts is None:
            return None
        status = int(m.group("status"))
        sev = "ERROR" if status >= 500 else "WARN" if status >= 400 else "INFO"
        req = m.group("req")
        parts = req.split(" ")
        method, path = (parts[0], parts[1]) if len(parts) >= 2 else ("", req)
        rest = m.group("rest") or ""
        attrs = {"client_ip": m.group("ip"), "status": status, "method": method, "path": path}
        if m.group("size") not in ("-", ""):
            attrs["bytes"] = int(m.group("size")) if m.group("size").isdigit() else m.group("size")
        rec = ParsedRecord(
            timestamp=ts, severity=sev, service=ctx.default_service or "web",
            message=f'{method} {path} -> {status}{(" " + rest.strip()) if rest.strip() else ""}',
            attributes=attrs, line_no=line_no,
        )
        return finalize(rec, ctx)


class NginxErrorParser:
    name = "nginx_error"
    _re = re.compile(rf"^(?P<ts>\d{{4}}/\d{{2}}/\d{{2}} \d{{2}}:\d{{2}}:\d{{2}}) \[(?P<lvl>\w+)\] (?P<pid>\d+)#\d+: (?:\*\d+ )?(?P<msg>.*)$")

    def parse_line(self, line, line_no, ctx):
        m = self._re.match(line)
        ts = m and parse_timestamp(m.group("ts"), ctx)
        if not m or ts is None:
            return None
        rec = ParsedRecord(timestamp=ts, severity=m.group("lvl"), service=ctx.default_service or "nginx", message=m.group("msg"), line_no=line_no, attributes={"pid": m.group("pid")})
        return finalize(rec, ctx)


class ApacheErrorParser:
    name = "apache_error"
    _re = re.compile(
        r"^\[(?P<ts>[A-Za-z]{3} [A-Za-z]{3} \d{2} [\d:.]+ \d{4})\] \[(?:(?P<mod>[\w.]+):)?(?P<lvl>\w+)\] "
        r"(?:\[pid \d+(?::tid \d+)?\] )?(?:\[client [^\]]+\] )?(?P<msg>.*)$"
    )

    def parse_line(self, line, line_no, ctx):
        m = self._re.match(line)
        if not m:
            return None
        ts = parse_timestamp(re.sub(r"(\d{2}:\d{2}:\d{2})\.\d+", r"\1", m.group("ts")), ctx) or _apache_err_ts(m.group("ts"))
        if ts is None:
            return None
        rec = ParsedRecord(timestamp=ts, severity=m.group("lvl"), service=ctx.default_service or "apache", message=m.group("msg"), line_no=line_no)
        return finalize(rec, ctx)


def _apache_err_ts(s: str):
    from dateutil import parser

    try:
        from datetime import UTC

        return parser.parse(s).replace(tzinfo=UTC)
    except Exception:
        return None


# ---- syslog ----------------------------------------------------------------------------------------------
_SYSLOG_SEV = {0: "FATAL", 1: "FATAL", 2: "FATAL", 3: "ERROR", 4: "WARN", 5: "INFO", 6: "INFO", 7: "DEBUG"}


class Syslog5424Parser:
    name = "syslog5424"
    _re = re.compile(
        r"^<(?P<pri>\d+)>(?P<ver>\d) (?P<ts>\S+) (?P<host>\S+) (?P<app>\S+) (?P<pid>\S+) (?P<msgid>\S+) "
        r"(?P<sd>-|(?:\[[^\]]*\])+) ?(?P<msg>.*)$"
    )

    def parse_line(self, line, line_no, ctx):
        m = self._re.match(line)
        ts = m and parse_timestamp(m.group("ts"), ctx)
        if not m or ts is None:
            return None
        sev = _SYSLOG_SEV.get(int(m.group("pri")) & 7, "INFO")
        app = m.group("app")
        rec = ParsedRecord(
            timestamp=ts, severity=sev, service=None if app == "-" else app, message=m.group("msg"), line_no=line_no,
            attributes={"host": m.group("host"), "pid": m.group("pid")},
        )
        return finalize(rec, ctx)


class Syslog3164Parser:
    name = "syslog3164"
    _re = re.compile(
        r"^(?:<(?P<pri>\d+)>)?(?P<ts>[A-Z][a-z]{2}\s+\d{1,2} \d{2}:\d{2}:\d{2}) (?P<host>\S+) "
        r"(?P<app>[\w./-]+?)(?:\[(?P<pid>\d+)\])?: (?P<msg>.*)$"
    )

    def parse_line(self, line, line_no, ctx):
        m = self._re.match(line)
        ts = m and parse_timestamp(m.group("ts"), ctx)
        if not m or ts is None:
            return None
        msg = m.group("msg")
        sev = _SYSLOG_SEV.get(int(m.group("pri")) & 7) if m.group("pri") else None
        rec = ParsedRecord(
            timestamp=ts, severity=sev or _sev_from_message(msg), service=m.group("app"), message=msg, line_no=line_no,
            attributes={"host": m.group("host"), **({"pid": m.group("pid")} if m.group("pid") else {})},
        )
        return finalize(rec, ctx)


def _sev_from_message(msg: str) -> str:
    m = re.match(rf"^\[?({_LEVELS})\]?[\s:]", msg, re.I)
    return m.group(1) if m else infer_severity(msg)


# ---- generic timestamp+level formats (covers most application logs) -------------------------------------
class GenericParser:
    name = "generic"
    _patterns = [
        # 2026-09-27T10:00:00Z ERROR [checkout-service] message   |  ... ERROR checkout-service: message
        re.compile(rf"^(?P<ts>{_ISO})\s*[|,-]?\s*\[?(?P<lvl>{_LEVELS})\]?\s*[|:-]?\s*(?:\[(?P<svc>[^\]]+)\]\s*|(?P<svc2>[\w.-]+):\s+)?(?P<msg>.*)$", re.I),
        # 2026-09-27 10:00:00,123 - checkout - ERROR - message
        re.compile(rf"^(?P<ts>{_ISO})\s*[-|]\s*(?P<svc>[\w.:-]+)\s*[-|]\s*(?P<lvl>{_LEVELS})\s*[-|:]\s*(?P<msg>.*)$", re.I),
        # [2026-09-27 10:00:00] checkout.ERROR: message   (monolog)
        re.compile(rf"^\[(?P<ts>[^\]]+)\]\s+(?P<svc>[\w.-]+)\.(?P<lvl>{_LEVELS}):\s*(?P<msg>.*)$", re.I),
        # ERROR 2026-09-27T10:00:00Z [svc] message  (level first)
        re.compile(rf"^(?P<lvl>{_LEVELS})\s+\[?(?P<ts>{_ISO})\]?\s+(?:\[(?P<svc>[^\]]+)\]\s*)?(?P<msg>.*)$", re.I),
        # [2026-09-27 10:00:00] [ERROR] [svc] message
        re.compile(rf"^\[(?P<ts>{_ISO})\]\s*\[(?P<lvl>{_LEVELS})\]\s*(?:\[(?P<svc>[^\]]+)\]\s*)?(?P<msg>.*)$", re.I),
        # timestamp only - severity inferred from the message
        re.compile(rf"^(?P<ts>{_ISO})\s+(?:\[(?P<svc>[^\]]+)\]\s+)?(?P<msg>.*)$"),
    ]

    def parse_line(self, line, line_no, ctx):
        for pat in self._patterns:
            m = pat.match(line)
            if not m:
                continue
            g = m.groupdict()
            ts = parse_timestamp(g["ts"], ctx)
            if ts is None:
                continue
            msg = g["msg"]
            rec = ParsedRecord(
                timestamp=ts,
                severity=g.get("lvl") or infer_severity(msg),
                service=g.get("svc") or g.get("svc2"),
                message=msg,
                line_no=line_no,
            )
            return finalize(rec, ctx)
        return None


class CustomPatternParser:
    """User-supplied regex with named groups: timestamp|ts, level|severity, service, message|msg
    (+ optional request_id, trace_id, environment, version)."""

    name = "custom"

    def __init__(self, pattern: str):
        self._re = re.compile(pattern)
        names = set(self._re.groupindex)
        if not names & {"ts", "timestamp"} or not names & {"msg", "message"}:
            raise ValueError("custom pattern needs named groups for (ts|timestamp) and (msg|message)")

    def parse_line(self, line, line_no, ctx):
        m = self._re.match(line)
        if not m:
            return None
        g = {k: v for k, v in m.groupdict().items() if v is not None}
        ts = parse_timestamp(g.get("ts") or g.get("timestamp"), ctx)
        if ts is None:
            return None
        rec = ParsedRecord(
            timestamp=ts,
            severity=g.get("level") or g.get("lvl") or g.get("severity") or infer_severity(g.get("msg") or g.get("message", "")),
            service=g.get("service") or g.get("svc"),
            message=g.get("msg") or g.get("message", ""),
            request_id=g.get("request_id"), trace_id=g.get("trace_id"),
            environment=g.get("environment") or g.get("env"), deployment_version=g.get("version"),
            line_no=line_no,
        )
        return finalize(rec, ctx)


LINE_PARSERS: dict[str, LineParser] = {
    p.name: p
    for p in (
        AccessLogParser(), NginxErrorParser(), ApacheErrorParser(), Syslog5424Parser(), Syslog3164Parser(), GenericParser(),
    )
}
_ = extract_fields  # re-exported for tests
