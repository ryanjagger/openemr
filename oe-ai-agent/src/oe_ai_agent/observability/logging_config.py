"""structlog configuration — JSON logs to stdout, suitable for Railway.

Called once from ``main.py`` startup. Idempotent so tests can re-invoke.
Stdlib loggers are routed through structlog's processor chain via
``structlog.stdlib`` so calls like ``logging.getLogger(__name__).warning(...)``
also emit JSON.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Final

import structlog

_DEFAULT_LEVEL: Final = "INFO"


def configure_logging(level: str | None = None) -> None:
    """Configure structlog + stdlib logging for JSON-to-stdout.

    Level resolution order: arg → ``LOG_LEVEL`` env → INFO.
    """
    resolved_level = (level or os.environ.get("LOG_LEVEL") or _DEFAULT_LEVEL).upper()
    numeric_level = getattr(logging, resolved_level, logging.INFO)

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            timestamper,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging through the same JSON pipeline so any third-party
    # library that uses logging (httpx, uvicorn, litellm) emits JSON too.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=structlog.processors.JSONRenderer(),
            foreign_pre_chain=[
                structlog.contextvars.merge_contextvars,
                structlog.stdlib.add_log_level,
                timestamper,
            ],
        )
    )
    root = logging.getLogger()
    # Replace any pre-existing handlers so we don't double-emit.
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(numeric_level)
