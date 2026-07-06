"""Concrete tenant-scoped repositories (defense-in-depth layer 2).

Per CLAUDE.md §5/§7 and SECURITY.md §2.2, all DB access for tenant-owned tables
goes through a repository that filters by ``tenant_id`` in the query. RLS (layer
1, in the DB) and pgvector filtering (layer 3) are the other two independent
layers of tenant isolation.

These repositories own the SQL for the write/action path (the most safety-critical
surface and the production SIS-seam boundary). Each is constructed bound to one
``(session, tenant_id)`` for its lifetime; the session must already have
``app.tenant_id`` set (open it via ``tenant_session``).

``ActionsRepository`` is the single home for staged-action lifecycle CRUD (get,
insert_pending, set_approved/rejected/executed, expire_stale); the write-path
callers construct it directly. ``LedgerRepository`` owns audit-log + outbox writes
(the ``audit_write`` / ``outbox_write`` primitives in ``services/actions`` delegate
to it).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa

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
    """The staged-action lifecycle table (pending → approved → executed/…).

    The single home for all ``actions`` table access. RLS scopes every statement
    to the current tenant; student-level isolation (student_id check) is the
    caller's responsibility (the approve handler checks it before calling here).
    """

    async def get(self, action_id: UUID) -> dict[str, Any] | None:
        """Load one action row. RLS scopes to current tenant — cross-tenant → None."""
        row = await self._session.execute(
            sa.text(
                "SELECT id, tenant_id, student_id, thread_id, type, payload, "
                "status, created_at, decided_at, audit_ref "
                "FROM actions WHERE id = :aid"
            ),
            {"aid": str(action_id)},
        )
        r = row.mappings().first()
        return dict(r) if r else None

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

    async def set_approved(self, action_id: UUID) -> None:
        """Transition pending → approved; record decided_at."""
        await self._session.execute(
            sa.text(
                "UPDATE actions SET status = 'approved', decided_at = :now "
                "WHERE id = :aid AND status = 'pending'"
            ),
            {"aid": str(action_id), "now": datetime.now(UTC)},
        )

    async def set_rejected(self, action_id: UUID) -> None:
        """Transition pending → rejected; record decided_at."""
        await self._session.execute(
            sa.text(
                "UPDATE actions SET status = 'rejected', decided_at = :now "
                "WHERE id = :aid AND status = 'pending'"
            ),
            {"aid": str(action_id), "now": datetime.now(UTC)},
        )

    async def set_executed(self, action_id: UUID, audit_ref: int | None = None) -> None:
        """Transition approved → executed; set audit_ref (NULL when the write's own
        audit row id isn't threaded back, e.g. institutional filings)."""
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
