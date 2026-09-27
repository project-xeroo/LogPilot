"""Autonomy policy resolution & enforcement (PRD Section 8).

`evaluate()` is the single decision point every autonomous path uses (tool router, forecasting
loop, deployment comparison, ...). Rules, straight from 8.3:

* An action inside the granted tier, above `min_confidence`, not flagged `requires_approval`
  runs autonomously.
* Anything outside the granted tier is *downgraded to propose-only and routed to a human
  queue*, never silently blocked.
* PII redaction is mandatory: it cannot be dialled down, disabled, or gated on approval.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.config.tools import TIER_ORDER, TOOLS, TOOLS_BY_NAME, Tier
from shared.models import AutonomyPolicy

MANDATORY_TOOLS = {"pii_redaction"}


def tier_rank(tier: str | Tier) -> int:
    return TIER_ORDER.index(Tier(tier))


@dataclass
class PolicyDecision:
    tool_name: str
    requested_tier: str
    granted_tier: str
    effective_tier: str
    autonomous: bool  # may proceed without a human
    requires_approval: bool
    min_confidence: float
    downgraded: bool
    reason: str = ""


def resolve_policy(session: Session, org_id: uuid.UUID | None, tool_name: str, environment: str | None) -> dict:
    """Most specific scope wins: exact environment, then '*', then the tool's built-in default."""
    spec = TOOLS_BY_NAME.get(tool_name)
    default = {
        "tool_name": tool_name,
        "scope": "*",
        "tier": (spec.default_tier.value if spec else Tier.READ_ONLY.value),
        "min_confidence": 0.0,
        "requires_approval": False,
        "enabled": True,
        "source": "default",
    }
    if tool_name in MANDATORY_TOOLS:
        return {**default, "tier": Tier.AUTONOMOUS_BACKGROUND.value, "source": "mandatory"}
    if org_id is None:
        return default
    rows = session.execute(
        select(AutonomyPolicy).where(AutonomyPolicy.org_id == org_id, AutonomyPolicy.tool_name == tool_name)
    ).scalars().all()
    by_scope = {r.scope: r for r in rows}
    row = by_scope.get(environment or "") or by_scope.get("*")
    if not row:
        return default
    return {
        "tool_name": tool_name,
        "scope": row.scope,
        "tier": row.tier,
        "min_confidence": row.min_confidence,
        "requires_approval": row.requires_approval,
        "enabled": row.enabled,
        "source": "policy",
    }


def evaluate(
    session: Session,
    org_id: uuid.UUID | None,
    tool_name: str,
    *,
    requested_tier: str | Tier | None = None,
    environment: str | None = None,
    confidence: float | None = None,
) -> PolicyDecision:
    spec = TOOLS_BY_NAME.get(tool_name)
    requested = Tier(requested_tier or (spec.default_tier if spec else Tier.READ_ONLY))
    policy = resolve_policy(session, org_id, tool_name, environment)
    granted = Tier(policy["tier"])

    if tool_name in MANDATORY_TOOLS:
        return PolicyDecision(tool_name, requested.value, granted.value, requested.value, True, False, 0.0, False, "mandatory")

    reasons = []
    if not policy["enabled"]:
        reasons.append("tool disabled by policy")
    if tier_rank(requested) > tier_rank(granted):
        reasons.append(f"requested tier '{requested.value}' exceeds granted tier '{granted.value}'")
    if confidence is not None and confidence < policy["min_confidence"]:
        reasons.append(f"confidence {confidence:.2f} below policy minimum {policy['min_confidence']:.2f}")
    if policy["requires_approval"] and requested != Tier.READ_ONLY:
        reasons.append("policy requires human approval")

    if reasons and requested != Tier.READ_ONLY:
        return PolicyDecision(
            tool_name, requested.value, granted.value, Tier.PROPOSE_ONLY.value, False, True,
            policy["min_confidence"], True, "; ".join(reasons),
        )
    return PolicyDecision(
        tool_name, requested.value, granted.value, requested.value, True, policy["requires_approval"],
        policy["min_confidence"], False, "within granted autonomy",
    )


def seed_policies(session: Session, org_id: uuid.UUID) -> int:
    """Create default rows (scope '*') for every tool that has none. Returns rows created."""
    existing = {
        r for (r,) in session.execute(
            select(AutonomyPolicy.tool_name).where(AutonomyPolicy.org_id == org_id, AutonomyPolicy.scope == "*")
        )
    }
    created = 0
    for t in TOOLS:
        if t.name in existing:
            continue
        session.add(
            AutonomyPolicy(
                org_id=org_id,
                tool_name=t.name,
                scope="*",
                tier=t.default_tier.value,
                min_confidence=0.0,
                # propose-only actions always sit in the human queue
                requires_approval=t.default_tier == Tier.PROPOSE_ONLY,
                enabled=True,
            )
        )
        created += 1
    session.flush()
    return created
