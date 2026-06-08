"""US3 — baseline migration creates 16 tables + RLS on the 15 tenant-owned ones.

Requires a real PostgreSQL (with pgvector + pgcrypto available). Set
``TEST_DATABASE_URL`` to an asyncpg DSN, e.g.::

    TEST_DATABASE_URL=postgresql+asyncpg://keel_app:keel_local_pw@localhost:5432/keel

If unset, the test is skipped so the lint/type/unit CI job stays green.
Verifies SC-003 (16 tables, 15 policies, reversible).
"""

from __future__ import annotations

import asyncio
import os

import pytest

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DSN, reason="TEST_DATABASE_URL not set; integration DB unavailable"
)

EXPECTED_TABLES = {
    "tenants",
    "users",
    "students",
    "courses",
    "prerequisites",
    "corequisites",
    "sections",
    "program_requirements",
    "student_transcript",
    "plans",
    "enrollments",
    "waitlist",
    "request_queue",
    "outbox",
    "audit_log",
    "notifications",
}


def _alembic_config():  # type: ignore[no-untyped-def]
    from alembic.config import Config

    from keel.config import get_settings

    # Point the app's settings at the test DB, then build an Alembic config.
    os.environ["DATABASE_URL"] = TEST_DSN  # type: ignore[assignment]
    get_settings.cache_clear()
    return Config("alembic.ini")


async def _fetch_counts() -> tuple[set[str], int]:
    import asyncpg

    raw_dsn = TEST_DSN.replace("+asyncpg", "")  # type: ignore[union-attr]
    conn = await asyncpg.connect(raw_dsn)
    try:
        tables = {
            r["tablename"]
            for r in await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        }
        policies = await conn.fetchval(
            "SELECT count(*) FROM pg_policies WHERE policyname = 'tenant_isolation'"
        )
        return tables, int(policies)
    finally:
        await conn.close()


def test_upgrade_creates_tables_and_rls_then_downgrades() -> None:
    from alembic import command

    cfg = _alembic_config()

    command.upgrade(cfg, "head")
    tables, policies = asyncio.run(_fetch_counts())
    assert EXPECTED_TABLES.issubset(tables), f"missing tables: {EXPECTED_TABLES - tables}"
    assert policies == 15, f"expected 15 tenant_isolation policies, found {policies}"

    command.downgrade(cfg, "base")
    tables_after, _ = asyncio.run(_fetch_counts())
    assert EXPECTED_TABLES.isdisjoint(tables_after), "tables remained after downgrade"
