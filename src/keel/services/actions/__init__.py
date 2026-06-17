"""Action service layer — the resumable-agent write pattern (spec §2).

Public surface:
  ActionRepo   — CRUD for the actions table.
  audit_write  — insert one audit_log row inside a transaction.
  outbox_write — insert one outbox row inside a transaction.

All write actions (enrollment, waitlist, petition, major-change, graduation-app)
share these primitives.  Each action type provides its own execute_*_tx function
that calls audit_write + outbox_write inside the same transaction.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from keel.logging import get_logger

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# ActionRepo — all action-table access goes through here
# ---------------------------------------------------------------------------


class ActionRepo:
    """Thin async repository for the actions table.

    Every method is tenant-scoped via the session's RLS setting.
    Student-level isolation (student_id check) is the caller's responsibility
    (handled by the approve handler before calling any repo method).
    """

    @staticmethod
    async def get(session: AsyncSession, action_id: UUID) -> dict[str, Any] | None:
        """Load one action row.  RLS scopes to current tenant — cross-tenant → None."""
        row = await session.execute(
            sa.text(
                "SELECT id, tenant_id, student_id, thread_id, type, payload, "
                "status, created_at, decided_at, audit_ref "
                "FROM actions WHERE id = :aid"
            ),
            {"aid": str(action_id)},
        )
        r = row.mappings().first()
        return dict(r) if r else None

    @staticmethod
    async def insert_pending(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        student_id: UUID,
        thread_id: str,
        action_type: str,
        payload: dict[str, Any],
    ) -> UUID:
        """Insert a pending action row and return its id."""
        row = await session.execute(
            sa.text(
                "INSERT INTO actions "
                "(tenant_id, student_id, thread_id, type, payload, status) "
                "VALUES (:tid, :sid, :thread, :atype, :payload::jsonb, 'pending') "
                "RETURNING id"
            ),
            {
                "tid": str(tenant_id),
                "sid": str(student_id),
                "thread": thread_id,
                "atype": action_type,
                "payload": json.dumps(payload),
            },
        )
        return UUID(str(row.scalar_one()))

    @staticmethod
    async def set_approved(session: AsyncSession, action_id: UUID) -> None:
        """Transition pending → approved; record decided_at."""
        await session.execute(
            sa.text(
                "UPDATE actions SET status = 'approved', decided_at = :now "
                "WHERE id = :aid AND status = 'pending'"
            ),
            {"aid": str(action_id), "now": datetime.now(UTC)},
        )

    @staticmethod
    async def set_rejected(session: AsyncSession, action_id: UUID) -> None:
        """Transition pending → rejected; record decided_at."""
        await session.execute(
            sa.text(
                "UPDATE actions SET status = 'rejected', decided_at = :now "
                "WHERE id = :aid AND status = 'pending'"
            ),
            {"aid": str(action_id), "now": datetime.now(UTC)},
        )

    @staticmethod
    async def set_executed(
        session: AsyncSession,
        action_id: UUID,
        audit_ref: int,
    ) -> None:
        """Transition approved → executed; set audit_ref."""
        await session.execute(
            sa.text(
                "UPDATE actions SET status = 'executed', audit_ref = :ref "
                "WHERE id = :aid AND status = 'approved'"
            ),
            {"aid": str(action_id), "ref": audit_ref},
        )

    @staticmethod
    async def set_failed(session: AsyncSession, action_id: UUID) -> None:
        """Transition approved → failed (e.g. re-validation failed on resume)."""
        await session.execute(
            sa.text("UPDATE actions SET status = 'failed', decided_at = :now WHERE id = :aid"),
            {"aid": str(action_id), "now": datetime.now(UTC)},
        )

    @staticmethod
    async def expire_stale(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        older_than: datetime,
    ) -> int:
        """Expire all pending actions older than older_than for a tenant. Returns count."""
        result = await session.execute(
            sa.text(
                "UPDATE actions SET status = 'expired', decided_at = :now "
                "WHERE tenant_id = :tid AND status = 'pending' AND created_at < :cutoff "
                "RETURNING id"
            ),
            {
                "tid": str(tenant_id),
                "now": datetime.now(UTC),
                "cutoff": older_than,
            },
        )
        return len(result.fetchall())


# ---------------------------------------------------------------------------
# Shared write helpers (called inside execute_* transactions)
# ---------------------------------------------------------------------------


async def audit_write(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    actor: str,
    action: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> int:
    """Insert one audit_log row and return its id (for action.audit_ref)."""
    row = await session.execute(
        sa.text(
            "INSERT INTO audit_log (tenant_id, actor, action, before, after) "
            "VALUES (:tid, :actor, :action, :before::jsonb, :after::jsonb) "
            "RETURNING id"
        ),
        {
            "tid": str(tenant_id),
            "actor": actor,
            "action": action,
            "before": json.dumps(before) if before is not None else None,
            "after": json.dumps(after) if after is not None else None,
        },
    )
    return int(row.scalar_one())


async def outbox_write(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    event_type: str,
    payload: dict[str, Any],
) -> UUID:
    """Insert one outbox row (unprocessed) and return its id.

    Called inside the same transaction as the domain write — the outbox row is
    the owed-work ledger entry that guarantees the side effect fires even across
    crashes (no dual-write inconsistency).
    """
    row = await session.execute(
        sa.text(
            "INSERT INTO outbox (tenant_id, kind, event_type, payload, processed) "
            "VALUES (:tid, :kind, :etype, :payload::jsonb, false) "
            "RETURNING id"
        ),
        {
            "tid": str(tenant_id),
            "kind": event_type,
            "etype": event_type,
            "payload": json.dumps(payload),
        },
    )
    return UUID(str(row.scalar_one()))
