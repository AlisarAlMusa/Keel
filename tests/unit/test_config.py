"""US2 — configuration is typed and validated (FR-013).

- a valid environment loads
- unknown/typo'd keys are silently ignored (``extra="ignore"`` — .env is shared
  with docker-compose infra vars; forbid would reject POSTGRES_USER etc.)
- types are coerced/validated
"""

from __future__ import annotations

import pytest

from keel.config import Settings

_VALID_ENV = {
    "KEEL_ENV": "ci",
    "KEEL_API_PORT": "8001",
    "DATABASE_URL": "postgresql+asyncpg://keel_app:placeholder@db:5432/keel",
    "VAULT_ADDR": "http://vault:8200",
}


def test_valid_env_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _VALID_ENV.items():
        monkeypatch.setenv(key, value)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.keel_env == "ci"
    assert settings.keel_api_port == 8001
    assert isinstance(settings.keel_api_port, int)


def test_unknown_key_is_ignored() -> None:
    # extra="ignore": unknown kwargs are silently dropped, not rejected.
    # .env is shared with docker-compose infra vars (POSTGRES_USER, MINIO_ROOT_*,
    # etc.) that are not Settings fields; forbid would reject them all.
    settings = Settings(totally_unknown_key="x", _env_file=None)  # type: ignore[call-arg]
    assert settings.keel_env == "local"


def test_defaults_are_present_without_env() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.vault_kv_mount == "secret"
    assert settings.vault_secret_path == "keel/app"
    assert settings.langsmith_tracing is False
