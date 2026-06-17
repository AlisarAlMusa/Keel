"""FastAPI application + lifespan wiring.

Startup sequence (order matters):
1. Configure structured logging and tracing.
2. **Vault gate (fail-closed):** load required secrets from Vault. If Vault is
   unreachable or a secret is missing, raise — the app does NOT start.
3. Merge Vault secrets into runtime config (DB password, MinIO keys).
4. Build singletons (DB engine/session factory, Redis, S3) and store on
   app.state.
5. Mount routers.

Secret values are never logged.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import cohere as cohere_lib
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from keel import __version__
from keel.agent.graph import build_agent, run_agent
from keel.agent.tools import AgentDeps
from keel.api.routers import actions as actions_router
from keel.api.routers import admin as admin_router
from keel.api.routers import chat as chat_router
from keel.api.routers import health
from keel.config import Settings, get_settings
from keel.domain.models import Term
from keel.infra import redis as redis_infra
from keel.infra import storage as storage_infra
from keel.infra import tracing
from keel.infra.database import engine as db_infra
from keel.infra.llm import get_llm
from keel.infra.model_client import ModelClient
from keel.infra.vault import VaultConfig, load_secrets
from keel.logging import configure_logging, get_logger
from keel.services.router import _load_fallback_threshold

_log = get_logger(__name__)


def _dsn_with_password(database_url: str, password: str) -> str:
    """Inject the Vault-sourced DB password into the DSN placeholder.

    The env DSN uses a literal ``placeholder`` password locally; the real
    password comes from Vault. Done with a targeted replace to avoid logging or
    string-building the secret elsewhere.
    """
    return database_url.replace(":placeholder@", f":{password}@", 1)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = get_settings()

    # 1. Observability
    configure_logging(
        service=settings.otel_service_name,
        level=settings.keel_log_level,
        log_file=settings.keel_log_file or None,
    )
    tracing.configure_tracing(
        service_name=settings.otel_service_name,
        otlp_endpoint=settings.otel_exporter_otlp_endpoint or None,
    )
    _log.info("startup_begin", env=settings.keel_env, version=__version__)

    # 2. Vault gate — FAIL CLOSED. Any failure here aborts startup.
    vault_cfg = VaultConfig(
        addr=settings.vault_addr,
        token=settings.vault_token,
        kv_mount=settings.vault_kv_mount,
        secret_path=settings.vault_secret_path,
    )
    secrets = load_secrets(vault_cfg)  # raises -> app does not boot
    _log.info("vault_secrets_loaded", keys=sorted(secrets.keys()))

    # 3. Merge secrets into runtime config (no secret values logged)
    dsn = _dsn_with_password(settings.database_url, secrets["db_password"])

    # 4. Singletons
    engine = db_infra.create_engine(dsn)
    app.state.engine = engine
    app.state.session_factory = db_infra.create_session_factory(engine)
    app.state.redis = redis_infra.create_redis(settings.redis_url)
    app.state.storage = storage_infra.create_s3_client(
        endpoint=settings.minio_endpoint,
        access_key=secrets["minio_access_key"],
        secret_key=secrets["minio_secret_key"],
    )
    try:
        storage_infra.ensure_bucket(app.state.storage, settings.minio_bucket)
    except Exception as exc:  # noqa: BLE001 — bucket ensure is best-effort at boot
        _log.warning("bucket_ensure_failed", error=type(exc).__name__)

    # 5. Phase-2 singletons: LLM clients, Cohere, model-server, agent
    gemini_key: str = secrets["gemini_api_key"]
    cohere_key: str = secrets["cohere_api_key"]

    app.state.llm_agent = get_llm("agent", api_key=gemini_key)
    app.state.llm_lite = get_llm("lite", api_key=gemini_key)
    app.state.cohere_client = cohere_lib.AsyncClientV2(api_key=cohere_key)
    app.state.model_client = ModelClient(settings.model_server_url)

    # Router threshold loaded from artifact (not settings — live-editable)
    app.state.fallback_threshold = _load_fallback_threshold()

    # AsyncPostgresSaver for durable LangGraph checkpointing.
    # psycopg3 requires "postgresql://" — strip the "+asyncpg" SQLAlchemy prefix.
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    pg_dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    async with AsyncPostgresSaver.from_conn_string(pg_dsn) as checkpointer:
        await checkpointer.setup()

        # Tools open their own sessions per-call via session_factory.
        deps = AgentDeps(
            session_factory=app.state.session_factory,
            cohere_client=app.state.cohere_client,
            llm_agent=app.state.llm_agent,
            settings=settings,
            current_term=Term.FALL,
            current_year=2025,
            model_client=app.state.model_client,
        )
        compiled_agent = build_agent(app.state.llm_agent, deps, checkpointer)
        # Store for the actions router (approve/reject resume).
        app.state.compiled_agent = compiled_agent

        async def _agent_run(envelope):  # type: ignore[no-untyped-def]
            return await run_agent(
                envelope=envelope,
                compiled_graph=compiled_agent,
                redis=app.state.redis,
                session_ttl=settings.session_ttl_seconds,
            )

        app.state.agent_run = _agent_run

        _log.info("startup_complete")
        try:
            yield
        finally:
            await engine.dispose()
            await app.state.redis.aclose()
            _log.info("shutdown_complete")


def create_app() -> FastAPI:
    app = FastAPI(title="Keel API", version=__version__, lifespan=lifespan)

    settings = get_settings()
    if settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Tenant-Id", "X-Idempotency-Key"],
        )

    app.include_router(health.router)
    app.include_router(chat_router.router)
    app.include_router(admin_router.router)
    app.include_router(actions_router.router)
    tracing.instrument_fastapi(app)
    return app


app = create_app()
