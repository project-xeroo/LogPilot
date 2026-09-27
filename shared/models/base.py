from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, mapped_column


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


def uuid_pk():
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def uuid_fk(target: str, **kw):
    from sqlalchemy import ForeignKey

    return mapped_column(UUID(as_uuid=True), ForeignKey(target, ondelete=kw.pop("ondelete", "CASCADE")), **kw)


def ts(**kw):
    return mapped_column(DateTime(timezone=True), **kw)


# Severity normalisation shared by parsers, anomaly detection and forecasting.
SEVERITY_NUM = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40, "FATAL": 50}
_ALIASES = {
    "TRACE": "DEBUG", "DEBUG": "DEBUG", "DBG": "DEBUG", "VERBOSE": "DEBUG", "FINE": "DEBUG",
    "INFO": "INFO", "INFORMATION": "INFO", "NOTICE": "INFO", "LOG": "INFO", "I": "INFO",
    "WARN": "WARN", "WARNING": "WARN", "W": "WARN",
    "ERROR": "ERROR", "ERR": "ERROR", "SEVERE": "ERROR", "E": "ERROR", "FAIL": "ERROR", "FAILURE": "ERROR",
    "FATAL": "FATAL", "CRITICAL": "FATAL", "CRIT": "FATAL", "ALERT": "FATAL", "EMERG": "FATAL",
    "EMERGENCY": "FATAL", "PANIC": "FATAL", "F": "FATAL",
}


def normalize_severity(value: object) -> str:
    if value is None:
        return "INFO"
    if isinstance(value, (int, float)):
        n = int(value)
        # syslog numeric severity (0=emerg .. 7=debug) vs pino/bunyan (10..60)
        if n >= 10:
            return "DEBUG" if n < 20 else "INFO" if n < 30 else "WARN" if n < 40 else "ERROR" if n < 50 else "FATAL"
        return "FATAL" if n <= 2 else "ERROR" if n == 3 else "WARN" if n == 4 else "INFO" if n <= 6 else "DEBUG"
    return _ALIASES.get(str(value).strip().upper(), "INFO")
