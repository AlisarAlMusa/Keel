"""Session utilities — tenant binding and context managers.

Every unit of work sets the tenant context via ``set_tenant`` so RLS policies
(``current_setting('app.tenant_id')``) scope rows to the correct tenant.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


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
