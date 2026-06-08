"""US4 — seed produces two fully-populated, tenant-scoped catalogs.

Requires a migrated PostgreSQL via ``TEST_DATABASE_URL`` (asyncpg DSN). Skipped
otherwise. Verifies SC-004 at the database level (per-tenant counts under RLS).
MinIO catalog upload is exercised in the compose/smoke path (the S3 API port is
internal to the stack); this test focuses on the relational seed.
"""

from __future__ import annotations

import asyncio
import os

import pytest

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DSN, reason="TEST_DATABASE_URL not set; integration DB unavailable"
)


async def _counts_for_tenant(conn, tenant_id: str) -> dict[str, int]:  # type: ignore[no-untyped-def]
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
        return {
            "courses": await conn.fetchval("SELECT count(*) FROM courses"),
            "students": await conn.fetchval("SELECT count(*) FROM students"),
            "sections": await conn.fetchval("SELECT count(*) FROM sections"),
            "prerequisites": await conn.fetchval("SELECT count(*) FROM prerequisites"),
            "transcripts": await conn.fetchval("SELECT count(*) FROM student_transcript"),
            "program_requirements": await conn.fetchval(
                "SELECT count(*) FROM program_requirements"
            ),
        }


async def _run() -> None:
    import asyncpg

    os.environ["SEED_DATABASE_URL"] = TEST_DSN  # type: ignore[assignment]
    os.environ["SEED_RESET"] = "1"

    from scripts import seed

    await seed.main()

    raw_dsn = TEST_DSN.replace("+asyncpg", "")  # type: ignore[union-attr]
    conn = await asyncpg.connect(raw_dsn)
    try:
        tenant_ids = [
            str(r["id"])
            for r in await conn.fetch(
                "SELECT id FROM tenants WHERE slug = ANY($1::text[])",
                ["northane", "summit"],
            )
        ]
        assert len(tenant_ids) == 2, "expected exactly 2 seeded tenants"
        for tid in tenant_ids:
            counts = await _counts_for_tenant(conn, tid)
            assert counts["courses"] >= 20, counts
            assert counts["students"] == 2, counts
            assert counts["sections"] >= 20, counts
            assert counts["prerequisites"] > 0, counts
            assert counts["transcripts"] > 0, counts
            assert counts["program_requirements"] > 0, counts
    finally:
        await conn.close()


def test_seed_populates_two_tenants() -> None:
    # Ensure the schema exists (the migration test may have left the DB at base).
    # Done here (sync) because Alembic's env.py calls asyncio.run internally and
    # cannot be invoked from within a running event loop.
    from alembic import command
    from alembic.config import Config

    from keel.config import get_settings

    os.environ["DATABASE_URL"] = TEST_DSN  # type: ignore[assignment]
    get_settings.cache_clear()
    command.upgrade(Config("alembic.ini"), "head")

    asyncio.run(_run())
