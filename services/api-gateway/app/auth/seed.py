"""First-run bootstrap: organisation, default autonomy policy, glossary and (when SEED_DEMO=true) one
demo user per role plus a sample project. Idempotent - safe on every start.

DEV/DEMO CREDENTIALS - override with env vars and set SEED_DEMO=false in real deployments."""
from __future__ import annotations

import logging
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.config import settings
from shared.config.glossary import GLOSSARY
from shared.config.roles import Role
from shared.models import GlossaryTerm, OrgSetting, Organization, Project, User, UserProject
from shared.utils.autonomy import seed_policies
from shared.utils.security import hash_password

log = logging.getLogger("logpilot.seed")

ORG_NAME = os.getenv("BOOTSTRAP_ORG", "Acme Reliability")
DEMO_PASSWORD = os.getenv("SEED_DEMO_PASSWORD", "logpilot-demo")
DEMO_USERS = [
    # email, name, role, guided
    ("admin@logpilot.local", "Avery Admin", Role.ADMIN, False),
    ("sre@logpilot.local", "Sam SRE", Role.SRE, False),
    ("dev@logpilot.local", "Dana Developer", Role.DEVELOPER, False),
    ("junior@logpilot.local", "Jo Junior", Role.JUNIOR, True),  # guided mode defaults ON for the Junior role
    ("viewer@logpilot.local", "Val Viewer", Role.VIEWER, False),
]


def seed(db: Session) -> None:
    org = db.execute(select(Organization).where(Organization.name == ORG_NAME)).scalar_one_or_none()
    if org is None:
        org = Organization(name=ORG_NAME)
        db.add(org)
        db.flush()
    seed_policies(db, org.id)
    if db.get(OrgSetting, (org.id, "forecasting")) is None:
        db.add(OrgSetting(org_id=org.id, key="forecasting", value={
            "warning_threshold": settings.forecast_warning_threshold, "critical_threshold": settings.forecast_critical_threshold,
            "interval_seconds": settings.forecast_interval_seconds, "window_seconds": settings.forecast_window_seconds,
        }))
    existing = {t for (t,) in db.execute(select(GlossaryTerm.term))}
    for term, definition, category in GLOSSARY:
        if term not in existing:
            db.add(GlossaryTerm(term=term, definition=definition, category=category, seeded_from="agent"))

    bootstrap_email = os.getenv("BOOTSTRAP_ADMIN_EMAIL")
    if bootstrap_email and not db.execute(select(User).where(User.email == bootstrap_email.lower())).scalar_one_or_none():
        db.add(User(org_id=org.id, email=bootstrap_email.lower(), name="Administrator", role=Role.ADMIN.value,
                    password_hash=hash_password(os.getenv("BOOTSTRAP_ADMIN_PASSWORD", DEMO_PASSWORD)), guided_mode=False))

    if settings.seed_demo:
        proj = db.execute(select(Project).where(Project.org_id == org.id, Project.name == "Checkout Platform")).scalar_one_or_none()
        if proj is None:
            proj = Project(org_id=org.id, name="Checkout Platform", environment="production", description="Demo project: checkout, payment, session, cache, auth and inventory services")
            db.add(proj)
            db.flush()
        for email, name, role, guided in DEMO_USERS:
            u = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
            if u is None:
                u = User(org_id=org.id, email=email, name=name, role=role.value, guided_mode=guided, password_hash=hash_password(DEMO_PASSWORD))
                db.add(u)
                db.flush()
            if role in (Role.DEVELOPER, Role.JUNIOR, Role.VIEWER) and not db.get(UserProject, (u.id, proj.id)):
                db.add(UserProject(user_id=u.id, project_id=proj.id))
    db.flush()
    log.info("seed complete (demo=%s)", settings.seed_demo)
