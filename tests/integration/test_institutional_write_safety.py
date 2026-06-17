"""Day-5 write-action-safety gate (DB half) — institutional writes (spec §5, App. B).

Real-Postgres assertions that the one institutional action pattern is safe:

  - no-write-without-approval  → approved=False writes 0 queue + 0 outbox rows
  - cross-tenant-never-writes  → a tenant-A filing never appears for tenant B (RLS)
  - petition-never-enrolls     → F3 writes a PETITION row, never an enrollment row
  - idempotency                → double-file (same key) yields exactly one PENDING row

Requires ``TEST_DATABASE_URL`` (asyncpg DSN); skipped otherwise so the unit CI job
stays green without infrastructure. Self-contained: it creates and tears down its
own tenants/students, independent of the dev seed.
"""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
_SKIP_REASON = "TEST_DATABASE_URL not set; integration DB unavailable"
pytestmark = [
    pytest.mark.skipif(not TEST_DSN, reason=_SKIP_REASON),
    pytest.mark.asyncio,
]


async def _setup():  # type: ignore[no-untyped-def]
    """Create engine + two tenants, one student each. Returns (factory, ids, cleanup)."""
    from keel.infra import database as db_infra
    from keel.infra.database.session import tenant_session

    engine = db_infra.create_engine(TEST_DSN)  # type: ignore[arg-type]
    factory = db_infra.create_session_factory(engine)

    tenant_a, tenant_b = uuid4(), uuid4()
    student_a, student_b = uuid4(), uuid4()

    # Tenants are not tenant-owned (no RLS) — plain insert.
    async with factory() as s:
        async with s.begin():
            for tid, slug in ((tenant_a, "wsafe-a"), (tenant_b, "wsafe-b")):
                await s.execute(
                    sa.text("INSERT INTO tenants (id, slug, name) VALUES (:id, :slug, :name)"),
                    {"id": str(tid), "slug": f"{slug}-{tid.hex[:8]}", "name": slug},
                )

    # Students require the RLS tenant context (WITH CHECK).
    for tid, sid in ((tenant_a, student_a), (tenant_b, student_b)):
        async with tenant_session(factory, tid) as s:
            await s.execute(
                sa.text(
                    "INSERT INTO students "
                    "(id, tenant_id, program_code, current_term, current_year) "
                    "VALUES (:id, :tid, 'BSCS', 'fall', 2026)"
                ),
                {"id": str(sid), "tid": str(tid)},
            )

    async def cleanup() -> None:
        async with factory() as s:
            async with s.begin():
                await s.execute(
                    sa.text("DELETE FROM tenants WHERE id IN (:a, :b)"),
                    {"a": str(tenant_a), "b": str(tenant_b)},
                )
        await engine.dispose()

    ids = {"ta": tenant_a, "tb": tenant_b, "sa": student_a, "sb": student_b}
    return factory, ids, cleanup


async def _count(factory, tenant_id: UUID, table: str, **where) -> int:  # type: ignore[no-untyped-def]
    from keel.infra.database.session import tenant_session

    clause = " AND ".join(f"{k} = :{k}" for k in where)
    sql = f"SELECT count(*) FROM {table}"
    if clause:
        sql += f" WHERE {clause}"
    async with tenant_session(factory, tenant_id) as s:
        row = await s.execute(sa.text(sql), {k: str(v) for k, v in where.items()})
        return int(row.scalar_one())


async def test_no_write_without_approval() -> None:
    from keel.services.actions import institutional as inst

    factory, ids, cleanup = await _setup()
    try:
        await inst.apply_graduation(
            factory, tenant_id=ids["ta"], student_id=ids["sa"], program="BSCS", approved=False
        )
        await inst.submit_petition(
            factory,
            tenant_id=ids["ta"],
            student_id=ids["sa"],
            course_id="CS301",
            justification="x",
            approved=False,
        )
        assert await _count(factory, ids["ta"], "request_queue", student_id=ids["sa"]) == 0
        assert await _count(factory, ids["ta"], "outbox") == 0
    finally:
        await cleanup()


async def test_cross_tenant_never_writes() -> None:
    from keel.services.actions import institutional as inst

    factory, ids, cleanup = await _setup()
    try:
        result = await inst.apply_graduation(
            factory, tenant_id=ids["ta"], student_id=ids["sa"], program="BSCS", approved=True
        )
        assert result.written is True
        # Tenant A has the row; tenant B sees nothing (RLS + repo scoping).
        assert await _count(factory, ids["ta"], "request_queue", student_id=ids["sa"]) == 1
        assert await _count(factory, ids["tb"], "request_queue") == 0
    finally:
        await cleanup()


async def test_petition_never_enrolls() -> None:
    from keel.services.actions import institutional as inst

    factory, ids, cleanup = await _setup()
    try:
        await inst.submit_petition(
            factory,
            tenant_id=ids["ta"],
            student_id=ids["sa"],
            course_id="CS301",
            justification="please",
            approved=True,
        )
        assert (
            await _count(factory, ids["ta"], "request_queue", student_id=ids["sa"], type="petition")
            == 1
        )
        assert await _count(factory, ids["ta"], "enrollments", student_id=ids["sa"]) == 0
    finally:
        await cleanup()


async def test_idempotent_pending() -> None:
    from keel.services.actions import institutional as inst

    factory, ids, cleanup = await _setup()
    try:
        for _ in range(2):
            await inst.apply_graduation(
                factory, tenant_id=ids["ta"], student_id=ids["sa"], program="BSCS", approved=True
            )
        # Exactly one PENDING row despite the double-file.
        assert (
            await _count(
                factory, ids["ta"], "request_queue", student_id=ids["sa"], status="pending"
            )
            == 1
        )
        # And exactly one outbox event (no notification storm).
        assert await _count(factory, ids["ta"], "outbox") == 1
    finally:
        await cleanup()
