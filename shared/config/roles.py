"""Roles and permissions (PRD Section 10).

Admin       Full system access.
Developer   Project-level access: upload, search, chat, RCA, health, alerts, reports,
            approve propose-only actions - for assigned projects.
Junior      Same as Developer but guided mode is on by default and approval of
            propose-only actions is withheld until an Admin/SRE promotes the account.
SRE         Cross-project. All Developer capabilities plus alert configuration and
            forecasting autonomy-tier management.
Viewer      Read-only: health-state, search results, reports, alerts.
"""
from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    DEVELOPER = "developer"
    JUNIOR = "junior_engineer"
    SRE = "sre"
    VIEWER = "viewer"


ALL_ROLES = set(Role)
_NOT_VIEWER = ALL_ROLES - {Role.VIEWER}

# permission -> roles that hold it
PERMISSIONS: dict[str, set[Role]] = {
    # perceive / read
    "health.view": ALL_ROLES,
    "risk.view": ALL_ROLES,
    "alerts.view": ALL_ROLES,
    "reports.view": ALL_ROLES,
    "reports.export": ALL_ROLES,
    "logs.search": ALL_ROLES,
    "feed.view": ALL_ROLES,
    "deployments.view": ALL_ROLES,
    "glossary.view": ALL_ROLES,
    "logs.view_sessions": ALL_ROLES,
    # act
    "logs.upload": {Role.ADMIN, Role.DEVELOPER, Role.JUNIOR, Role.SRE},
    "chat.use": _NOT_VIEWER,
    "rca.run": _NOT_VIEWER,
    "reports.generate": {Role.ADMIN, Role.DEVELOPER, Role.SRE, Role.JUNIOR},
    "reports.edit": {Role.ADMIN, Role.DEVELOPER, Role.SRE},
    "deployments.compare": _NOT_VIEWER,
    "alerts.outcome": _NOT_VIEWER,
    # approvals (junior withheld; guided-mode high-risk check is enforced in the router)
    "actions.decide": {Role.ADMIN, Role.DEVELOPER, Role.SRE},
    # configuration
    "services.configure": {Role.ADMIN, Role.SRE},
    "settings.thresholds": {Role.ADMIN, Role.SRE},
    "policy.view": {Role.ADMIN, Role.SRE},
    "policy.edit": {Role.ADMIN, Role.SRE},  # SRE limited to forecasting tools (see below)
    "settings.provider": {Role.ADMIN},
    "webhooks.manage": {Role.ADMIN, Role.SRE},
    "api_keys.manage": {Role.ADMIN, Role.SRE},
    "users.manage": {Role.ADMIN},
    "users.promote": {Role.ADMIN, Role.SRE},
    "projects.manage": {Role.ADMIN},
    "audit.view": {Role.ADMIN, Role.SRE},
    "audit.export": {Role.ADMIN, Role.SRE},
    "audit.revert": {Role.ADMIN, Role.SRE},
}

# Roles with access to every project regardless of assignment.
CROSS_PROJECT_ROLES = {Role.ADMIN, Role.SRE}

# Permissions an API key may be granted (see shared.models.apikey.ApiKey.scopes). Deliberately a small,
# curated subset of PERMISSIONS - never anything that mutates state beyond ingesting logs, and never
# anything cross-project or administrative, regardless of who created the key.
API_KEY_SCOPES = ["logs.upload", "logs.view_sessions", "logs.search", "health.view", "risk.view"]

# Tools whose autonomy tier an SRE (not only Admin) may configure.
SRE_POLICY_TOOLS = {"proactive_failure_forecasting", "pre_incident_alerts", "recommended_actions", "pre_mortem_reports"}


def has_permission(role: str, permission: str) -> bool:
    try:
        return Role(role) in PERMISSIONS.get(permission, set())
    except ValueError:
        return False


def permissions_for(role: str) -> list[str]:
    try:
        r = Role(role)
    except ValueError:
        return []
    return sorted(p for p, roles in PERMISSIONS.items() if r in roles)
