"""Redis client factory.

Async client used for short-term session memory / cache and as the RQ broker.
Created once at startup (lifespan singleton), injected via ``api.deps``.
"""

from __future__ import annotations

import redis.asyncio as aioredis


def create_redis(redis_url: str) -> aioredis.Redis:
    """Create an async Redis client from a connection URL."""
    return aioredis.from_url(redis_url, encoding="utf-8", decode_responses=True)


async def ping(client: aioredis.Redis) -> bool:
    """Return True if Redis responds to PING (used by readiness checks)."""
    try:
        return bool(await client.ping())
    except Exception:  # noqa: BLE001
        return False
