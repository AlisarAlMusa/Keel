"""Model-server configuration (pydantic-settings).

Lean and isolated from the backend. Phase 0 needs almost nothing; the model
artifact directory + SHA pinning arrive with model loading in later phases.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid", case_sensitive=False)

    model_server_port: int = 9000
    log_level: str = "INFO"
    grad_risk_artifacts_dir: str = "/app/ml/grad_risk/artifacts"
    intent_artifacts_dir: str = "/app/ml/intent/artifacts"


@lru_cache
def get_settings() -> Settings:
    return Settings()
