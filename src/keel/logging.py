"""Structured JSON logging (ENGINEERING_RULES §14).

Every log line is JSON and carries: event, level, timestamp, service, and —
where bound — request_id, trace_id, tenant_id. Never use ``print`` in
application code; get a logger from here.

Secret redaction is the responsibility of the guardrails/redaction layer
(later phase) before text reaches a log call; this module never logs secrets
because callers never pass them.
"""

from __future__ import annotations

import logging
from typing import Any

import structlog


def configure_logging(*, service: str, level: str = "INFO") -> None:
    """Configure structlog + stdlib logging to emit JSON to stdout."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(format="%(message)s", level=log_level)

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
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Bind the service name onto every log line for this process.
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
