"""HashiCorp Vault integration — fail-closed secret loading.

Constitution mandate: secrets come from Vault at startup; the app refuses to
boot if Vault is unreachable or a required secret is missing. This module never
logs secret *values*; on failure it raises ``ConfigurationError`` /
``ExternalServiceError`` with a non-sensitive message.
"""

from __future__ import annotations

from dataclasses import dataclass

import hvac

from keel.domain.exceptions import ConfigurationError, ExternalServiceError

# Secret keys the app requires to be present in Vault at startup.
REQUIRED_SECRET_KEYS: tuple[str, ...] = (
    "db_password",
    "minio_access_key",
    "minio_secret_key",
    "jwt_signing_key",
    "widget_token_secret",
)


@dataclass(frozen=True)
class VaultConfig:
    addr: str
    token: str
    kv_mount: str
    secret_path: str


def _client(cfg: VaultConfig) -> hvac.Client:
    return hvac.Client(url=cfg.addr, token=cfg.token)


def load_secrets(cfg: VaultConfig) -> dict[str, str]:
    """Read required secrets from Vault KV v2. Fail closed.

    Raises:
        ExternalServiceError: Vault unreachable / not authenticated / read error.
        ConfigurationError: a required secret key is missing.
    """
    try:
        client = _client(cfg)
        if not client.is_authenticated():
            raise ExternalServiceError("Vault authentication failed (check VAULT_TOKEN)")
        resp = client.secrets.kv.v2.read_secret_version(
            path=cfg.secret_path, mount_point=cfg.kv_mount, raise_on_deleted_version=True
        )
    except ExternalServiceError:
        raise
    except Exception as exc:  # noqa: BLE001 — normalize any hvac/connection error
        # Deliberately do not include the exception's full repr (may echo config).
        raise ExternalServiceError(
            f"Vault unreachable or read failed at {cfg.addr} ({type(exc).__name__})"
        ) from exc

    data = resp.get("data", {}).get("data", {})
    missing = [k for k in REQUIRED_SECRET_KEYS if not data.get(k)]
    if missing:
        raise ConfigurationError(
            f"Vault is missing required secret(s): {', '.join(sorted(missing))}"
        )
    return {k: str(v) for k, v in data.items()}
