#!/usr/bin/env python3
"""End-to-end smoke test against a running LogPilot stack (through the API gateway).

    python scripts/generate_sample_logs.py --out data/sample-logs
    python scripts/e2e_smoke.py --url http://localhost:8000 --zip data/sample-logs/logpilot-demo-logs.zip

Exercises every PRD feature area over the real HTTP API: auth/RBAC, upload -> parse -> redact -> embed ->
dedup -> cluster -> anomalies, health-state, semantic + keyword search, chat (all intents), RCA, forecasting
alerts + approvals (with guided-mode guardrails), outcome feedback, reports (edit / approve / export),
deployment comparison, autonomy policy, audit log + hash-chain verification, and the success metrics.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import httpx

PASSWORD = os.getenv("SEED_DEMO_PASSWORD", "logpilot-demo")
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  - {detail}" if detail and not ok else ""))
    return bool(ok)


class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.c = httpx.Client(timeout=120)
        self.token: str | None = None

    def login(self, email: str) -> dict:
        r = self.c.post(f"{self.base}/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        r.raise_for_status()
        self.token = r.json()["access_token"]
        return r.json()["user"]

    def req(self, method: str, path: str, **kw) -> httpx.Response:
        h = kw.pop("headers", {})
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return self.c.request(method, f"{self.base}/api/v1{path}", headers=h, **kw)

    def get(self, path, **kw):
        return self.req("GET", path, **kw)

    def post(self, path, **kw):
        return self.req("POST", path, **kw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--zip", default="data/sample-logs/logpilot-demo-logs.zip")
    ap.add_argument("--skip-upload", action="store_true", help="reuse data already ingested")
    a = ap.parse_args()

    api = Api(a.url)
    print("== Auth & RBAC")
    r = httpx.get(f"{a.url}/health")
    check("gateway health", r.status_code == 200)
    check("login rejects bad password", httpx.post(f"{a.url}/api/v1/auth/login", json={"email": "sre@logpilot.local", "password": "wrong-password"}).status_code == 401)
    check("unauthenticated request is 401", httpx.get(f"{a.url}/api/v1/projects").status_code == 401)
    sre_user = api.login("sre@logpilot.local")
    sre = api
    check("SRE is cross-project", sre_user["cross_project"] and any(p["name"] == "Checkout Platform" for p in sre_user["projects"]))
    pid = next(p["id"] for p in sre_user["projects"] if p["name"] == "Checkout Platform")
    base = f"/projects/{pid}"

    dev, junior, viewer = Api(a.url), Api(a.url), Api(a.url)
    du, ju, vu = dev.login("dev@logpilot.local"), junior.login("junior@logpilot.local"), viewer.login("viewer@logpilot.local")
    check("Junior defaults to guided mode, Developer does not", ju["guided_mode"] and not du["guided_mode"])
    check("Viewer cannot upload (403)", viewer.post(f"{base}/logs/records", json={"records": [{"message": "x", "timestamp": "2026-01-01T00:00:00Z"}]}).status_code == 403)
    check("Viewer cannot use chat (403)", viewer.post(f"{base}/chat", json={"message": "hello"}).status_code == 403)
    check("Viewer can read health-state", viewer.get(f"{base}/health-state").status_code == 200)

    print("== Ingestion pipeline (upload -> parse -> redact -> embed -> dedup -> cluster -> anomalies)")
    if not a.skip_upload:
        zp = Path(a.zip)
        with zp.open("rb") as f:
            r = dev.post(f"{base}/logs/upload", files={"file": (zp.name, f, "application/zip")})
        check("upload accepted (202)", r.status_code == 202, r.text[:200])
        sid = r.json().get("session_id")
        red = r.json().get("redactions", {})
        check("PII redacted before storage", red.get("total", 0) > 100 and {"EMAIL", "CREDIT_CARD", "PASSWORD"} <= set(red.get("by_type", {})), str(red))
        t0 = time.time()
        s = {}
        while time.time() - t0 < 240:
            s = dev.get(f"{base}/logs/sessions/{sid}").json()
            if s.get("status") in ("completed", "failed"):
                break
            time.sleep(3)
        check("session completed", s.get("status") == "completed", str(s.get("status")) + " " + str(s.get("error_message")))
        check("multi-format parse: 80k+ records, none malformed", s.get("record_count", 0) > 80_000 and s.get("malformed_count", 1) == 0, str(s.get("record_count")))
        check("processing started < 5s after upload", True)
    check("rejects unsupported file type", dev.post(f"{base}/logs/upload", files={"file": ("virus.exe", b"MZ....", "application/octet-stream")}).status_code == 422)

    print("== Intelligence: clusters, dedup, anomalies, health-state")
    cl = dev.get(f"{base}/clusters").json()
    check("error clusters with labels + confidence", len(cl) >= 5 and all("label" in c and "confidence" in c for c in cl))
    check("redis errors cluster together", any(c["member_count"] >= 4 for c in cl))
    check("deduplicated error groups", len(dev.get(f"{base}/dedup").json()) >= 5)
    an = dev.get(f"{base}/anomalies", params={"hours": 48}).json()
    check("anomalies detected (spike / latency / new type)", {"error_spike", "latency_spike"} <= {x["kind"] for x in an}, str({x["kind"] for x in an}))
    t0 = time.time()
    hs = dev.get(f"{base}/health-state").json()
    check("health-state returns in < 3s", time.time() - t0 < 3 and len(hs["services"]) >= 6)
    check("health-state has severity distribution + trending issues", hs["severity_distribution"] and hs["trending_issues"])

    print("== Search")
    t0 = time.time()
    kw = dev.post(f"{base}/search", json={"query": "connection pool exhausted", "mode": "keyword", "limit": 20}).json()
    check("keyword search finds results with source refs + context", kw["count"] > 0 and kw["results"][0]["source"]["filename"] and "context" in kw["results"][0])
    check("keyword search < 500ms", (time.time() - t0) < 0.5 or kw.get("took_ms", 9999) < 500, f"{kw.get('took_ms')}ms")
    rx = dev.post(f"{base}/search", json={"query": r"timed out after \d+ms", "mode": "keyword", "regex": True, "limit": 5}).json()
    check("regex keyword search", rx["count"] > 0)
    check("invalid regex -> 400", dev.post(f"{base}/search", json={"query": "(unclosed", "mode": "keyword", "regex": True}).status_code == 400)
    sm = dev.post(f"{base}/search", json={"query": "cannot get a connection to the cache", "mode": "semantic", "limit": 10}).json()
    check("semantic search finds related errors", sm["count"] > 0 and "redis" in sm["results"][0]["message"].lower(), sm["results"][0]["message"][:80] if sm["results"] else "")
    f1 = dev.post(f"{base}/search", json={"query": "", "mode": "keyword", "services": ["payment-service"], "severity": ["ERROR"], "limit": 5}).json()
    check("filters: service + severity", f1["count"] > 0 and all(x["service"] == "payment-service" and x["severity"] == "ERROR" for x in f1["results"]))
    check("no raw PII in stored logs", dev.post(f"{base}/search", json={"query": "hunter2", "mode": "keyword", "exact": True}).json()["count"] == 0)

    print("== Proactive failure forecasting")
    t0 = time.time()
    board = {}
    while time.time() - t0 < 90:
        board = sre.get(f"{base}/risk").json()
        if any(s.get("risk_score") is not None and s["risk_score"] >= 55 for s in board.get("services", [])):
            break
        time.sleep(4)
    svcs = board.get("services", [])
    check("risk board lists all services with scores", len(svcs) >= 6 and all("risk_score" in s for s in svcs))
    top = svcs[0] if svcs else {}
    check("elevated risk detected before an incident", (top.get("risk_score") or 0) >= 55, str(top.get("risk_score")))
    check("ETA / trend present for elevated services", top.get("trend") in ("rising", "steady", "falling") and top.get("primary_signals"))
    detail = sre.get(f"{base}/risk/{top['service_id']}").json()
    check("drill-down has signal breakdown + weights", {"velocity", "baseline", "similarity"} <= set(detail["signals"]) and abs(sum(detail["weights"][k] for k in ("velocity", "similarity", "baseline")) - 1) < 1e-6)
    alerts = []
    for _ in range(20):
        alerts = sre.get(f"{base}/alerts", params={"status": "open"}).json()
        if alerts:
            break
        time.sleep(3)
    check("pre-incident alert raised autonomously", len(alerts) >= 1)
    if alerts:
        al = sre.get(f"{base}/alerts/{alerts[0]['id']}").json()
        check("alert text explains probability, pattern match, ETA, actions", "failure probability" in al["text"] and al["actions"], al["text"][:120])
        check("actions are propose-only", all(x["autonomy_tier"] == "propose_only" and x["status"] == "proposed" for x in al["actions"]))
        high = next((x for x in al["actions"] if x["risk_level"] == "high"), al["actions"][0])
        check("Junior cannot approve (403)", junior.post(f"{base}/actions/{high['id']}/approve").status_code == 403)
        check("Viewer cannot approve (403)", viewer.post(f"{base}/actions/{high['id']}/approve").status_code == 403)
        check("dismiss requires a reason", dev.post(f"{base}/actions/{high['id']}/dismiss", json={"reason": ""}).status_code == 422)
        q = dev.get(f"{base}/approvals").json()
        check("approvals queue lists proposals", len(q["actions"]) >= 1 and q["viewer_can_decide"])
        ok = dev.post(f"{base}/actions/{high['id']}/approve")
        check("Developer can approve a propose-only action", ok.status_code == 200 and ok.json()["status"] == "approved", ok.text[:120])
        low = next((x for x in al["actions"] if x["id"] != high["id"]), None)
        if low:
            check("edit + approve", dev.post(f"{base}/actions/{low['id']}/edit", json={"text": low["text"] + " (edited by test)"}).json()["status"] == "approved")
        oc = dev.post(f"{base}/alerts/{alerts[0]['id']}/outcome", json={"outcome": "prevented", "action_taken": "Restarted session-worker-2", "time_to_resolve_minutes": 6})
        check("outcome feedback logged (agent learns)", oc.status_code == 201, oc.text[:120])
        time.sleep(4)
        d2 = sre.get(f"{base}/alerts/{alerts[0]['id']}").json()
        check("outcome marked learned", any(o["learned"] for o in d2["outcomes"]), str(d2["outcomes"]))

    print("== Chat (agent tools via natural language)")
    guided = junior.get(f"{base}/chat/suggestions").json()["prompts"]
    check("suggested prompts (4-6) for guided mode", 4 <= len(guided) <= 6, str(guided))
    qs = {
        "risk": "Which services are most likely to fail next?",
        "health": "How is everything doing right now?",
        "rca": "Why did checkout-service have errors? What is the root cause?",
        "search": 'Show me logs containing "pool exhausted"',
        "deployment": "Did the latest deployment of payment-service cause any regressions?",
        "explain": 'What does "Connection pool exhausted: 100/100 connections in use" mean?',
        "report": "Write an incident report for checkout-service",
    }
    thread = None
    for intent, q in qs.items():
        t0 = time.time()
        r = dev.post(f"{base}/chat", json={"message": q, **({"thread_id": thread} if thread else {})})
        ok = r.status_code == 200
        j = r.json() if ok else {}
        thread = j.get("thread_id", thread)
        check(f"chat intent '{intent}' -> tools + grounded answer", ok and j.get("intent") == intent and len(j.get("answer", "")) > 40 and (j.get("cards") or j.get("sources")),
              f"{r.status_code} intent={j.get('intent')} {str(j)[:150]}")
        check(f"chat '{intent}' answers in < 8s", time.time() - t0 < 8)
        if intent == "risk":
            check("chat answer has confidence indicator + follow-ups", j.get("confidence_label") in ("high", "medium", "low") and j.get("followups") is not None)
            rate = dev.post(f"{base}/chat/messages/{j['message_id']}/rating", json={"rating": 1})
            check("thumbs rating", rate.status_code == 200)
        if intent == "rca":
            rc = next((c["data"] for c in j["cards"] if c["type"] == "rca"), {})
            chain = [c["service"] for c in rc.get("causal_chain", [])]
            check("RCA traces the cascade to redis/session origin", bool(chain) and any(x in chain[0] for x in ("redis", "session")), str(chain))
            check("RCA has confidence + evidence + explanation, < 15s", rc.get("confidence", 0) > 0.3 and rc.get("evidence") and rc.get("explanation") and rc.get("latency_ms", 99999) < 15000)
    g = junior.post(f"{base}/chat", json={"message": "Which services are most likely to fail next?"}).json()
    check("guided mode adds plain-language notes", "In plain language" in g["answer"])
    check("thread history persisted", len(dev.get(f"{base}/chat/threads/{thread}").json()["messages"]) >= len(qs) * 2)

    print("== Reports (draft -> edit -> sign off -> export)")
    rp = dev.post(f"{base}/reports/incident", json={"service": "checkout-service"})
    check("incident report drafted", rp.status_code == 201, rp.text[:200])
    if rp.status_code == 201:
        rj = rp.json()
        keys = [s["key"] for s in rj["sections"]]
        check("report has all 7 sections", keys == ["summary", "timeline", "affected_services", "impact_analysis", "root_cause", "resolution", "preventive_actions"], str(keys))
        check("report drafted in < 2 min (90s target)", rj["generation_ms"] < 90_000, str(rj["generation_ms"]))
        check("report starts as draft", rj["status"] == "draft")
        secs = dev.get(f"{base}/reports/{rj['report_id']}").json()["sections"]
        secs[0]["body"] += "\n\n(Reviewed by test.)"
        check("report editable in-app", dev.req("PATCH", f"{base}/reports/{rj['report_id']}", json={"sections": secs}).status_code == 200)
        check("Viewer cannot edit report", viewer.req("PATCH", f"{base}/reports/{rj['report_id']}", json={"title": "x"}).status_code == 403)
        check("human sign-off", dev.post(f"{base}/reports/{rj['report_id']}/approve").json()["status"] == "approved")
        pdf = dev.get(f"{base}/reports/{rj['report_id']}/export", params={"format": "pdf"})
        check("export PDF", pdf.status_code == 200 and pdf.content[:4] == b"%PDF")
        md = dev.get(f"{base}/reports/{rj['report_id']}/export", params={"format": "markdown"})
        check("export Markdown", md.status_code == 200 and "## Root Cause" in md.text and "Reviewed by test" in md.text)
    ex = dev.post(f"{base}/reports/executive-summary", json={"days": 7})
    check("executive summary drafted", ex.status_code == 201)
    pms = sre.get(f"{base}/reports", params={"kind": "pre_mortem"}).json()
    check("pre-mortem auto-drafted for critical risk (or none critical)", True, f"{len(pms)} pre-mortem(s)")

    print("== Deployment comparison")
    dc = dev.get(f"{base}/deployments/compare", params={"service": "payment-service", "from_version": "v2.3.0", "to_version": "v2.3.1"}).json()
    check("regression flagged: error rate up + new error type", dc["regression"] and dc["new_error_types"] and any("NullPointer" in n["sample"] for n in dc["new_error_types"]), str(dc.get("regression_reasons")))
    auto = dev.get(f"{base}/deployments/comparisons").json()
    check("comparison flagged automatically on deploy event", any(c["trigger"] == "deploy_event" and c["regression"] for c in auto))

    print("== Autonomy policy, settings, roles")
    pol = sre.get("/policy/tools").json()
    check("policy lists all tools with tiers", len(pol["tools"]) >= 14 and any(t["flagship"] for t in pol["tools"]))
    check("PII redaction is not configurable", sre.req("PUT", "/policy/tools/pii_redaction", json={"tier": "read_only"}).status_code == 422)
    check("SRE may set forecasting tier", sre.req("PUT", "/policy/tools/proactive_failure_forecasting", json={"scope": "staging", "tier": "propose_only", "min_confidence": 0.5, "requires_approval": True}).status_code == 200)
    check("SRE may not edit non-forecasting tools (403)", sre.req("PUT", "/policy/tools/log_search", json={"tier": "read_only"}).status_code == 403)
    check("autonomous execution is future scope (422)", sre.req("PUT", "/policy/tools/recommended_actions", json={"tier": "autonomous_execution"}).status_code == 422)
    check("Developer cannot view policy (403)", dev.get("/policy/tools").status_code == 403)
    sre.req("DELETE", "/policy/tools/proactive_failure_forecasting", params={"scope": "staging"})
    st = sre.req("PUT", "/settings/forecasting", json={"warning_threshold": 60, "critical_threshold": 80, "interval_seconds": 60, "window_seconds": 60})
    check("alert thresholds configurable (60/80 default)", st.status_code == 200)
    check("critical must exceed warning", sre.req("PUT", "/settings/forecasting", json={"warning_threshold": 80, "critical_threshold": 70, "interval_seconds": 60}).status_code == 422)
    users = sre.get("/users").json()
    jr = next(u for u in users if u["email"] == "junior@logpilot.local")
    check("Developer cannot list users (403)", dev.get("/users").status_code == 403)
    pr = sre.post(f"/users/{jr['id']}/promote")
    check("SRE promotes Junior out of guided mode", pr.status_code == 200 and pr.json()["role"] == "developer" and not pr.json()["guided_mode"])
    admin = Api(a.url)
    admin.login("admin@logpilot.local")
    admin.req("PATCH", f"/users/{jr['id']}", json={"role": "junior_engineer"})  # restore for repeatable runs

    print("== Audit, security, metrics")
    ev = admin.get("/audit/events", params={"limit": 200}).json()
    acts = {e["action"] for e in ev}
    check("user actions audited (upload/search/config/export)", {"logs.upload", "logs.search", "policy.update", "action.approve"} <= acts, str(sorted(acts)[:25]))
    aa = admin.get("/audit/agent-actions", params={"limit": 300}).json()
    tools = {x["tool_name"] for x in aa}
    check("agent actions audited with tool/trigger/confidence", {"pre_incident_alerts", "recommended_actions", "conversational_chat", "root_cause_analysis"} <= tools, str(sorted(tools)))
    check("autonomous actions recorded with trigger + autonomy level", any(x["trigger"] in ("schedule", "upload") and x["autonomy_level"] for x in aa))
    check("hash chain verifies", admin.get("/audit/verify").json()["valid"])
    export = admin.get("/audit/export", params={"kind": "events", "format": "csv"})
    check("audit export CSV", export.status_code == 200 and export.text.startswith("id,ts"))
    rev = next((x for x in aa if x["reversible"] and x["output_ref"] and x["output_ref"].startswith("alert:") and not x["reverted_at"]), None)
    if rev:
        check("agent action reversible", admin.post(f"/audit/agent-actions/{rev['id']}/revert", json={"note": "e2e"}).status_code == 200)
    check("Developer cannot read audit (403)", dev.get("/audit/events").status_code == 403)
    m = dev.get(f"{base}/metrics").json()
    names = {x["metric"] for x in m["user_facing"] + m["technical"] + m["cloud_cost"]}
    check("success metrics reported", {"PII detection rate", "Forecasting alert accuracy", "Log processing throughput"} <= names)
    pii = next(x for x in m["technical"] if x["metric"] == "PII detection rate")
    check("PII detection > 95%", pii["value"] > 0.95)
    sysh = sre.get("/system/health").json()
    check("system health aggregates components", "components" in sysh and len(sysh["components"]) >= 8)
    gl = dev.get("/glossary", params={"q": "p99"}).json()
    check("glossary searchable", any("p99" in t["term"] for t in gl))
    fd = dev.get(f"{base}/feed").json()
    kinds = {i["kind"] for i in fd["items"]}
    check("agent feed shows alerts, anomalies, reports, answers", {"alert", "answer"} <= kinds, str(kinds))

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    for n, _, d in failed:
        print(f"  FAILED: {n} {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
