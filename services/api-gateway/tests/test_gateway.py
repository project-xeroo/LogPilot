from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from app.agent import slots
from app.agent.tool_router import TOOL_DEFS
from app.auth.deps import Principal
from app.routers import alerts as alerts_router
from shared.config.roles import CROSS_PROJECT_ROLES, PERMISSIONS, Role, has_permission, permissions_for
from shared.config.tools import TOOLS, TOOLS_BY_NAME
from shared.utils import autonomy
from shared.utils.security import create_access_token, decode_token, hash_password, verify_password

import uuid

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
SERVICES = ["checkout-service", "payment-service", "session-worker", "redis-cache"]


def P(role, guided=False):
    return Principal(uuid.uuid4(), uuid.uuid4(), "u@x", "U", role, guided)


# ---- roles (PRD Section 10) -------------------------------------------------------------------------------------------
def test_role_matrix():
    assert has_permission("admin", "users.manage") and not has_permission("sre", "users.manage")
    assert has_permission("sre", "users.promote") and has_permission("sre", "policy.edit")
    assert has_permission("developer", "actions.decide") and has_permission("developer", "logs.upload")
    assert not has_permission("junior_engineer", "actions.decide") and has_permission("junior_engineer", "chat.use")
    assert not any(has_permission("viewer", p) for p in ("logs.upload", "chat.use", "actions.decide", "policy.view", "reports.edit"))
    assert has_permission("viewer", "health.view") and has_permission("viewer", "reports.view") and has_permission("viewer", "alerts.view")
    assert CROSS_PROJECT_ROLES == {Role.ADMIN, Role.SRE}
    assert set(permissions_for("admin")) == set(PERMISSIONS)


def test_junior_and_guided_approval_guardrails():
    with pytest.raises(HTTPException) as e:
        alerts_router._guard_decide(P("junior_engineer"), high_risk=False)
    assert e.value.status_code == 403 and "Junior" in e.value.detail
    with pytest.raises(HTTPException):
        alerts_router._guard_decide(P("developer", guided=True), high_risk=True)
    alerts_router._guard_decide(P("developer", guided=True), high_risk=False)
    alerts_router._guard_decide(P("sre"), high_risk=True)
    with pytest.raises(HTTPException):
        alerts_router._guard_decide(P("viewer"))


# ---- the 14-tool registry (PRD Section 5) -----------------------------------------------------------------------------
def test_all_fourteen_tools_and_autonomy_levels():
    numbered = [t for t in TOOLS if t.number.isdigit()]
    assert [t.number for t in numbered] == [f"{i:02d}" for i in range(1, 15)]
    assert TOOLS_BY_NAME["proactive_failure_forecasting"].flagship
    assert TOOLS_BY_NAME["recommended_actions"].default_tier.value == "propose_only"
    assert TOOLS_BY_NAME["log_search"].default_tier.value == "read_only"
    assert TOOLS_BY_NAME["incident_report_generation"].human_review == "human sign-off"
    assert {"log_search", "health_state", "root_cause_analysis", "incident_report_generation", "deployment_comparison", "proactive_failure_forecasting"} <= set(TOOL_DEFS)


def test_autonomy_downgrades_to_propose_only_instead_of_blocking(monkeypatch):
    def fake(tier, **kw):
        return lambda session, org, tool, env: {"tool_name": tool, "scope": "*", "tier": tier, "min_confidence": kw.get("mc", 0.0), "requires_approval": kw.get("ra", False), "enabled": kw.get("en", True), "source": "policy"}

    monkeypatch.setattr(autonomy, "resolve_policy", fake("propose_only"))
    d = autonomy.evaluate(None, None, "pre_incident_alerts", requested_tier="autonomous_policy_bounded")
    assert not d.autonomous and d.downgraded and d.effective_tier == "propose_only"
    monkeypatch.setattr(autonomy, "resolve_policy", fake("autonomous_policy_bounded"))
    assert autonomy.evaluate(None, None, "pre_incident_alerts", requested_tier="autonomous_policy_bounded").autonomous
    monkeypatch.setattr(autonomy, "resolve_policy", fake("autonomous_policy_bounded", mc=0.9))
    assert not autonomy.evaluate(None, None, "pre_incident_alerts", requested_tier="autonomous_policy_bounded", confidence=0.5).autonomous
    monkeypatch.setattr(autonomy, "resolve_policy", fake("autonomous_policy_bounded", en=False))
    assert autonomy.evaluate(None, None, "pre_incident_alerts", requested_tier="autonomous_policy_bounded").downgraded
    # PII redaction is mandatory: no policy can change it
    monkeypatch.setattr(autonomy, "resolve_policy", fake("read_only", en=False))
    assert autonomy.evaluate(None, None, "pii_redaction", requested_tier="autonomous_background").autonomous


# ---- auth -------------------------------------------------------------------------------------------------------------------
def test_jwt_has_configurable_expiry_and_passwords_are_hashed():
    uid, org = uuid.uuid4(), uuid.uuid4()
    tok, ttl = create_access_token(uid, org, "sre", expires_minutes=5)
    claims = decode_token(tok)
    assert ttl == 300 and claims["sub"] == str(uid) and claims["role"] == "sre" and claims["exp"] - claims["iat"] == 300
    h = hash_password("correct horse battery")
    assert h != "correct horse battery" and verify_password("correct horse battery", h) and not verify_password("wrong", h)


# ---- intents & slots --------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,intent", [
    ("Why did checkout fail at 3am?", "rca"), ("What is the root cause of the outage?", "rca"),
    ("Which services are most likely to fail next?", "risk"), ("Why is payment-service flagged?", "risk"), ("What should I do about redis-cache?", "risk"),
    ("How is everything doing right now?", "health"), ("Show me the top problem services", "health"),
    ('Show me logs containing "pool exhausted"', "search"), ("find errors in checkout-service", "search"),
    ("Did the latest deployment of payment-service cause regressions?", "deployment"),
    ("Write an incident report for checkout", "report"), ("Show the pre-mortem for redis-cache", "report"),
    ("What does connection reset by peer mean?", "explain"), ("hello there", "qa"),
])
def test_intent_classification(text, intent):
    assert slots.classify(text) == intent


def test_slot_extraction():
    s = slots.extract("Why did checkout fail at 3am?", SERVICES, NOW)
    assert s.service == "checkout-service" and s.start.hour == 2 and s.end.hour == 3 and s.end.minute == 45
    s = slots.extract("errors in redis-cache in the last 30 minutes", SERVICES, NOW)
    assert s.service == "redis-cache" and s.window_minutes == 30 and (s.end - s.start).seconds == 1800
    assert slots.extract("what happened yesterday", SERVICES, NOW).start.day == 26
    s = slots.extract('search for "connection refused" on v2.3.1', SERVICES, NOW)
    assert s.quoted == "connection refused" and s.version == "v2.3.1"
    assert slots.search_query('Show me logs containing "pool exhausted"', slots.extract('Show me logs containing "pool exhausted"', SERVICES, NOW)) == ("pool exhausted", True)
