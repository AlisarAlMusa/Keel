"""OpenTelemetry + LangSmith tracing initialization (ENGINEERING_RULES §16).

Initialized once in the FastAPI lifespan. If no OTLP endpoint is configured,
tracing degrades gracefully (spans still created, no exporter) so a missing
collector never blocks startup. LangSmith env is wired for later LLM tracing.
"""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from keel.logging import get_logger

_log = get_logger(__name__)


def configure_tracing(*, service_name: str, otlp_endpoint: str | None) -> TracerProvider:
    """Set up the global tracer provider.

    Adds an OTLP exporter only if ``otlp_endpoint`` is non-empty; otherwise the
    provider runs without an exporter (graceful degradation).
    """
    resource = Resource.create({SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)

    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint))
            )
            _log.info("tracing_configured", exporter="otlp", endpoint=otlp_endpoint)
        except Exception as exc:  # noqa: BLE001 — never let tracing block boot
            _log.warning("tracing_exporter_failed", error=type(exc).__name__)
    else:
        _log.info("tracing_configured", exporter="none")

    trace.set_tracer_provider(provider)
    return provider


def instrument_fastapi(app: object) -> None:
    """Attach FastAPI auto-instrumentation. Best-effort; never blocks boot."""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        _log.warning("fastapi_instrumentation_failed", error=type(exc).__name__)


def instrument_libraries(*, engine: object | None = None) -> None:
    """Auto-instrument the I/O libraries so DB, cache, and outbound HTTP calls
    appear as child spans under each request/agent turn.

    * SQLAlchemy  → every engine query (the deterministic engine's reads, repos).
    * Redis       → session memory + cache hits/misses.
    * httpx       → outbound calls: the LLM (Gemini), reranker (Cohere), and the
                    model-server. This is what makes the "LLM" step visible in the
                    trace with timing, without an LLM-specific SDK.

    Each instrumentor is best-effort and idempotent — a missing optional package
    or a double-call must never block boot. Called from both the API lifespan and
    the worker entrypoint (the worker does DB/Redis/email I/O too).
    """
    if engine is not None:
        try:
            from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

            # Async engines expose the real DBAPI engine as ``.sync_engine``.
            sync_engine = getattr(engine, "sync_engine", engine)
            SQLAlchemyInstrumentor().instrument(engine=sync_engine)
        except Exception as exc:  # noqa: BLE001
            _log.warning("sqlalchemy_instrumentation_failed", error=type(exc).__name__)

    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor

        RedisInstrumentor().instrument()
    except Exception as exc:  # noqa: BLE001
        _log.warning("redis_instrumentation_failed", error=type(exc).__name__)

    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    except Exception as exc:  # noqa: BLE001
        _log.warning("httpx_instrumentation_failed", error=type(exc).__name__)
