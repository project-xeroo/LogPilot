"""Deterministic PII-detection self-test used by the success-metrics endpoint (target: >95% detection).

Each case is (text, sensitive_substring): the substring must NOT survive redaction."""
from __future__ import annotations

import random

from shared.utils.redaction import redact_text


def _luhn(rng: random.Random) -> str:
    digits = [4] + [rng.randint(0, 9) for _ in range(14)]
    total = 0
    for i, d in enumerate(reversed(digits)):
        d2 = d * 2 if i % 2 == 0 else d
        total += d2 - 9 if d2 > 9 else d2
    return "".join(map(str, digits)) + str((10 - total % 10) % 10)


def corpus(n_each: int = 12, seed: int = 11) -> list[tuple[str, str]]:
    rng = random.Random(seed)
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    cases: list[tuple[str, str]] = []
    for i in range(n_each):
        cases += [
            (f"user first{i}.last+tag@example{i}.com login failed", f"first{i}.last+tag@example{i}.com"),
            (f"call back at +1 (415) 555-{2000 + i}", f"555-{2000 + i}"),
            (f"contact 020 7946 {1000 + i} please", f"7946 {1000 + i}"),
            (f"card {_luhn(rng)} declined", ""),  # filled below
            (f"db connect failed password=hunter{i}xyz host=db", f"hunter{i}xyz"),
            (f'{{"user":"a","password":"S3cret!{i}"}}', f"S3cret!{i}"),
            (f"Authorization: Bearer abcdEFGH{i}1234567890xyz", f"abcdEFGH{i}1234567890xyz"),
            (f"api_key=sk-live-{'a1b2c3d4e5' * 3}{i}", f"sk-live-{'a1b2c3d4e5' * 3}{i}"),
            (f"aws key AKIAIOSFODNN7EXAMPL{i % 10} used", f"AKIAIOSFODNN7EXAMPL{i % 10}"),
            (f"token check {jwt} user={i}", jwt),
            (f"client_secret: s{i}ecretvalue9876", f"s{i}ecretvalue9876"),
            (f"dsn postgres://svc:pw{i}pass@db.internal:5432/app", f"pw{i}pass"),
        ]
    out = []
    for text, secret in cases:
        if not secret:  # card cases: the secret is the number embedded in the text
            secret = text.split()[1]
        out.append((text, secret))
    return out


def detection_rate() -> dict:
    cases = corpus()
    missed = [t for t, s in cases if s in redact_text(t)[0]]
    return {"cases": len(cases), "missed": len(missed), "rate": 1 - len(missed) / len(cases)}
