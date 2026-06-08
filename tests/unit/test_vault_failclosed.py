"""US2 — the app fails closed when Vault is unavailable or incomplete.

Verifies FR-012/FR-013 at the unit level by mocking hvac:
- reachable + all required secrets present -> ``load_secrets`` returns them
- unreachable / not authenticated       -> ExternalServiceError
- missing a required secret              -> ConfigurationError
- error messages never contain a secret value
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from keel.domain.exceptions import ConfigurationError, ExternalServiceError
from keel.infra.vault import REQUIRED_SECRET_KEYS, VaultConfig, load_secrets

CFG = VaultConfig(
    addr="http://vault:8200",
    token="test-token",
    kv_mount="secret",
    secret_path="keel/app",
)

_ALL_SECRETS = {k: f"value-for-{k}" for k in REQUIRED_SECRET_KEYS}


def _client_returning(secret_data: dict[str, str], *, authed: bool = True) -> MagicMock:
    client = MagicMock()
    client.is_authenticated.return_value = authed
    client.secrets.kv.v2.read_secret_version.return_value = {"data": {"data": secret_data}}
    return client


def test_reachable_and_complete_returns_secrets() -> None:
    with patch("keel.infra.vault._client", return_value=_client_returning(_ALL_SECRETS)):
        result = load_secrets(CFG)
    assert set(REQUIRED_SECRET_KEYS).issubset(result.keys())


def test_unauthenticated_fails_closed() -> None:
    with patch("keel.infra.vault._client", return_value=_client_returning({}, authed=False)):
        with pytest.raises(ExternalServiceError):
            load_secrets(CFG)


def test_unreachable_fails_closed() -> None:
    def _boom(_cfg: VaultConfig) -> MagicMock:
        raise ConnectionError("connection refused")

    with patch("keel.infra.vault._client", side_effect=_boom):
        with pytest.raises(ExternalServiceError):
            load_secrets(CFG)


def test_missing_required_secret_raises_configuration_error() -> None:
    incomplete = dict(_ALL_SECRETS)
    incomplete.pop("db_password")
    with patch("keel.infra.vault._client", return_value=_client_returning(incomplete)):
        with pytest.raises(ConfigurationError) as exc:
            load_secrets(CFG)
    assert "db_password" in str(exc.value)


def test_error_message_contains_no_secret_values() -> None:
    def _boom(_cfg: VaultConfig) -> MagicMock:
        raise RuntimeError("token=super-secret-value should-not-appear")

    with patch("keel.infra.vault._client", side_effect=_boom):
        with pytest.raises(ExternalServiceError) as exc:
            load_secrets(CFG)
    # The normalized message must not echo the underlying secret-bearing text.
    assert "super-secret-value" not in str(exc.value)
