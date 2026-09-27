from __future__ import annotations

import logging

from shared.config import settings


def setup_logging(service: str) -> logging.Logger:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format=f"%(asctime)s %(levelname)s [{service}] %(name)s: %(message)s",
    )
    return logging.getLogger(f"logpilot.{service}")
