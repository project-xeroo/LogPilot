"""PII / secret redaction (PRD tool 03 - "mandatory", ">=95% detection rate").

This runs *before* any data is stored and *before* any text crosses the egress boundary to a
cloud AI provider. It lives in `shared` because both the ingestion path (storage boundary) and
the AI provider gateway (egress boundary) must enforce it - it is not policy-configurable.

Detected: emails, phone numbers, passwords, credit-card numbers (Luhn-validated), JWT tokens,
API keys, and secrets (cloud/VCS/chat tokens, bearer/basic auth, private keys, URL credentials).
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

REDACTED = "[REDACTED_{}]"


def _tag(kind: str) -> str:
    return REDACTED.format(kind)


# ---- patterns -----------------------------------------------------------------------------------
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S
)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b")
_URL_CREDS = re.compile(r"(?P<scheme>\b[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<user>[^\s:/@]+):(?P<pw>[^\s@/]+)@")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_KNOWN_KEYS = [
    ("API_KEY", re.compile(r"\bsk-(?:live-|test-|proj-|ant-)?[A-Za-z0-9_-]{16,}\b")),
    ("API_KEY", re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{12,}\b")),
    ("API_KEY", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("API_KEY", re.compile(r"\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b")),
    ("API_KEY", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("SECRET", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("SECRET", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("SECRET", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
]
_EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")

# key=value / "key": "value" style credentials. Only the *value* is replaced.
_PW_KEYS = r"(?:pass(?:word|wd|phrase)?|pwd|passcode|pin|db_pass(?:word)?|user_pass(?:word)?)"
_API_KEYS = r"(?:api[_-]?key|apikey|x-api-key|access[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|id[_-]?token|session[_-]?token|token)"
_SECRET_KEYS = r"(?:secret(?:[_-]?key)?|client[_-]?secret|private[_-]?key|signing[_-]?key|encryption[_-]?key|credentials?)"

_KV_TEMPLATE = (
    r"(?P<key>(?<![A-Za-z0-9])\"?{keys}\"?)"
    r"(?P<sep>\s*[:=]\s*)"
    r"(?P<val>\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;&\"'}}\]]+)"
)
_KV_PASSWORD = re.compile(_KV_TEMPLATE.format(keys=_PW_KEYS), re.I)
_KV_API = re.compile(_KV_TEMPLATE.format(keys=_API_KEYS), re.I)
_KV_SECRET = re.compile(_KV_TEMPLATE.format(keys=_SECRET_KEYS), re.I)

_CC = re.compile(r"(?<![\w-])(?:\d[ -]?){12,18}\d(?![\w-])")  # never inside an alphanumeric token (hex ids, uuids)
# international (+..) or separated national numbers; validated by digit count below
_PHONE = re.compile(
    r"(?<![\w.:/-])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?|\d{2,4}[\s.-])\d{3,4}[\s.-]\d{3,4}(?:[\s.-]\d{1,4})?(?![\w-])"
    r"|(?<![\w.:/-])\+\d{10,15}(?![\w-])"
)
_TEN_DIGITS = re.compile(r"(?:\d[ ().-]{0,2}){10}")
# Values already redacted / non-secret literals we should not re-tag
_SKIP_VALUES = {"", "null", "none", "true", "false", "undefined", "***", "****", "<redacted>", "[redacted]"}


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


class Redactor:
    """Stateless redactor; `redact(text)` returns (clean_text, Counter of kinds)."""

    def redact(self, text: str) -> tuple[str, Counter]:
        counts: Counter = Counter()
        if not text:
            return text, counts
        # cheap pre-check: nothing to redact in most log lines
        out = text

        if "PRIVATE KEY" in out:
            out, n = _PRIVATE_KEY.subn(_tag("SECRET"), out)
            counts["SECRET"] += n
        if "eyJ" in out:
            out, n = _JWT.subn(_tag("JWT"), out)
            counts["JWT"] += n
        if "://" in out and "@" in out:
            def _url(m: re.Match) -> str:
                counts["PASSWORD"] += 1
                return f"{m.group('scheme')}{m.group('user')}:{_tag('PASSWORD')}@"

            out = _URL_CREDS.sub(_url, out)
        low = out.lower()
        if "bearer" in low or "basic " in low:
            def _bearer(m: re.Match) -> str:
                counts["API_KEY"] += 1
                return f"{m.group(1)} {_tag('API_KEY')}"

            out = _BEARER.sub(_bearer, out)
        for kind, pat in _KNOWN_KEYS:
            if pat.search(out):
                out, n = pat.subn(_tag(kind), out)
                counts[kind] += n
        if "@" in out:
            out, n = _EMAIL.subn(_tag("EMAIL"), out)
            counts["EMAIL"] += n

        for kind, pat in (("PASSWORD", _KV_PASSWORD), ("API_KEY", _KV_API), ("SECRET", _KV_SECRET)):
            out = self._sub_kv(out, pat, kind, counts)

        if _TEN_DIGITS.search(out):  # cheap pre-check: any 10+ digit run (with separators)?
            out = self._sub_cards(out, counts)
            out = self._sub_phones(out, counts)
        return out, counts

    # -- helpers --------------------------------------------------------------------------------
    @staticmethod
    def _sub_kv(text: str, pat: re.Pattern, kind: str, counts: Counter) -> str:
        def repl(m: re.Match) -> str:
            val = m.group("val")
            inner = val.strip("\"'")
            if inner.lower() in _SKIP_VALUES or inner.startswith("[REDACTED_"):
                return m.group(0)
            counts[kind] += 1
            quote = val[0] if val[0] in "\"'" else ""
            return f"{m.group('key')}{m.group('sep')}{quote}{_tag(kind)}{quote}"

        return pat.sub(repl, text)

    @staticmethod
    def _sub_cards(text: str, counts: Counter) -> str:
        def repl(m: re.Match) -> str:
            digits = re.sub(r"\D", "", m.group(0))
            if 13 <= len(digits) <= 19 and _luhn_ok(digits):
                counts["CREDIT_CARD"] += 1
                return _tag("CREDIT_CARD")
            return m.group(0)

        return _CC.sub(repl, text)

    @staticmethod
    def _sub_phones(text: str, counts: Counter) -> str:
        def repl(m: re.Match) -> str:
            s = m.group(0)
            digits = re.sub(r"\D", "", s)
            if not 10 <= len(digits) <= 15:
                return s
            # avoid dates / timestamps like 2026-09-27 or 10:00:00.123 and version-ish numbers
            if re.fullmatch(r"\d{4}[-./]\d{2}[-./]\d{2}", s.strip()):
                return s
            counts["PHONE"] += 1
            return _tag("PHONE")

        return _PHONE.sub(repl, text)


_default = Redactor()


def redact_text(text: str) -> tuple[str, Counter]:
    return _default.redact(text)


def redact(text: str) -> str:
    return _default.redact(text)[0]


def redact_obj(obj: Any, counts: Counter | None = None) -> Any:
    """Recursively redact every string in a JSON-like structure (dict keys are preserved)."""
    if counts is None:
        counts = Counter()
    if isinstance(obj, str):
        clean, c = _default.redact(obj)
        counts.update(c)
        return clean
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            # a secret-looking key holding a scalar is redacted wholesale
            if isinstance(v, str) and re.fullmatch(
                rf"(?i){_PW_KEYS}|{_API_KEYS}|{_SECRET_KEYS}", str(k)
            ) and v.lower() not in _SKIP_VALUES and not v.startswith("[REDACTED_"):
                counts["SECRET"] += 1
                out[k] = _tag("SECRET")
            else:
                out[k] = redact_obj(v, counts)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v, counts) for v in obj]
    return obj


def contains_pii(text: str) -> bool:
    return bool(_default.redact(text)[1])
