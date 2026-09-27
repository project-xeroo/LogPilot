"""Streaming redaction for the ingestion boundary (tool 03).

The redaction *engine* lives in `shared.utils.redaction` because the AI provider gateway must
apply the same gate at the egress boundary. This module applies it to file/record streams and
keeps per-session statistics.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator
from typing import Any

from shared.utils.redaction import Redactor, redact_obj

_redactor = Redactor()
MAX_LINE = 64 * 1024


class RedactionStats:
    def __init__(self) -> None:
        self.counts: Counter = Counter()
        self.lines = 0
        self.lines_with_pii = 0

    def as_dict(self) -> dict[str, Any]:
        return {"by_type": dict(self.counts), "total": sum(self.counts.values()), "lines": self.lines, "lines_with_pii": self.lines_with_pii}


def redact_lines(lines: Iterable[str], stats: RedactionStats) -> Iterator[str]:
    for line in lines:
        if len(line) > MAX_LINE:
            line = line[:MAX_LINE]
        clean, counts = _redactor.redact(line)
        stats.lines += 1
        if counts:
            stats.lines_with_pii += 1
            stats.counts.update(counts)
        yield clean


def redact_record_dict(rec: dict[str, Any], stats: RedactionStats) -> dict[str, Any]:
    """API-ingested records arrive already structured: redact every string field, keep keys."""
    before = sum(stats.counts.values())
    c: Counter = Counter()
    out = redact_obj(rec, c)
    stats.counts.update(c)
    stats.lines += 1
    if sum(c.values()) > 0 or sum(stats.counts.values()) > before:
        stats.lines_with_pii += 1
    return out
