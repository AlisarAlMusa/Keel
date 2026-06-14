"""Structured JSON logging (ENGINEERING_RULES §8).

Every log line is JSON and carries: event, level, timestamp, service, and —
where bound — request_id, trace_id, tenant_id. Never use ``print`` in
application code; get a logger from here.

Output goes to stdout always. If ``log_file`` is provided to
``configure_logging``, a ``RotatingFileHandler`` is also attached so logs
survive container restarts when the path is volume-mounted.

Secret redaction is the responsibility of the guardrails/redaction layer
(later phase) before text reaches a log call; this module never logs secrets
because callers never pass them.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from typing import Any

import structlog


def configure_logging(
    *,
    service: str,
    level: str = "INFO",
    log_file: str | None = None,
) -> None:
    """Configure structlog + stdlib logging to emit JSON.

    Always writes to stdout. If *log_file* is a non-empty path, also writes to
    a rotating file (10 MB per file, 5 backups) so logs persist across restarts
    when the path is volume-mounted (ENGINEERING_RULES §8).
    """
    log_level = getattr(logging, level.upper(), logging.INFO)
    fmt = logging.Formatter("%(message)s")

    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()  # idempotent — avoid duplicate output on re-configure

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    structlog.contextvars.bind_contextvars(service=service)


def bind_request_context(
    *,
    request_id: str | None = None,
    trace_id: str | None = None,
    tenant_id: str | None = None,
) -> None:
    """Bind per-request correlation fields onto the current context."""
    fields: dict[str, Any] = {}
    if request_id is not None:
        fields["request_id"] = request_id
    if trace_id is not None:
        fields["trace_id"] = trace_id
    if tenant_id is not None:
        fields["tenant_id"] = tenant_id
    if fields:
        structlog.contextvars.bind_contextvars(**fields)


def get_logger(name: str | None = None) -> Any:
    """Return a bound structlog logger."""
    return structlog.get_logger(name)
