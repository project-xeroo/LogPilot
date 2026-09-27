"""Intent classification and slot extraction for the chat loop (rule-based: fast, deterministic, auditable)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

_UNIT = {"minute": 1, "min": 1, "hour": 60, "hr": 60, "day": 1440}


@dataclass
class Slots:
    service: str | None = None
    services: list[str] = field(default_factory=list)
    start: datetime | None = None
    end: datetime | None = None
    window_minutes: int | None = None
    quoted: str | None = None
    version: str | None = None


def find_services(text: str, known: list[str]) -> list[str]:
    low = text.lower()
    hits: list[tuple[int, str]] = []
    for name in known:
        n = name.lower()
        base = re.sub(r"-(service|worker|cache|db)$", "", n)
        for cand in {n, base} if len(base) >= 4 else {n}:
            m = re.search(rf"(?<![\w-]){re.escape(cand)}(?![\w-])", low)
            if m:
                hits.append((m.start(), name))
                break
    return [n for _, n in sorted(hits)]


def extract(text: str, known_services: list[str], now: datetime) -> Slots:
    s = Slots()
    s.services = find_services(text, known_services)
    s.service = s.services[0] if s.services else None
    q = re.search(r"[\"'`“]([^\"'`”]{3,300})[\"'`”]", text)
    s.quoted = q.group(1) if q else None
    v = re.search(r"\bv?\d+\.\d+(?:\.\d+)?\b", text)
    s.version = v.group(0) if v else None
    low = text.lower()

    m = re.search(r"last\s+(\d+)\s*(minute|min|hour|hr|day)s?", low)
    if m:
        s.window_minutes = int(m.group(1)) * _UNIT[m.group(2)]
    elif re.search(r"\b(last|past)\s+hour\b", low):
        s.window_minutes = 60
    elif re.search(r"\btoday\b", low):
        s.start, s.end = now.replace(hour=0, minute=0, second=0, microsecond=0), now
    elif re.search(r"\byesterday\b", low):
        d0 = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        s.start, s.end = d0, d0 + timedelta(days=1)
    else:
        t = re.search(r"\b(?:at|around|about|near)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", low)
        if t:
            hh, mm, ap = int(t.group(1)), int(t.group(2) or 0), t.group(3)
            if ap == "pm" and hh < 12:
                hh += 12
            if ap == "am" and hh == 12:
                hh = 0
            if hh <= 23:
                at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                if at > now:
                    at -= timedelta(days=1)  # "at 3am" means the most recent 3am
                s.start, s.end = at - timedelta(minutes=45), at + timedelta(minutes=45)
    if s.window_minutes and not s.start:
        s.end, s.start = now, now - timedelta(minutes=s.window_minutes)
    return s


# A meta question about the agent itself, not about the logs it watches - no tool can ever answer this (they
# return log/service data, never agent self-description), so both the old regex router and the new agentic
# planner (see planner.py's `is_meta_question` use) treat it as a fast, tool-free special case.
CAPABILITIES_PATTERN = re.compile(
    r"\b(what (all )?(can|do) you do|what are you (capable of|able to do)|what can (this|the) (agent|assistant|tool|bot) do|"
    r"your capabilities|what (tools|skills|features) do you have|list (your |the )?(tools|capabilities|features)|"
    r"how (can|do) you help( me)?|who are you|what (is|are) logpilot)\b", re.I,
)


def is_meta_question(text: str) -> bool:
    return bool(CAPABILITIES_PATTERN.search(text))


# ---- intents ------------------------------------------------------------------------------------------------------------------
_RULES: list[tuple[str, re.Pattern]] = [
    ("capabilities", CAPABILITIES_PATTERN),
    ("explain", re.compile(r"\b(what does .{2,80} mean|what is this (error|log|message)|explain|is this (normal|bad|serious)|what('s| is) (a|an) )\b", re.I)),
    ("report", re.compile(r"\b(report|post-?mortem|pre-?mortem|write.?up|incident summary|executive summary|summari[sz]e (the )?incident)\b", re.I)),
    ("deployment", re.compile(r"\b(deploy(ment|ed)?|release|rollout|rolled out|regression|new version|latest version)\b", re.I)),
    ("rca", re.compile(r"\b(root cause|why did|what caused|cause of|rca|figure out why|why (is|are|was|were) .{0,60}(failing|down|erroring|broken|slow))\b", re.I)),
    ("risk", re.compile(r"\b(risk|forecast|predict\w*|likely to (fail|break)|will (fail|break)|going to (fail|break)|about to (fail|break)|fail next|pre-?incident|failure probability|flagged|early warning|what should (i|we) do)\b", re.I)),
    ("search", re.compile(r"\b(find|search|grep|show me|look for|list)\b.*\b(logs?|errors?|messages?|lines?|records?|exceptions?)\b|\b(logs?|errors?) (for|with|containing|matching)\b", re.I)),
    ("health", re.compile(r"\b(health|status|how (is|are)|overview|error rate|top problem|trending|doing|summary|what happened|state of)\b", re.I)),
]


def classify(text: str) -> str:
    for name, pat in _RULES:
        if pat.search(text):
            return name
    return "qa"


def search_query(text: str, slots: Slots) -> tuple[str, bool]:
    """Returns (query, is_keyword). Quoted text => exact keyword search; otherwise a semantic search of the request."""
    if slots.quoted:
        return slots.quoted, True
    m = re.search(r"(?:containing|matching|with|for)\s+([\w.\-/: ]{3,80})$", text.strip().rstrip("?.!"), re.I)
    if m and not re.search(r"\b(errors?|logs?)$", m.group(1).strip(), re.I):
        return m.group(1).strip(), True
    return re.sub(r"\b(show me|find|search( for)?|list|logs?|please)\b", "", text, flags=re.I).strip(" ?.!") or text, False
