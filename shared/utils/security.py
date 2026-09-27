"""JWT auth with configurable expiry, password hashing, service-to-service auth."""
from __future__ import annotations

import hmac
import uuid
from datetime import timedelta
from typing import Any

import bcrypt
import jwt

from shared.config import settings
from shared.models.base import utcnow


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt(rounds=10)).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode()[:72], hashed.encode())
    except ValueError:
        return False


def create_access_token(user_id: uuid.UUID, org_id: uuid.UUID, role: str, expires_minutes: int | None = None) -> tuple[str, int]:
    minutes = expires_minutes or settings.jwt_expire_minutes
    now = utcnow()
    payload = {
        "sub": str(user_id),
        "org": str(org_id),
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
        "typ": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm), minutes * 60


def decode_token(token: str) -> dict[str, Any]:
    """Raises jwt.PyJWTError on invalid/expired tokens."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm], options={"require": ["exp", "sub"]})


def internal_token_ok(candidate: str | None) -> bool:
    return bool(candidate) and hmac.compare_digest(candidate, settings.internal_api_token)
