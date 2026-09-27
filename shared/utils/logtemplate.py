"""Message normalisation: mask volatile tokens so repeated errors share one template."""
from __future__ import annotations

import hashlib
import re

_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<uuid>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"), "<ts>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d{1,5})?\b"), "<ip>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<hex>"),
    (re.compile(r"\b[0-9a-fA-F]{12,}\b"), "<hex>"),
    # id-ish tokens: req-8f3a9c, session_worker_2 keep, but tokens mixing letters+digits with a separator
    (re.compile(r"\b([A-Za-z]{2,}[-_])[A-Za-z0-9]*\d[A-Za-z0-9]{3,}\b"), r"\1<id>"),
    (re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?:ms|s|m|h|kb|mb|gb|b|%)?(?![\w.])", re.I), "<num>"),
]
# key=value metadata that the parsers already extract into dedicated fields; it must not shape the template
_META = re.compile(
    r"(?<![\w.-])(?:request[_-]?id|req[_-]?id|x-request-id|trace[_-]?id|span[_-]?id|env|environment|deployment[_-]?version|"
    r"app[_-]?version|version|release|build|latency[_-]?ms|duration[_-]?ms|elapsed[_-]?ms|took[_-]?ms|request[_-]?time|rt|"
    r"user[_-]?id|host|pid)=\"?[^\s,;\"]+\"?",
    re.I,
)
_WS = re.compile(r"\s+")
MAX_LEN = 500


def normalize_message(message: str) -> str:
    text = message[:MAX_LEN]
    # only the first line drives the template (stack traces would explode cardinality)
    text = text.split("\n", 1)[0]
    text = _META.sub(" ", text)
    for pat, repl in _RULES:
        text = pat.sub(repl, text)
    return _WS.sub(" ", text).strip()


def template_of(message: str) -> tuple[str, str]:
    """Returns (template, template_hash)."""
    t = normalize_message(message)
    return t, hashlib.sha1(t.lower().encode("utf-8")).hexdigest()[:32]


def template_id(project_id, template_hash: str):
    """Deterministic id shared by log_templates.id, log_records.embedding_id and the vector point id."""
    import uuid

    return uuid.uuid5(uuid.NAMESPACE_URL, f"{project_id}:{template_hash}")


def strip_metadata(message: str, limit: int = 300) -> str:
    """Message text without the key=value metadata that parsers already extracted (for display/labels)."""
    return _WS.sub(" ", _META.sub(" ", message.split("\n", 1)[0])).strip()[:limit]
