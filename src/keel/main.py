"""FastAPI application + lifespan wiring.

Startup sequence (order matters):
1. Configure structured logging and tracing.
2. **Vault gate (fail-closed):** load required secrets from Vault. If Vault is
   unreachable or a secret is missing, raise — the app does NOT start.
3. Merge Vault secrets into runtime config (DB password, MinIO keys).
4. Build singletons (DB engine/session factory, Redis, S3) and store on
   app.state.
5. Mount routers and static files.

Secret values are never logged.

Phase 5 additions:
- app.state.widget_token_secret  — for JWT widget token signing
- app.state.jwt_signing_key      — for portal session cookie signing
- app.state.widget_origins_map   — in-memory cache of per-tenant origins
- app.state.widget_persona_map   — in-memory cache of per-tenant persona names
- app.state.tenant_names         — list of (tenant_id, slug, name) for guardrail
- Portal router mounted at /portal
- Static files: /widget.js loader, /widget/ app, /portal/ app, /admin/ app
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import cohere as cohere_lib
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from keel import __version__
from keel.agent.graph import build_agent, run_agent
from keel.agent.tools import AgentDeps
from keel.api.routers import actions as actions_router
from keel.api.routers import admin as admin_router
from keel.api.routers import auth_admin as auth_admin_router
from keel.api.routers import chat as chat_router
from keel.api.routers import health
from keel.api.routers import internal as internal_router
from keel.api.routers import platform as platform_router
from keel.config import Settings, get_settings
from keel.domain.models import Term
from keel.infra import redis as redis_infra
from keel.infra import storage as storage_infra
from keel.infra import tracing
from keel.infra.database import engine as db_infra
from keel.infra.database.models import WidgetConfig
from keel.infra.llm import get_llm
from keel.infra.model_client import ModelClient
from keel.infra.vault import VaultConfig, load_secrets
from keel.logging import configure_logging, get_logger
from keel.services.router import _load_fallback_threshold

_log = get_logger(__name__)

# Resolves to: /app/frontend/ in Docker (main.py = /app/src/keel/main.py → 3 parents = /app/)
# Also works locally: repo-root/frontend/
_FRONTEND_ROOT = Path(__file__).parent.parent.parent / "frontend"


def _dsn_with_password(database_url: str, password: str) -> str:
    return database_url.replace(":placeholder@", f":{password}@", 1)


async def _load_widget_config(
    session_factory: object, app_state: object
) -> None:
    """Populate in-memory caches from widget_config and tenants tables.

    Uses SECURITY DEFINER functions so keel_app (NOBYPASSRLS) can read all
    tenants' rows at startup without SET LOCAL row_security = OFF.
    """
    sf: async_sessionmaker[AsyncSession] = session_factory  # type: ignore[assignment]
    try:
        async with sf() as session:
            # widget_config_all: persona_name + allowed_origins per tenant
            cfg_rows = await session.execute(
                text("SELECT tenant_id, persona_name, persona, allowed_origins FROM widget_config_all()")
            )
            origins_map: dict[str, list[str]] = {}
            persona_map: dict[str, str] = {}
            persona_prompt_map: dict[str, str] = {}
            for tid, pname, persona_prompt, allowed_origins in cfg_rows:
                origins_map[str(tid)] = list(allowed_origins or [])
                persona_map[str(tid)] = pname or "Keel"
                if persona_prompt:
                    persona_prompt_map[str(tid)] = persona_prompt
            app_state.widget_origins_map = origins_map  # type: ignore[attr-defined]
            app_state.widget_persona_map = persona_map  # type: ignore[attr-defined]
            app_state.widget_persona_prompt_map = persona_prompt_map  # type: ignore[attr-defined]

            # tenant_names_all: (id, slug, name) — used by cross-tenant guardrail
            name_rows = await session.execute(
                text("SELECT id, slug, name FROM tenant_names_all()")
            )
            # list of (tenant_id, slug, display_name) tuples
            app_state.tenant_names = [  # type: ignore[attr-defined]
                (str(tid), slug or "", tname or "")
                for tid, slug, tname in name_rows
            ]
            _log.info(
                "widget_config_cache_loaded",
                tenants=len(origins_map),
            )
    except Exception as exc:  # noqa: BLE001
        _log.warning("widget_config_cache_failed", error=type(exc).__name__)
        app_state.widget_origins_map = {}  # type: ignore[attr-defined]
        app_state.widget_persona_map = {}  # type: ignore[attr-defined]
        app_state.tenant_names = []  # type: ignore[attr-defined]


# Keep old name as shim so existing call sites compile
_load_widget_origins = _load_widget_config


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

    # 2. Vault gate — FAIL CLOSED.
    vault_cfg = VaultConfig(
        addr=settings.vault_addr,
        token=settings.vault_token,
        kv_mount=settings.vault_kv_mount,
        secret_path=settings.vault_secret_path,
    )
    secrets = load_secrets(vault_cfg)
    _log.info("vault_secrets_loaded", keys=sorted(secrets.keys()))

    # 3. Merge secrets
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
    except Exception as exc:  # noqa: BLE001
        _log.warning("bucket_ensure_failed", error=type(exc).__name__)

    # Phase 5: store auth secrets on app.state so auth deps can reach them
    app.state.widget_token_secret = secrets["widget_token_secret"]
    app.state.jwt_signing_key = secrets["jwt_signing_key"]
    app.state.portal_service_secret = secrets["portal_service_secret"]

    # 5. Phase-2+ singletons
    gemini_key: str = secrets["gemini_api_key"]
    cohere_key: str = secrets["cohere_api_key"]

    app.state.llm_agent = get_llm("agent", api_key=gemini_key)
    app.state.llm_lite = get_llm("lite", api_key=gemini_key)
    app.state.cohere_client = cohere_lib.AsyncClientV2(api_key=cohere_key)
    app.state.model_client = ModelClient(settings.model_server_url)
    app.state.fallback_threshold = _load_fallback_threshold()

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    pg_dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    async with AsyncPostgresSaver.from_conn_string(pg_dsn) as checkpointer:
        await checkpointer.setup()

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
        app.state.compiled_agent = compiled_agent

        async def _agent_run(envelope):  # type: ignore[no-untyped-def]
            return await run_agent(
                envelope=envelope,
                compiled_graph=compiled_agent,
                redis=app.state.redis,
                session_ttl=settings.session_ttl_seconds,
            )

        app.state.agent_run = _agent_run

        # Phase 5: warm widget config cache (origins + persona + tenant names)
        await _load_widget_config(app.state.session_factory, app.state)

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

    # CORS — allow all frontend origins. Origin enforcement is done server-side
    # via verify_origin_or_403; CORS is defense-in-depth.
    cors_origins = settings.cors_allowed_origins or ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization", "Content-Type", "X-Tenant-Id", "X-Admin-Token",
            "X-Idempotency-Key",
        ],
    )

    # API routers
    app.include_router(health.router)
    app.include_router(chat_router.router)
    app.include_router(admin_router.router)
    app.include_router(actions_router.router)
    app.include_router(internal_router.router)
    # Phase 5 addendum: email+password login + platform operator
    app.include_router(auth_admin_router.router)
    app.include_router(platform_router.router)

    # widget.js loader (inline; fronts the widget iframe)
    @app.get("/widget.js", include_in_schema=False)
    async def widget_js() -> FileResponse:
        js_path = _FRONTEND_ROOT / "widget.js"
        if js_path.exists():
            return FileResponse(str(js_path), media_type="application/javascript")
        # Fallback inline stub so the route always responds
        from fastapi.responses import Response as _Resp
        stub = _WIDGET_JS_STUB
        return _Resp(content=stub, media_type="application/javascript")

    # Brand assets (keel-icon.png, keel-logo.png) — referenced by widget iframe and admin console
    _mount_static_if_exists(app, "/static", _FRONTEND_ROOT / "static", "brand-static")
    # Static frontends (served after build; ignored if dirs don't exist yet)
    _mount_static_if_exists(app, "/widget", _FRONTEND_ROOT / "widget" / "dist", "widget")
    # Unified Keel console (tenant_admin + platform_operator, role-based routing)
    _mount_static_if_exists(app, "/keel", _FRONTEND_ROOT / "admin" / "dist", "keel-console")

    tracing.instrument_fastapi(app)
    return app


def _mount_static_if_exists(app: FastAPI, path: str, directory: Path, name: str) -> None:
    if directory.exists():
        app.mount(path, StaticFiles(directory=str(directory), html=True), name=name)
        _log.info("static_mounted", path=path, directory=str(directory))
    else:
        _log.info("static_skipped_not_built", path=path)


# ---------------------------------------------------------------------------
# Inline widget.js fallback stub
# ---------------------------------------------------------------------------

_WIDGET_JS_STUB = r"""
/**
 * Keel widget loader (D-P5-003, D-P5-006).
 *
 * This script is served from keel-api but runs on the HOST PAGE (the SIS portal).
 * Key security properties:
 *  1. Token is fetched from the portal's own origin (window.location.origin) so
 *     the session cookie is included automatically — no cross-origin cookie issues.
 *  2. The widget iframe is on the keel-api origin so /chat calls are same-origin.
 *  3. Token is postMessaged to the iframe with an explicit origin check — the host
 *     page cannot read the token once it is inside the iframe.
 *  4. Token never touches localStorage, sessionStorage, or a URL param.
 */
(function() {
  var script = document.currentScript;
  var widgetId = script && script.getAttribute('data-widget-id');
  if (!widgetId) return;

  // keel-api base URL (where this script is served from)
  var KEEL_BASE = (script && script.src.replace('/widget.js', '')) || '';
  // Portal base URL (where the session lives — the HOST page's origin)
  var PORTAL_ORIGIN = window.location.origin;

  // ── Launcher button ──────────────────────────────────────────────────────
  var btn = document.createElement('button');
  btn.id = 'keel-launcher';
  btn.setAttribute('aria-label', 'Open Keel academic advisor');
  btn.innerHTML = [
    '<img src="', KEEL_BASE, '/static/keel-icon.png"',
    ' alt="" style="width:32px;height:32px;"',
    ' onerror="this.style.display=\'none\'"/>',
  ].join('');
  btn.style.cssText = [
    'position:fixed','bottom:24px','right:24px','z-index:9999',
    'width:56px','height:56px','border-radius:50%','border:2px solid #495B7D',
    'background:#02122F','cursor:pointer','display:flex',
    'align-items:center','justify-content:center',
    'box-shadow:0 4px 20px rgba(2,18,47,0.45)',
    'transition:transform 0.15s ease',
  ].join(';');
  btn.addEventListener('mouseenter', function() { btn.style.transform = 'scale(1.08)'; });
  btn.addEventListener('mouseleave', function() { btn.style.transform = 'scale(1)'; });

  // ── Widget panel (iframe) ─────────────────────────────────────────────────
  var panel = null;
  var isOpen = false;
  var tokenCache = null;

  function openPanel() {
    if (!panel) {
      panel = document.createElement('iframe');
      panel.src = KEEL_BASE + '/widget/';
      panel.style.cssText = [
        'position:fixed','bottom:96px','right:24px','z-index:9998',
        'width:390px','height:620px','border:none',
        'border-radius:14px',
        'box-shadow:0 12px 40px rgba(2,18,47,0.45)',
        'background:#02122F',
        'opacity:0','transition:opacity 0.15s ease',
        'pointer-events:none',
      ].join(';');
      panel.allow = 'clipboard-write';
      document.body.appendChild(panel);

      // Once the iframe loads, send the token if we already have one
      panel.addEventListener('load', function() {
        if (tokenCache) sendToken(tokenCache);
      });
    }

    // Mint / reuse token then show panel
    getToken(function(token) {
      tokenCache = token;
      panel.style.opacity = '1';
      panel.style.pointerEvents = 'auto';
      isOpen = true;
      sendToken(token);
    });
  }

  function closePanel() {
    if (!panel) return;
    panel.style.opacity = '0';
    panel.style.pointerEvents = 'none';
    isOpen = false;
  }

  function sendToken(token) {
    if (!panel || !panel.contentWindow) return;
    // Origin check: only send to the keel-api iframe
    panel.contentWindow.postMessage(
      { type: 'KEEL_TOKEN', token: token, widgetId: widgetId },
      KEEL_BASE
    );
  }

  function getToken(cb) {
    // Call the PORTAL's keel-token endpoint (same-origin → session cookie sent)
    fetch(PORTAL_ORIGIN + '/api/portal/keel-token', { credentials: 'same-origin' })
      .then(function(r) {
        if (!r.ok) throw new Error('token mint failed: ' + r.status);
        return r.json();
      })
      .then(function(data) { cb(data.token); })
      .catch(function(err) {
        console.error('[Keel] token fetch error:', err);
      });
  }

  btn.addEventListener('click', function() {
    if (isOpen) { closePanel(); } else { openPanel(); }
  });

  document.body.appendChild(btn);
})();
"""

app = create_app()
