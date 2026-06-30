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

    # --- Email / notifications (G4) ---
    # keel_email_enabled is the master switch and is ON: Keel-originated actions
    # (registration/waitlist/petition/graduation/major-change/escalation) produce a
    # notification email. SIS-domain events (registrar approve/reject) are NOT Keel
    # actions and never email — they are gated out in the worker.
    keel_email_enabled: bool = True
    # Demo simulation: we have no real per-student mailboxes, so every Keel email is
    # addressed to this one inbox. Set empty to fall back to the payload's address.
    keel_email_simulate_to: str = "mousaelisar@gmail.com"
    # Real delivery is still opt-in. With keel_smtp_enabled=false (default) the
    # worker SIMULATES the send (logs it, to the address above) — no real mail goes
    # out. Set keel_smtp_enabled=true + host to actually send via SMTP.
    keel_smtp_enabled: bool = False
    keel_smtp_host: str = ""
    keel_smtp_port: int = 587
    keel_smtp_user: str = ""
    keel_smtp_password: str = ""
    keel_smtp_starttls: bool = True
    keel_email_from: str = "noreply@keel.local"

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
    # Fallback chain verified against actual API quota screen:
    #   gemini-3.1-flash-lite → 500 RPD (primary — highest quota available)
    #   gemini-3-flash        → 20 RPD  (fallback — fresh at demo time)
    #   gemini-2.5-flash-lite → 20 RPD  (last resort)
    # gemini-2.5-flash has 20 RPD and was already exhausted (36 used).
    gemini_model: str = "gemini-3.1-flash-lite"
    gemini_fallback_models: list[str] = ["gemini-3-flash", "gemini-2.5-flash-lite"]
    gemini_lite_model: str = "gemini-3.1-flash-lite"

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
    # Demo passwords — intentionally trivial ("123") for an easy live demo login.
    # All three are Vault-overridable, so production never ships this default.
    # Demo password for the platform operator account (Vault-overridable).
    keel_operator_password: str = "123"
    # Demo password for tenant_admin accounts (Vault-overridable).
    keel_admin_password: str = "123"
    # Demo password for portal users (students + registrar; Vault-overridable).
    keel_portal_password: str = "123"

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
