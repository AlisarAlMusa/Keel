"""Database access infrastructure.

Async SQLAlchemy engine + session factory. The app connects as the
non-superuser role ``keel_app`` so PostgreSQL Row-Level Security is enforced
(superusers bypass RLS). Every unit of work sets the tenant context via
``set_tenant`` so RLS policies (``current_setting('app.tenant_id')``) scope rows.

Created once at startup (lifespan singleton), injected via ``api.deps``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """Create the async engine. ``database_url`` uses the asyncpg driver."""
    return create_async_engine(database_url, echo=echo, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def set_tenant(session: AsyncSession, tenant_id: UUID) -> None:
    """Bind the tenant for this transaction so RLS policies apply.

    Uses ``SET LOCAL`` so the setting is scoped to the current transaction and
    automatically cleared on commit/rollback. Must be called inside a
    transaction (the session's begin block).
    """
    # SET LOCAL does not accept bind parameters; set_config(..., is_local=true)
    # is the parameterized, transaction-scoped equivalent.
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_id)}
    )


@asynccontextmanager
async def tenant_session(
    session_factory: async_sessionmaker[AsyncSession], tenant_id: UUID
) -> AsyncIterator[AsyncSession]:
    """Open a session, begin a transaction, and bind the tenant context."""
    async with session_factory() as session:
        async with session.begin():
            await set_tenant(session, tenant_id)
            yield session
