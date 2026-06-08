"""FastAPI dependency providers (ENGINEERING_RULES §4, §5).

Singletons (DB engine/session factory, Redis, S3 client, settings) are built
once in the lifespan and stored on ``app.state``; these providers expose them
via ``Depends``. Nothing here constructs a new client per request.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from keel.config import Settings, get_settings


def get_app_settings() -> Settings:
    return get_settings()


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.session_factory  # type: ignore[no-any-return]


async def get_session(
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> AsyncIterator[AsyncSession]:
    """Yield a session. Tenant context is bound by callers via ``set_tenant``."""
    async with session_factory() as session:
        yield session


def get_redis(request: Request) -> Any:
    return request.app.state.redis


def get_storage(request: Request) -> Any:
    return request.app.state.storage


__all__ = [
    "get_app_settings",
    "get_session",
    "get_session_factory",
    "get_redis",
    "get_storage",
    "Settings",
]
