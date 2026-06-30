"""Concrete tenant-scoped repositories (defense-in-depth layer 2).

Per CLAUDE.md §5/§7 and SECURITY.md §2.2, all DB access for tenant-owned tables
goes through a repository that (a) filters by ``tenant_id`` and (b) asserts each
returned row's tenant matches the caller — so a misconfigured RLS policy is still
caught in application code. RLS (layer 1) and pgvector filtering (layer 3) are the
other two independent layers.

These repositories own the SQL for the write/action path (the most safety-critical
surface and the production SIS-seam boundary). Each is constructed bound to one
``(session, tenant_id)`` for its lifetime; the session must already have
``app.tenant_id`` set (open it via ``tenant_session``).

The shared primitives in ``services/actions/__init__.py`` (``ActionRepo``,
``audit_write``, ``outbox_write``) delegate here, so every existing caller routes
through the repository layer without changing its call sites.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from keel.repositories.base import TenantScopedRepository


class LedgerRepository(TenantScopedRepository):
    """Audit-log + outbox writes — the write-and-notify ledger."""

    async def write_audit(
        self,
        *,
        actor: str,
        action: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> int:
        row = await self._session.execute(
            sa.text(
                "INSERT INTO audit_log (tenant_id, actor, action, before, after) "
                "VALUES (:tid, :actor, :action, CAST(:before AS jsonb), CAST(:after AS jsonb)) "
                "RETURNING id"
            ),
            {
                "tid": str(self._tenant_id),
                "actor": actor,
                "action": action,
                "before": json.dumps(before) if before is not None else None,
                "after": json.dumps(after) if after is not None else None,
            },
        )
        return int(row.scalar_one())

    async def write_outbox(self, *, event_type: str, payload: dict[str, Any]) -> UUID:
        row = await self._session.execute(
            sa.text(
                "INSERT INTO outbox (tenant_id, kind, event_type, payload, processed) "
                "VALUES (:tid, :kind, :etype, CAST(:payload AS jsonb), false) "
                "RETURNING id"
            ),
            {
                "tid": str(self._tenant_id),
                "kind": event_type,
                "etype": event_type,
                "payload": json.dumps(payload),
            },
        )
        return UUID(str(row.scalar_one()))


class ActionsRepository(TenantScopedRepository):
    """The staged-action lifecycle table (pending → approved → executed/…)."""

    async def get(self, action_id: UUID) -> dict[str, Any] | None:
        row = await self._session.execute(
            sa.text(
                "SELECT id, tenant_id, student_id, thread_id, type, payload, "
                "status, created_at, decided_at, audit_ref "
                "FROM actions WHERE id = :aid"
            ),
            {"aid": str(action_id)},
        )
        r = row.mappings().first()
        if r is None:
            return None
        # Defense-in-depth: RLS already scopes to this tenant; assert anyway.
        self._assert_tenant(UUID(str(r["tenant_id"])))
        return dict(r)

    async def insert_pending(
        self,
        *,
        student_id: UUID,
        thread_id: str,
        action_type: str,
        payload: dict[str, Any],
    ) -> UUID:
        row = await self._session.execute(
            sa.text(
                "INSERT INTO actions "
                "(tenant_id, student_id, thread_id, type, payload, status) "
                "VALUES (:tid, :sid, :thread, :atype, CAST(:payload AS jsonb), 'pending') "
                "RETURNING id"
            ),
            {
                "tid": str(self._tenant_id),
                "sid": str(student_id),
                "thread": thread_id,
                "atype": action_type,
                "payload": json.dumps(payload),
            },
        )
        return UUID(str(row.scalar_one()))

    async def set_status(
        self, action_id: UUID, *, new_status: str, from_status: str | None
    ) -> None:
        if from_status is not None:
            await self._session.execute(
                sa.text(
                    "UPDATE actions SET status = :new, decided_at = :now "
                    "WHERE id = :aid AND status = :old"
                ),
                {
                    "aid": str(action_id),
                    "new": new_status,
                    "old": from_status,
                    "now": datetime.now(UTC),
                },
            )
        else:
            await self._session.execute(
                sa.text("UPDATE actions SET status = :new, decided_at = :now WHERE id = :aid"),
                {"aid": str(action_id), "new": new_status, "now": datetime.now(UTC)},
            )

    async def set_executed(self, action_id: UUID, audit_ref: int | None = None) -> None:
        await self._session.execute(
            sa.text(
                "UPDATE actions SET status = 'executed', audit_ref = :ref "
                "WHERE id = :aid AND status = 'approved'"
            ),
            {"aid": str(action_id), "ref": audit_ref},
        )

    async def expire_stale(self, *, older_than: datetime) -> int:
        result = await self._session.execute(
            sa.text(
                "UPDATE actions SET status = 'expired', decided_at = :now "
                "WHERE tenant_id = :tid AND status = 'pending' AND created_at < :cutoff "
                "RETURNING id"
            ),
            {"tid": str(self._tenant_id), "now": datetime.now(UTC), "cutoff": older_than},
        )
        return len(result.fetchall())


def ledger(session: AsyncSession, tenant_id: UUID) -> LedgerRepository:
    return LedgerRepository(session, tenant_id)


def actions(session: AsyncSession, tenant_id: UUID) -> ActionsRepository:
    return ActionsRepository(session, tenant_id)
