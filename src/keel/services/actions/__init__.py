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

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from keel.logging import get_logger

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Notification context — real-world, human-readable details for emails / alerts
# ---------------------------------------------------------------------------


def fmt_slots(slots: object) -> str:
    """Render meeting slots like 'Tue/Thu 10:00 AM–11:15 AM' for a notification body."""
    if not slots or not isinstance(slots, list):
        return ""

    def _hm(mins: int) -> str:
        h, m = divmod(int(mins), 60)
        ampm = "AM" if h < 12 else "PM"
        return f"{h % 12 or 12}:{m:02d} {ampm}"

    ordered = sorted(slots, key=lambda s: (int(s["start_min"]), str(s["day"])))
    days = "/".join(str(s["day"]).capitalize() for s in ordered)
    return f"{days} {_hm(ordered[0]['start_min'])}–{_hm(ordered[0]['end_min'])}"


def section_label(ctx: dict[str, str]) -> str:
    """A human section descriptor: 'the Tue/Thu 10:00 AM–11:15 AM section with Prof. Nasser'."""
    when = ctx.get("when") or ""
    instr = ctx.get("instructor") or ""
    if when and instr:
        return f"the {when} section with {instr}"
    if when:
        return f"the {when} section"
    if instr:
        return f"the section with {instr}"
    return "the section"


async def notify_context(
    session: AsyncSession, *, section_id: UUID, student_id: UUID
) -> dict[str, str]:
    """Human-readable details for a notification: student name, course, instructor, time.

    Best-effort — missing pieces fall back to friendly placeholders so a real-world
    email never shows a raw UUID. Shared by the action services and the worker.
    """
    sec = await session.execute(
        sa.text(
            "SELECT sec.course_code, sec.instructor, sec.slots, c.name AS course_name "
            "FROM sections sec "
            "LEFT JOIN courses c "
            "  ON c.tenant_id = sec.tenant_id AND c.code = sec.course_code "
            "WHERE sec.id = :secid"
        ),
        {"secid": str(section_id)},
    )
    s = sec.mappings().first()
    name_row = await session.execute(
        sa.text(
            "SELECT u.display_name FROM students st "
            "JOIN users u ON u.id = st.user_id WHERE st.id = :sid"
        ),
        {"sid": str(student_id)},
    )
    student_name = name_row.scalar_one_or_none()
    return {
        "student_name": student_name or "there",
        "course_code": str(s["course_code"]) if s else "your course",
        "course_name": str(s["course_name"] or "") if s else "",
        "instructor": str(s["instructor"] or "the instructor") if s else "the instructor",
        "when": fmt_slots(s["slots"]) if s else "",
    }


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
        """Insert a pending action row and return its id (via ActionsRepository)."""
        from keel.repositories.core import ActionsRepository

        return await ActionsRepository(session, tenant_id).insert_pending(
            student_id=student_id,
            thread_id=thread_id,
            action_type=action_type,
            payload=payload,
        )

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
        audit_ref: int | None = None,
    ) -> None:
        """Transition approved → executed; set audit_ref (NULL when the write's
        own audit row id isn't threaded back, e.g. institutional filings)."""
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
        """Expire all pending actions older than older_than (via ActionsRepository)."""
        from keel.repositories.core import ActionsRepository

        return await ActionsRepository(session, tenant_id).expire_stale(older_than=older_than)


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
    """Insert one audit_log row and return its id (for action.audit_ref).

    Delegates to the tenant-scoped ``LedgerRepository`` (defense-in-depth layer 2).
    """
    from keel.repositories.core import LedgerRepository

    return await LedgerRepository(session, tenant_id).write_audit(
        actor=actor, action=action, before=before, after=after
    )


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
    crashes (no dual-write inconsistency). Delegates to ``LedgerRepository``.
    """
    from keel.repositories.core import LedgerRepository

    return await LedgerRepository(session, tenant_id).write_outbox(
        event_type=event_type, payload=payload
    )
