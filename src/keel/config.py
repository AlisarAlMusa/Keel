"""Application configuration.

The ONLY place in the backend that reads environment variables. Everything else
receives a typed ``Settings`` via dependency injection. Secret *values* are not
read here — they come from Vault at startup (see ``infra.vault``); this object
holds non-secret config plus the coordinates needed to reach Vault.

Rules (ENGINEERING_RULES §6):
- no ``os.getenv`` outside this module
- all required values are typed
- ``extra="forbid"`` so an unknown/typo'd env key fails fast
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed, validated application configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # "ignore" rather than "forbid": .env is shared between pydantic-settings
        # (app config) and docker-compose (POSTGRES_USER, MINIO_ROOT_*, etc.).
        # Separating the two files is future work; forbid would reject all infra vars.
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---
    keel_env: str = "local"
    keel_log_level: str = "INFO"
    keel_log_file: str = ""  # empty = stdout only; set to volume path in prod
    keel_api_port: int = 8000

    # --- CORS (Engineering Rules §15) ---
    # Empty list = CORS disabled (safe default for API-only / same-origin dev).
    # In production, set to the widget origin(s): '["https://widget.uni.edu"]'
    cors_allowed_origins: list[str] = []

    # --- Datastores (non-secret; password merged from Vault at runtime) ---
    database_url: str = "postgresql+asyncpg://keel_app:placeholder@db:5432/keel"
    redis_url: str = "redis://redis:6379/0"
    minio_endpoint: str = "http://minio:9000"
    minio_bucket: str = "keel-artifacts"

    # --- MLflow ---
    mlflow_tracking_uri: str = "http://mlflow:5000"

    # --- Tracing ---
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "keel-api"
    langsmith_tracing: bool = False

    # --- Model server ---
    model_server_url: str = "http://model-server:9000"

    # --- Vault coordinates (how to reach Vault; NOT secret values) ---
    vault_addr: str = "http://vault:8200"
    vault_token: str = "keel-dev-root-token"
    vault_kv_mount: str = "secret"
    vault_secret_path: str = "keel/app"

    # --- LLM models (names are not secret; keys come from Vault) ---
    # Fallback chain: high-quota primary → reserve.
    #   gemini-1.5-flash   → 1500 RPD free tier (primary — plenty for demo)
    #   gemini-1.5-flash-8b → 4000 RPD free tier (fallback — virtually unlimited)
    # gemini-2.5-flash has only 20 RPD on free tier — exhausted in minutes of testing.
    gemini_model: str = "gemini-1.5-flash"
    gemini_fallback_models: list[str] = ["gemini-1.5-flash-8b", "gemini-1.5-pro"]
    gemini_lite_model: str = "gemini-1.5-flash-8b"

    # --- RAG / embedding knobs (all tuneable without code change) ---
    embed_model: str = "embed-multilingual-v3.0"
    embed_dim: int = 1024
    rerank_model: str = "rerank-multilingual-v3.0"
    dense_k: int = 20
    sparse_k: int = 20
    rrf_k: int = 60
    rerank_top_n: int = 5

    # --- Session / cache ---
    session_ttl_seconds: int = 1800  # 30-min sliding TTL for Redis chat memory

    # --- Platform operator (Phase 5 addendum) ---
    # Demo password for the platform operator account (Vault-overridable).
    keel_operator_password: str = "keel-operator-demo"
    # Demo password for tenant_admin accounts (Vault-overridable).
    keel_admin_password: str = "keel-admin-demo"
    # Demo password for portal users (students + registrar; Vault-overridable).
    keel_portal_password: str = "keel-portal-demo"

    # --- Portal tenant binding ---
    # Set per portal service instance to bind login + SIS reads to one tenant.
    # In compose: PORTAL_TENANT=<slug> for each portal-northane / portal-summit.
    portal_tenant: str = "northane"

    @property
    def service_name(self) -> str:
        return self.otel_service_name


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
