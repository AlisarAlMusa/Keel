"""Alembic environment — async, targets the ORM metadata.

The database URL comes from ``Settings`` (env), never hardcoded. Migrations run
with the asyncpg driver. Autogenerate targets ``keel.infra.database.models.Base.metadata``;
RLS/extension statements are hand-authored in the migration (autogenerate cannot
emit them).
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

from keel.config import get_settings
from keel.infra.database.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_database_url() -> str:
    """Resolve the DSN.

    If the configured DSN still carries the literal ``placeholder`` password
    (the compose/runtime case), inject the real DB password from Vault — the
    same fail-closed source the app uses. If the DSN already contains a real
    password (host/CI direct case), use it as-is so migrations can run without
    Vault.
    """
    settings = get_settings()
    dsn = settings.database_url
    if ":placeholder@" not in dsn:
        return dsn

    from keel.infra.vault import VaultConfig, load_secrets

    secrets = load_secrets(
        VaultConfig(
            addr=settings.vault_addr,
            token=settings.vault_token,
            kv_mount=settings.vault_kv_mount,
            secret_path=settings.vault_secret_path,
        )
    )
    return dsn.replace(":placeholder@", f":{secrets['db_password']}@", 1)


# Inject the runtime DSN so the URL is not committed to alembic.ini.
config.set_main_option("sqlalchemy.url", _resolve_database_url())


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: object) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)  # type: ignore[arg-type]
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode with an async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
