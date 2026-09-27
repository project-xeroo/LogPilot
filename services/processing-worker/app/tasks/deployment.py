"""Deployment Comparison Tool (tool 12): autonomous on a detected deployment event."""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import select

from shared.config.tools import Tier
from shared.models import DeploymentComparison, DeploymentEvent, Project
from shared.utils.analytics import compare_deployments
from shared.utils.audit import record_agent_action
from shared.utils.autonomy import evaluate
from shared.utils.celery_factory import RETRY_KWARGS
from shared.utils.clients import ServiceError, ai
from shared.utils.db import session_scope
from shared.utils.events import add_feed_item, publish_event

log = logging.getLogger("logpilot.deployment")


def register(celery_app):
    @celery_app.task(name="deployment.compare_on_deploy", bind=True, **RETRY_KWARGS)
    def compare_on_deploy(self, project_id: str, service: str, environment: str, version: str) -> dict:
        pid = uuid.UUID(project_id)
        with session_scope() as db:
            cur = db.execute(
                select(DeploymentEvent).where(DeploymentEvent.project_id == pid, DeploymentEvent.service == service,
                                              DeploymentEvent.version == version, DeploymentEvent.environment == environment)
            ).scalar_one_or_none()
            if cur is None:
                return {"skipped": "unknown deployment"}
            prev = db.execute(
                select(DeploymentEvent)
                .where(DeploymentEvent.project_id == pid, DeploymentEvent.service == service, DeploymentEvent.environment == environment,
                       DeploymentEvent.deployed_at < cur.deployed_at, DeploymentEvent.version != version)
                .order_by(DeploymentEvent.deployed_at.desc()).limit(1)
            ).scalar_one_or_none()
            if prev is None:
                cur.compared = True
                return {"skipped": "no earlier version to compare with"}
            try:
                result = compare_deployments(db, pid, service, prev.version, version, environment)
            except ValueError as exc:
                return {"skipped": str(exc)}
            org = db.get(Project, pid).org_id
            decision = evaluate(db, org, "deployment_comparison", requested_tier=Tier.AUTONOMOUS_POLICY_BOUNDED, environment=environment)
            narrative = _narrate(result)
            comp = DeploymentComparison(
                project_id=pid, service=service, environment=environment, from_version=prev.version, to_version=version,
                result={**result, "narrative": narrative}, regression=result["regression"], trigger="deploy_event",
            )
            db.add(comp)
            cur.compared = True
            db.flush()
            record_agent_action(
                db, tool_name="deployment_comparison", trigger="deploy_event", autonomy_level=decision.effective_tier,
                org_id=org, project_id=pid, status="executed" if decision.autonomous else "proposed",
                downgraded_from=decision.requested_tier if decision.downgraded else None,
                input_ref=f"deployment:{service}:{version}", output_ref=f"deployment_comparison:{comp.id}",
                summary=f"Compared {service} {prev.version} -> {version}: " + ("REGRESSION - " + "; ".join(result["regression_reasons"]) if result["regression"] else "no regression"),
                details={"reasons": result["regression_reasons"], "policy": decision.reason},
            )
            if result["regression"]:
                add_feed_item(
                    db, project_id=pid, org_id=org, kind="deployment", severity="warning", service=service,
                    title=f"Regression flagged: {service} {prev.version} -> {version}",
                    body=narrative + ("" if decision.autonomous else " (Flag downgraded to propose-only by policy: needs human review.)"),
                    ref_type="deployment_comparison", ref_id=comp.id, meta={"reasons": result["regression_reasons"]}, interrupt=False,
                )
                publish_event("deployment.regression", {"service": service, "from": prev.version, "to": version,
                                                        "reasons": result["regression_reasons"], "comparison_id": str(comp.id)},
                              project_id=pid, org_id=org)
            return {"comparison_id": str(comp.id), "regression": result["regression"]}

    return {"compare_on_deploy": compare_on_deploy}


def _narrate(result: dict) -> str:
    base = (f"{result['service']} {result['from']['version']} -> {result['to']['version']}: error rate "
            f"{result['from']['error_rate']:.2%} -> {result['to']['error_rate']:.2%}")
    if result["regression"]:
        base += ". Regression: " + "; ".join(result["regression_reasons"]) + "."
    else:
        base += ". No regression detected."
    try:
        res = ai.post("/internal/narrate-deployment", json={"comparison": _slim(result), "summary": base}, timeout=20)
        return res.get("narrative") or base
    except ServiceError:
        return base


def _slim(result: dict) -> dict:
    return {k: result[k] for k in ("service", "deltas", "regression", "regression_reasons", "health")} | {
        "from": {k: v for k, v in result["from"].items() if k != "error_templates"},
        "to": {k: v for k, v in result["to"].items() if k != "error_templates"},
        "new_error_types": result["new_error_types"][:5],
    }
