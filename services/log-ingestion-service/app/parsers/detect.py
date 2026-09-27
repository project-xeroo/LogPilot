"""Format auto-detection and the top-level streaming parser (tool 02: Log Parsing)."""
from __future__ import annotations

import copy
import itertools
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import PurePosixPath

from app.parsers.base import FILE_MARKER, MAX_MESSAGE, Malformed, ParseContext, ParsedRecord
from app.parsers.formats import LINE_PARSERS, CustomPatternParser, GenericParser, LineParser
from app.parsers.structured import parse_csv_lines, parse_json_lines

_CONTINUATION = re.compile(r"^(\s+\S|\tat |at \S|Caused by:|Traceback|\.\.\. \d+ more|File \"|Suppressed:|[\w.$]+(?:Error|Exception|Failure)\b)")
_SNIFF_LINES = 100
_TEXT_ORDER = ["syslog5424", "syslog3164", "access", "nginx_error", "apache_error", "generic"]


def service_from_filename(name: str | None) -> str | None:
    if not name:
        return None
    stem = PurePosixPath(name.replace("\\", "/")).name
    for suffix in (".gz", ".log", ".txt", ".json", ".csv", ".ndjson", ".jsonl"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
    stem = re.sub(r"[.-]?(\d{4}-\d{2}-\d{2}|\d{8}|\d+)$", "", stem)  # rotated suffixes: app.log.1, app-2026-09-27
    stem = re.sub(r"\.log$", "", stem, flags=re.I)
    return stem or None


def detect_format(sample: list[str], filename_hint: str | None = None, ctx: ParseContext | None = None) -> str:
    lines = [l for l in sample if l.strip()]
    if not lines:
        return "generic"
    hint = (filename_hint or "").lower()
    first = lines[0].lstrip()
    if hint.endswith((".json", ".ndjson", ".jsonl")) or first[:1] in ("{", "["):
        return "json"
    if hint.endswith(".csv"):
        return "csv"
    if _looks_like_csv(lines):
        return "csv"
    ctx = ctx or ParseContext()
    scores: dict[str, float] = {}
    for name in _TEXT_ORDER:
        p = LINE_PARSERS[name]
        ok = sum(1 for i, l in enumerate(lines) if p.parse_line(l, i, ctx) is not None)
        scores[name] = ok / len(lines)
    # highest match rate wins; ties go to the more specific format (earlier in _TEXT_ORDER)
    best = max(_TEXT_ORDER, key=lambda n: (scores[n], n != "generic"))
    return best if scores[best] >= 0.5 else "generic"


def _looks_like_csv(lines: list[str]) -> bool:
    head = lines[0]
    if head.count(",") < 2:
        return False
    cols = [c.strip().strip('"').lower() for c in head.split(",")]
    return any(c in ("timestamp", "time", "ts", "@timestamp", "datetime", "date") for c in cols) and any(
        c in ("message", "msg", "log", "text") for c in cols
    )


def _parse_text(lines: Iterable[tuple[int, str]], parser: LineParser, ctx: ParseContext) -> Iterator[ParsedRecord | Malformed]:
    fallback = GenericParser()
    current: ParsedRecord | None = None
    for line_no, line in lines:
        if not line.strip():
            continue
        rec = parser.parse_line(line, line_no, ctx)
        if rec is None and parser.name != "generic":
            rec = fallback.parse_line(line, line_no, ctx)
        if rec is not None:
            if current is not None:
                yield current
            current = rec
            continue
        if current is not None and _CONTINUATION.match(line):  # stack trace / multi-line message
            if len(current.message) < MAX_MESSAGE:
                current.message = (current.message + "\n" + line.rstrip())[:MAX_MESSAGE]
            continue
        if current is not None:
            yield current
            current = None
        yield Malformed(line_no, line[:2000], "line does not match any known log format")
    if current is not None:
        yield current


def parse_stream(lines: Iterable[str], ctx: ParseContext, formats_seen: list[str] | None = None) -> Iterator[ParsedRecord | Malformed]:
    """Parse an entire (possibly multi-file) stream of redacted lines.

    Each file in a multi-file upload is introduced by a FILE_MARKER line, which resets the default
    service (from the filename) and re-runs format detection.
    """
    custom = CustomPatternParser(ctx.custom_pattern) if ctx.custom_pattern else None
    it = enumerate(lines, 1)
    carry: list[tuple[int, str]] = []  # a marker line that ended the previous segment

    def rest() -> Iterator[tuple[int, str]]:
        for item in it:
            if item[1].startswith(FILE_MARKER):
                carry.append(item)
                return
            yield item

    while True:
        first = carry.pop() if carry else next(it, None)
        if first is None:
            return
        seg_ctx = copy.copy(ctx)
        filename = None
        buffered: list[tuple[int, str]] = []
        if first[1].startswith(FILE_MARKER):
            filename = first[1][len(FILE_MARKER):]
            seg_ctx.default_service = ctx.default_service or service_from_filename(filename)
        else:
            buffered.append(first)
        while len(buffered) < _SNIFF_LINES:
            nxt = next(it, None)
            if nxt is None:
                break
            if nxt[1].startswith(FILE_MARKER):
                carry.append(nxt)
                break
            buffered.append(nxt)
        fmt = "custom" if custom else detect_format([l for _, l in buffered], filename, seg_ctx)
        if formats_seen is not None:
            formats_seen.append(fmt)
        stream = itertools.chain(buffered, [] if carry else rest())
        if fmt == "json":
            yield from parse_json_lines(stream, seg_ctx)
        elif fmt == "csv":
            yield from parse_csv_lines(stream, seg_ctx)
        else:
            yield from _parse_text(stream, custom or LINE_PARSERS[fmt], seg_ctx)


def dominant_format(seen: list[str]) -> str | None:
    return Counter(seen).most_common(1)[0][0] if seen else None
