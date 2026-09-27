"""Auto-labelling of error clusters: fast cloud model first, deterministic keyword fallback."""
from __future__ import annotations

import logging
import re
from collections import Counter

from shared.utils.clients import ServiceError, ai

log = logging.getLogger("logpilot.cluster.label")

_STOP = set(
    "the a an of to for in on at by with from and or is are was were be been not no failed failure error errors "
    "exception unable could cannot can't cant cannot due while during after before when into over under via "
    "num id ip ts uuid hex request response req res".split()
)


def heuristic_label(templates: list[tuple[str, int]], services: list[str]) -> str:
    """templates: (template_text, occurrence_count). Picks the most distinctive words by weighted frequency."""
    counts: Counter = Counter()
    for text, occ in templates[:20]:
        seen = set()
        for tok in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", re.sub(r"<[a-z]+>", " ", text)):
            t = tok.lower()
            if t in _STOP or t in seen:
                continue
            seen.add(t)
            counts[t] += 1 + min(occ, 100) ** 0.5
    words = [w for w, _ in counts.most_common(3)]
    if not words:
        return (templates[0][0][:60] if templates else "Unclassified errors").strip().title()
    label = " ".join(w.replace("_", " ").title() for w in words)
    return label[:120]


def label_cluster(templates: list[tuple[str, int]], services: list[str]) -> str:
    try:
        res = ai.post(
            "/internal/label",
            json={"templates": [t for t, _ in templates[:8]], "services": services[:6]},
            timeout=20,
        )
        label = (res.get("label") or "").strip()
        if label:
            return label[:120]
    except ServiceError as exc:
        log.info("AI labeller unavailable (%s); using keyword label", exc)
    return heuristic_label(templates, services)
