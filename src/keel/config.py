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
    keel_api_port: int = 8000

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

    @property
    def service_name(self) -> str:
        return self.otel_service_name


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
