"""Model-server configuration (pydantic-settings).

Lean and isolated from the backend. Phase 0 needs almost nothing; the model
artifact directory + SHA pinning arrive with model loading in later phases.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    model_server_port: int = 9000
    log_level: str = "INFO"
    grad_risk_artifacts_dir: str = "/app/ml/grad_risk/artifacts"
    intent_artifacts_dir: str = "/app/ml/intent/artifacts"

    # SHA-256 pins — server refuses to boot if the loaded file doesn't match.
    # Update here whenever a new artifact is promoted to production.
    intent_model_sha256: str = "8708f944149c65955aca4c3da854c56eb571a17a2b12baf0603228029e645f62"
    grad_risk_model_sha256: str = "fda480982c284cd9928af32af970262459554a71b91175a104925d64cb137687"


@lru_cache
def get_settings() -> Settings:
    return Settings()
