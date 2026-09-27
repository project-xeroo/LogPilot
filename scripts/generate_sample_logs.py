#!/usr/bin/env python3
"""Generate a realistic multi-format demo dataset for LogPilot.

Story (all times relative to --end, default "now", UTC):
  * six services in five different log formats (generic text, NDJSON, python-logging, syslog, apache, CSV)
  * two PAST outages with the same signature: Redis connection-pool exhaustion -> session-worker
    timeouts -> checkout failures (each preceded by a ~14 minute build-up)
  * a bad deployment: payment-service v2.3.1 introduces a NullPointerException (new error type + regression)
  * an auth-service latency spike, and background noise
  * a NEW build-up that started ~16 minutes before --end: Redis errors climbing, session-worker starting to
    fail, checkout still healthy -> the agent should forecast checkout/session-worker failure BEFORE it happens
  * ~0.5% of lines carry PII/secrets (emails, phones, cards, tokens, passwords) to prove redaction

Usage:  python scripts/generate_sample_logs.py --out data/sample-logs [--hours 26] [--seed 7] [--no-zip]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import random
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

PII = [
    "user alice.smith@example.com logged in from 10.2.3.4",
    "customer contact phone +1 415-555-0132 verified",
    "payment declined for card 4111 1111 1111 1111 (retrying)",
    "downstream call failed Authorization: Bearer abcDEF1234567890abcDEF123456",
    "config reloaded password=hunter2 timeout=30",
    "api_key=sk-live-abcdefghijklmnopqrstuvwx rotated",
    "notified ops-team@corp.example.org about quota",
]


class Gen:
    def __init__(self, end: datetime, hours: float, seed: int):
        self.end = end
        self.start = end - timedelta(hours=hours)
        self.rng = random.Random(seed)
        self.lines: dict[str, list[tuple[datetime, dict]]] = {k: [] for k in
            ("checkout-service", "payment-service", "session-worker", "redis-cache", "auth-service", "inventory-service")}
        # incidents: onset (user-visible failure) times
        self.past = [end - timedelta(hours=20, minutes=5), end - timedelta(hours=9, minutes=40)]
        self.now_ramp_start = end - timedelta(minutes=16)
        self.deploy_at = end - timedelta(hours=5)
        self.latency_spike = (end - timedelta(hours=13, minutes=10), end - timedelta(hours=13, minutes=2))

    # -- helpers ---------------------------------------------------------------------------------------
    def emit(self, svc: str, ts: datetime, sev: str, msg: str, **extra) -> None:
        self.lines[svc].append((ts, {"sev": sev, "msg": msg, **extra}))

    def version(self, svc: str, ts: datetime) -> str:
        if svc == "payment-service":
            return "v2.3.1" if ts >= self.deploy_at else "v2.3.0"
        return {"checkout-service": "v5.12.0", "session-worker": "v1.8.4", "redis-cache": "v7.0.11", "auth-service": "v3.1.2", "inventory-service": "v4.0.7"}[svc]

    def poisson(self, lam: float) -> int:
        if lam <= 0:
            return 0
        if lam > 30:
            return max(0, int(self.rng.gauss(lam, math.sqrt(lam))))
        L, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= self.rng.random()
            if p <= L:
                return k
            k += 1

    def jitter(self, minute: datetime) -> datetime:
        return minute + timedelta(seconds=self.rng.random() * 59.9)

    def tid(self) -> str:
        return uuid.UUID(int=self.rng.getrandbits(128)).hex

    # -- incident intensity (errors per minute) --------------------------------------------------------
    @staticmethod
    def ramp(t: datetime, begin: datetime, end_: datetime, lo: float, hi: float) -> float:
        if t < begin or t > end_:
            return 0.0
        x = (t - begin).total_seconds() / max((end_ - begin).total_seconds(), 1)
        return lo * (hi / lo) ** x  # exponential build-up

    def intensity(self, svc: str, t: datetime) -> float:
        total = 0.0
        for onset in self.past:
            recover = onset + timedelta(minutes=12)
            if svc == "redis-cache":
                total += self.ramp(t, onset - timedelta(minutes=14), onset, 1.0, 26.0) + (26.0 if onset < t <= recover else 0)
            elif svc == "session-worker":
                total += self.ramp(t, onset - timedelta(minutes=8), onset, 1.0, 20.0) + (22.0 if onset < t <= recover else 0)
            elif svc == "checkout-service":
                total += (self.ramp(t, onset - timedelta(minutes=2), onset, 1.0, 12.0) + (24.0 if onset < t <= recover else 0))
            elif svc == "payment-service":
                total += 8.0 if onset < t <= recover else 0
        rs = self.now_ramp_start
        if rs <= t <= self.end + timedelta(minutes=1):
            if svc == "redis-cache":
                total += self.ramp(t, rs, self.end, 1.0, 19.0)
            elif svc == "session-worker":
                total += self.ramp(t, rs + timedelta(minutes=7), self.end, 1.0, 9.0)
            elif svc == "checkout-service":
                total += self.ramp(t, self.end - timedelta(minutes=3), self.end, 0.5, 2.5)
        return total

    # -- message catalogues ----------------------------------------------------------------------------
    def error_msg(self, svc: str, incident: bool) -> str:
        r = self.rng
        if svc == "redis-cache":
            return r.choice([
                f"Connection pool exhausted: 100/100 connections in use, {r.randint(3, 60)} clients waiting",
                f"Redis connection timed out after {r.choice([3000, 5000, 5000, 8000])}ms",
                f"Timeout connecting to redis: connection pool exhausted, waited {r.randint(2000, 9000)}ms",
                f"Client {r.randint(1, 900)} rejected: max number of clients reached",
            ])
        if svc == "session-worker":
            return r.choice([
                f"Timeout acquiring Redis connection for session {uuid.UUID(int=r.getrandbits(128))} after {r.randint(2000, 6000)}ms",
                "Failed to refresh session: Redis connection pool exhausted",
                f"Could not acquire Redis connection from pool (attempt {r.randint(1, 3)}/3)",
            ])
        if svc == "checkout-service":
            return r.choice([
                f"Checkout failed for order {r.randint(100000, 999999)}: session service unavailable (upstream session-worker timeout)",
                "HTTP 503 from session-worker for POST /checkout",
                f"Order {r.randint(100000, 999999)} could not be completed: session lookup timed out after 3000ms",
            ])
        if svc == "payment-service":
            if incident:
                return f"Payment authorization timed out waiting for checkout session {r.randint(100000, 999999)}"
            return r.choice(["Card authorization declined by issuer (code 05)", "Payment provider returned 502 Bad Gateway, will retry"])
        if svc == "auth-service":
            return r.choice(["Unauthorized: invalid token for user", "Token validation failed: signature expired"])
        return r.choice(["Stock reservation failed: item not found in warehouse index", "Failed to fetch price list: upstream timeout after 2000ms"])

    def info_msg(self, svc: str) -> str:
        r = self.rng
        return {
            "checkout-service": r.choice([f"Order {r.randint(100000, 999999)} placed successfully in {r.randint(80, 320)}ms", f"Cart {r.randint(1, 9999)} validated", "Checkout session created"]),
            "payment-service": r.choice([f"Payment authorized for order {r.randint(100000, 999999)} in {r.randint(60, 240)}ms", "Card tokenised", "Settlement batch queued"]),
            "session-worker": r.choice([f"Session {uuid.UUID(int=r.getrandbits(128))} refreshed", "Session cache warmed", f"Expired {r.randint(1, 40)} sessions"]),
            "redis-cache": r.choice([f"GET session:{r.randint(1, 99999)} hit ({r.randint(1, 4)}ms)", "Background save started", f"Connected clients: {r.randint(20, 60)}/100"]),
            "auth-service": r.choice(["Login succeeded", "Token issued", "Token refreshed", "Session validated"]),
            "inventory-service": r.choice([f"Stock reserved for sku-{r.randint(1000, 9999)}", "Warehouse index refreshed", f"Price list served in {r.randint(4, 30)}ms"]),
        }[svc]

    def add_resolution_logs(self) -> None:
        """Operators/autoscaler leave a trail when they fix an outage - the agent mines these for
        'what resolved it' (historical resolution logs)."""
        for onset in self.past:
            rec = onset + timedelta(minutes=11)
            self.emit("session-worker", rec, "INFO", "Operator action: restarted session-worker-2 (pid 4412) to release leaked Redis connections", req=self.tid()[:12], trace=self.tid(), lat=None)
            self.emit("redis-cache", rec + timedelta(minutes=1), "INFO", "Autoscaler: scaling redis-cache from 2 to 3 replicas", req=self.tid()[:12], trace=self.tid(), lat=None)
            self.emit("session-worker", rec + timedelta(minutes=2), "INFO", "Runbook: increased Redis connection pool limit from 100 to 200", req=self.tid()[:12], trace=self.tid(), lat=None)

    # -- generate ------------------------------------------------------------------------------------------
    def run(self) -> None:
        r = self.rng
        t = self.start.replace(second=0, microsecond=0)
        base = {"checkout-service": 9, "payment-service": 7, "session-worker": 8, "redis-cache": 10, "auth-service": 12, "inventory-service": 6}
        while t <= self.end:
            minute_trace = self.tid()  # cascading failures share one trace id across services within a minute
            hour = t.hour + t.minute / 60
            diurnal = 1 + 0.35 * math.sin((hour - 9) / 24 * 2 * math.pi)
            for svc, b in base.items():
                incident_err = self.intensity(svc, t)
                n_info = self.poisson(b * diurnal)
                n_err_bg = self.poisson(0.25)
                n_err = self.poisson(incident_err) + n_err_bg
                for _ in range(n_info):
                    sev = r.choices(["INFO", "DEBUG", "WARN"], [90, 8, 2])[0]
                    lat = None
                    if svc in ("auth-service", "checkout-service", "inventory-service"):
                        lat = r.gauss(90, 20)
                        if svc == "auth-service" and self.latency_spike[0] <= t <= self.latency_spike[1]:
                            lat = r.gauss(2600, 300)
                    self.emit(svc, self.jitter(t), sev, self.info_msg(svc) if sev != "WARN" else "Slow response from dependency, retrying",
                              req=self.tid()[:12], trace=self.tid(), lat=None if lat is None else max(5.0, lat))
                shared_trace = minute_trace
                for k in range(n_err):
                    inc = incident_err > 3
                    st = self.jitter(t)
                    if inc and k == 0:  # cascading failure shares one trace across services
                        pass
                    self.emit(svc, st, r.choices(["ERROR", "FATAL"], [97, 3])[0] if inc else "ERROR", self.error_msg(svc, inc),
                              req=self.tid()[:12], trace=shared_trace if (inc and r.random() < 0.6) else self.tid(), lat=None)
                # payment regression after the deployment
                if svc == "payment-service" and t >= self.deploy_at:
                    for _ in range(self.poisson(2.5)):
                        self.emit(svc, self.jitter(t), "ERROR", "NullPointerException in PaymentValidator.validate(): card.billingAddress is null",
                                  req=self.tid()[:12], trace=self.tid(), lat=None)
                # PII sprinkles
                if r.random() < 0.06:
                    self.emit(svc, self.jitter(t), "INFO", r.choice(PII), req=self.tid()[:12], trace=self.tid(), lat=None)
            t += timedelta(minutes=1)
        self.add_resolution_logs()
        for v in self.lines.values():
            v.sort(key=lambda x: x[0])

    # -- renderers -----------------------------------------------------------------------------------------
    def render(self) -> dict[str, str]:
        out: dict[str, str] = {}
        # checkout: generic text
        out["checkout-service.log"] = "\n".join(
            f"{ts:%Y-%m-%dT%H:%M:%S.%f}"[:-3] + f"Z {d['sev']} [checkout-service] {d['msg']} request_id={d['req']} trace_id={d['trace']} version={self.version('checkout-service', ts)} env=production"
            + (f" latency_ms={d['lat']:.0f}" if d["lat"] else "") for ts, d in self.lines["checkout-service"]) + "\n"
        # payment: NDJSON
        out["payment-service.json"] = "\n".join(json.dumps({
            "timestamp": ts.isoformat().replace("+00:00", "Z"), "level": d["sev"].lower(), "service": "payment-service", "message": d["msg"],
            "request_id": d["req"], "trace_id": d["trace"], "version": self.version("payment-service", ts), "environment": "production"})
            for ts, d in self.lines["payment-service"]) + "\n"
        # session-worker: python logging
        out["session-worker.log"] = "\n".join(
            f"{ts:%Y-%m-%d %H:%M:%S},{ts.microsecond // 1000:03d} - session-worker - {d['sev']} - {d['msg']} request_id={d['req']} trace_id={d['trace']} version={self.version('session-worker', ts)} env=production"
            for ts, d in self.lines["session-worker"]) + "\n"
        # redis: syslog 3164
        sysmap = {"DEBUG": 7, "INFO": 6, "WARN": 4, "ERROR": 3, "FATAL": 2}
        out["redis-cache.log"] = "\n".join(
            f"<{16 * 8 + sysmap[d['sev']]}>{ts:%b} {ts.day:2d} {ts:%H:%M:%S} redis-host-1 redis-cache[812]: {d['msg']} trace_id={d['trace']} version={self.version('redis-cache', ts)} env=production"
            for ts, d in self.lines["redis-cache"]) + "\n"
        # auth: apache combined (+ request_time)
        rows = []
        for ts, d in self.lines["auth-service"]:
            status = 200 if d["sev"] in ("INFO", "DEBUG") else 401 if d["sev"] == "ERROR" else 403 if d["sev"] == "FATAL" else 200
            lat = (d["lat"] or 90) / 1000
            rows.append(f'10.1.{self.rng.randint(0, 9)}.{self.rng.randint(1, 250)} - - [{ts:%d/%b/%Y:%H:%M:%S} +0000] "POST /v1/token HTTP/1.1" {status} {self.rng.randint(200, 900)} "-" "svc-client/1.0" rt={lat:.3f} request_id={d["req"]} version={self.version("auth-service", ts)} env=production')
        out["auth-service.log"] = "\n".join(rows) + "\n"
        # inventory: csv
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["timestamp", "level", "service", "message", "trace_id", "version", "environment"])
        for ts, d in self.lines["inventory-service"]:
            w.writerow([ts.isoformat().replace("+00:00", "Z"), d["sev"], "inventory-service", d["msg"], d["trace"], self.version("inventory-service", ts), "production"])
        out["inventory-service.csv"] = buf.getvalue()
        return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/sample-logs")
    ap.add_argument("--hours", type=float, default=26)
    ap.add_argument("--end", default="now", help="ISO timestamp or 'now'")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-zip", action="store_true")
    a = ap.parse_args()
    end = datetime.now(UTC) if a.end == "now" else datetime.fromisoformat(a.end.replace("Z", "+00:00"))
    end = end.replace(microsecond=0)
    g = Gen(end, a.hours, a.seed)
    g.run()
    files = g.render()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (out / name).write_text(body, encoding="utf-8")
    if not a.no_zip:
        with zipfile.ZipFile(out / "logpilot-demo-logs.zip", "w", zipfile.ZIP_DEFLATED) as z:
            for name, body in files.items():
                z.writestr(name, body)
    total = sum(len(v) for v in g.lines.values())
    print(f"wrote {total:,} records to {out} (end={end.isoformat()}); past outages at "
          + ", ".join(o.isoformat() for o in g.past) + f"; live ramp began {g.now_ramp_start.isoformat()}")


if __name__ == "__main__":
    main()
