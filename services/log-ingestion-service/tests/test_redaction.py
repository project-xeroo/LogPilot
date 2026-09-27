"""PII detection rate must stay >= 95% (PRD tool 03 / technical metric)."""
import random

from shared.utils.redaction import redact_obj, redact_text

random.seed(7)


def _luhn_card() -> str:
    digits = [4] + [random.randint(0, 9) for _ in range(14)]
    total = 0
    for i, d in enumerate(reversed(digits)):
        d2 = d * 2 if i % 2 == 0 else d
        total += d2 - 9 if d2 > 9 else d2
    return "".join(map(str, digits)) + str((10 - total % 10) % 10)


def _corpus():
    cases = []
    for i in range(40):
        cases.append((f"user u{i}.name+tag@example{i}.co.uk login failed", f"u{i}.name+tag@example{i}.co.uk"))
    for i in range(30):
        cases.append((f"call back at +1 (415) 555-{2000 + i} please", f"555-{2000 + i}"))
        cases.append((f"customer phone 020 7946 {1000 + i}", f"7946 {1000 + i}"))
    for i in range(30):
        card = _luhn_card()
        cases.append((f"payment declined for card {card} amount=10", card))
    for i in range(30):
        cases.append((f"connect failed password=hunter{i}xyz host=db", f"hunter{i}xyz"))
        cases.append((f'{{"user":"a","password":"S3cret!{i}"}}', f"S3cret!{i}"))
    for i in range(20):
        cases.append((f"Authorization: Bearer abcdEFGH{i}1234567890xyz", f"abcdEFGH{i}1234567890xyz"))
        cases.append((f"api_key=sk-live-{'a1b2c3d4e5' * 3}{i}", f"sk-live-{'a1b2c3d4e5' * 3}{i}"))
        cases.append((f"aws key AKIA{'IOSFODNN7EXAMPL'}{i % 10}", f"AKIAIOSFODNN7EXAMPL{i % 10}"))
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    for i in range(20):
        cases.append((f"token check {jwt} user={i}", jwt))
        cases.append((f"client_secret: s{i}ecretvalue9876", f"s{i}ecretvalue9876"))
        cases.append((f"dsn postgres://svc:pw{i}pass@db.internal:5432/app", f"pw{i}pass"))
    return cases


def test_detection_rate_at_least_95_percent():
    cases = _corpus()
    missed = [(t, s) for t, s in cases if s in redact_text(t)[0]]
    rate = 1 - len(missed) / len(cases)
    assert rate >= 0.95, f"detection rate {rate:.3f}; missed e.g. {missed[:5]}"
    assert rate >= 0.99  # we actually target much better than the floor


def test_no_false_positives_on_ordinary_log_content():
    for line in [
        "2026-09-27T10:00:00.123Z INFO [checkout] order 1234567890123456 placed in 245ms from 10.0.0.12:8443",
        "GET /api/v1/items?page=2 -> 200 bytes=1532 rt=0.031",
        "version=v2.3.1 build=4821 connections=95/100",
        "request req-8f3a9c completed status=200 trace_id=4bf92f3577b34da6a3ce929d0e0e4736",
    ]:
        out, counts = redact_text(line)
        assert out == line and not counts, (line, out)


def test_redact_obj_preserves_structure_and_redacts_secret_keys():
    obj = {"msg": "mail bob@x.io", "password": "abc", "nested": {"api_key": "zzz", "n": 5}, "list": ["+14155552671"]}
    out = redact_obj(obj)
    assert out["msg"] == "mail [REDACTED_EMAIL]"
    assert out["password"].startswith("[REDACTED_") and out["nested"]["api_key"].startswith("[REDACTED_")
    assert out["nested"]["n"] == 5 and "[REDACTED_PHONE]" in out["list"][0]
