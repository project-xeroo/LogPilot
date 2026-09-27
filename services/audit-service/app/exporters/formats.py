"""Audit exporters: JSON Lines and CSV (streamed, so large ranges do not load into memory)."""
from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable, Iterator
from typing import Any

EVENT_COLUMNS = ["id", "ts", "actor_type", "actor_label", "action", "resource_type", "resource_id", "project_id", "ip", "details", "hash"]
ACTION_COLUMNS = ["id", "created_at", "tool_name", "trigger", "autonomy_level", "status", "confidence", "approved_by", "input_ref", "output_ref",
                  "high_impact", "reversible", "reverted_at", "summary", "downgraded_from"]


def to_jsonl(rows: Iterable[dict[str, Any]]) -> Iterator[bytes]:
    for r in rows:
        yield (json.dumps(r, default=str) + "\n").encode()


def to_csv(rows: Iterable[dict[str, Any]], columns: list[str]) -> Iterator[bytes]:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    w.writeheader()
    yield buf.getvalue().encode()
    for r in rows:
        buf.seek(0)
        buf.truncate()
        w.writerow({k: (json.dumps(v, default=str) if isinstance(v, (dict, list)) else v) for k, v in r.items()})
        yield buf.getvalue().encode()
