"""Waitlist write actions — join and leave (spec §3.1 / plan.md §1.5).

Dual-caller rule (spec §2): these are deterministic service functions.
- Agent calls them as tools today (stage_waitlist_join/leave → action pattern).
- Portal button calls them directly on Day 6.

Public API:
  join_waitlist_tx(session, *, action_id, tenant_id, student_id, section_id, auto_enroll)
    → WaitlistResult
  leave_waitlist_tx(session, *, action_id, tenant_id, student_id, section_id)
    → WaitlistResult
  fulfill_waitlist_tx(session, *, waitlist_id, tenant_id, student_id, section_id)
    → WaitlistResult  (called by the capacity-sync worker)
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from keel.logging import get_logger
from keel.services.actions import (
    ActionRepo,
    audit_write,
    notify_context,
    outbox_write,
)

_log = get_logger(__name__)


@dataclass
class WaitlistResult:
    success: bool
    waitlist_id: UUID | None
    message: str


async def join_waitlist_tx(
    session: AsyncSession,
    *,
    action_id: UUID,
    tenant_id: UUID,
    student_id: UUID,
    section_id: UUID,
    auto_enroll: bool,
) -> WaitlistResult:
    """Insert a waitlist row + outbox(waitlist_joined) + audit; mark action executed.

    Idempotent: if the student is already on the waitlist for this section, skip.
    The auto_enroll flag rides in the frozen payload — approving this action covers
    both "put me on the list" and (when True) the conditional future enrollment
    (delegated consent, spec §1a).
    """
    # Idempotency check.
    existing = await session.execute(
        sa.text(
            "SELECT id FROM waitlist "
            "WHERE tenant_id = :tid AND student_id = :sid AND section_id = :secid "
            "AND status = 'waiting'"
        ),
        {"tid": str(tenant_id), "sid": str(student_id), "secid": str(section_id)},
    )
    if existing.scalar_one_or_none():
        return WaitlistResult(
            success=True,
            waitlist_id=None,
            message="Already on the waitlist for this section.",
        )

    # Determine next position.
    pos_row = await session.execute(
        sa.text(
            "SELECT COALESCE(MAX(position), 0) + 1 FROM waitlist "
            "WHERE tenant_id = :tid AND section_id = :secid AND status = 'waiting'"
        ),
        {"tid": str(tenant_id), "secid": str(section_id)},
    )
    position = int(pos_row.scalar_one())

    # Insert waitlist row.
    wl_row = await session.execute(
        sa.text(
            "INSERT INTO waitlist "
            "(tenant_id, student_id, section_id, position, auto_enroll, status) "
            "VALUES (:tid, :sid, :secid, :pos, :ae, 'waiting') "
            "RETURNING id"
        ),
        {
            "tid": str(tenant_id),
            "sid": str(student_id),
            "secid": str(section_id),
            "pos": position,
            "ae": auto_enroll,
        },
    )
    waitlist_id = UUID(str(wl_row.scalar_one()))

    # Outbox — confirmation email (enriched for a real-world message).
    ctx = await notify_context(session, section_id=section_id, student_id=student_id)
    await outbox_write(
        session,
        tenant_id=tenant_id,
        event_type="waitlist_joined",
        payload={
            "student_id": str(student_id),
            "section_id": str(section_id),
            "waitlist_id": str(waitlist_id),
            "auto_enroll": auto_enroll,
            "position": position,
            "action_id": str(action_id),
            **ctx,
        },
    )

    # Audit row.
    audit_id = await audit_write(
        session,
        tenant_id=tenant_id,
        actor=str(student_id),
        action="waitlist.joined",
        before=None,
        after={
            "action_id": str(action_id),
            "section_id": str(section_id),
            "waitlist_id": str(waitlist_id),
            "auto_enroll": auto_enroll,
            "position": position,
        },
    )

    await ActionRepo.set_executed(session, action_id, audit_id)

    _log.info(
        "waitlist.joined",
        student_id=str(student_id),
        section_id=str(section_id),
        position=position,
        auto_enroll=auto_enroll,
        tenant_id=str(tenant_id),
    )
    return WaitlistResult(
        success=True,
        waitlist_id=waitlist_id,
        message=(
            f"You are #{position} on the waitlist. "
            + (
                "You will be automatically enrolled when a seat opens, if you are still eligible."
                if auto_enroll
                else "You will be notified when a seat opens."
            )
        ),
    )


async def leave_waitlist_tx(
    session: AsyncSession,
    *,
    action_id: UUID,
    tenant_id: UUID,
    student_id: UUID,
    section_id: UUID,
) -> WaitlistResult:
    """Mark the student's waitlist entry as 'left'; emit outbox; audit; mark executed."""
    result = await session.execute(
        sa.text(
            "UPDATE waitlist SET status = 'left' "
            "WHERE tenant_id = :tid AND student_id = :sid AND section_id = :secid "
            "AND status = 'waiting' "
            "RETURNING id"
        ),
        {"tid": str(tenant_id), "sid": str(student_id), "secid": str(section_id)},
    )
    row = result.fetchone()
    if not row:
        return WaitlistResult(
            success=False, waitlist_id=None, message="No active waitlist entry found."
        )

    waitlist_id = UUID(str(row[0]))

    ctx = await notify_context(session, section_id=section_id, student_id=student_id)
    await outbox_write(
        session,
        tenant_id=tenant_id,
        event_type="waitlist_left",
        payload={
            "student_id": str(student_id),
            "section_id": str(section_id),
            "waitlist_id": str(waitlist_id),
            "action_id": str(action_id),
            **ctx,
        },
    )

    audit_id = await audit_write(
        session,
        tenant_id=tenant_id,
        actor=str(student_id),
        action="waitlist.left",
        before={"status": "waiting"},
        after={"status": "left", "action_id": str(action_id)},
    )

    await ActionRepo.set_executed(session, action_id, audit_id)

    _log.info(
        "waitlist.left",
        student_id=str(student_id),
        section_id=str(section_id),
        tenant_id=str(tenant_id),
    )
    return WaitlistResult(
        success=True,
        waitlist_id=waitlist_id,
        message="Removed from waitlist.",
    )


async def fulfill_waitlist_tx(
    session: AsyncSession,
    *,
    waitlist_id: UUID,
    tenant_id: UUID,
    student_id: UUID,
    section_id: UUID,
) -> WaitlistResult:
    """Called by the capacity-sync worker when a seat opens and auto_enroll=True.

    Re-verification (engine) is the CALLER's responsibility before this function.
    This function only writes the enrollment + marks waitlist fulfilled.
    """
    # Reuse the SAME idempotency key as a direct enrollment so a (student, section) can
    # never exist as two rows — a dropped "enroll:" row plus a separate "waitlist-fulfill:"
    # row. Both write paths now key on enroll:{student}:{section}.
    idempotency_key = f"enroll:{student_id}:{section_id}"

    # Lock the section row and re-check capacity so a concurrent enrollment can't
    # let the seat-fill overbook (the worker re-verifies before calling this, but
    # the lock makes the write atomic with respect to other writers).
    cap_row = await session.execute(
        sa.text(
            "SELECT capacity, enrolled FROM sections "
            "WHERE id = :secid AND tenant_id = :tid FOR UPDATE"
        ),
        {"secid": str(section_id), "tid": str(tenant_id)},
    )
    cap = cap_row.mappings().first()
    if cap is None or int(cap["enrolled"]) >= int(cap["capacity"]):
        return WaitlistResult(
            success=False, waitlist_id=waitlist_id, message="Section is no longer available."
        )

    # Reactivate an existing dropped row, or insert a fresh one — never duplicate. The
    # section counter is bumped only when the student newly holds the seat (new insert or
    # reactivated drop), not when they were already enrolled.
    existing = await session.execute(
        sa.text(
            "SELECT id, status FROM enrollments "
            "WHERE tenant_id = :tid AND student_id = :sid AND section_id = :secid"
        ),
        {"tid": str(tenant_id), "sid": str(student_id), "secid": str(section_id)},
    )
    prior = existing.mappings().first()
    if prior and prior["status"] == "enrolled":
        return WaitlistResult(
            success=False, waitlist_id=waitlist_id, message="Already enrolled (idempotent)."
        )
    if prior:  # a prior 'dropped' row → reactivate it (the unique key forbids re-insert)
        await session.execute(
            sa.text("UPDATE enrollments SET status = 'enrolled', source = 'keel' WHERE id = :eid"),
            {"eid": str(prior["id"])},
        )
        enroll_id = prior["id"]
    else:
        enroll_row = await session.execute(
            sa.text(
                "INSERT INTO enrollments "
                "(tenant_id, student_id, section_id, status, idempotency_key, source) "
                "VALUES (:tid, :sid, :secid, 'enrolled', :ikey, 'keel') "
                "ON CONFLICT DO NOTHING RETURNING id"
            ),
            {
                "tid": str(tenant_id),
                "sid": str(student_id),
                "secid": str(section_id),
                "ikey": idempotency_key,
            },
        )
        enroll_id = enroll_row.scalar_one_or_none()
        if not enroll_id:
            return WaitlistResult(
                success=False, waitlist_id=waitlist_id, message="Already enrolled (idempotent)."
            )

    # Increment enrolled counter — safe under the FOR UPDATE lock above.
    await session.execute(
        sa.text(
            "UPDATE sections SET enrolled = enrolled + 1 WHERE id = :secid AND tenant_id = :tid"
        ),
        {"secid": str(section_id), "tid": str(tenant_id)},
    )

    # Mark waitlist entry fulfilled.
    await session.execute(
        sa.text("UPDATE waitlist SET status = 'fulfilled' WHERE id = :wid AND tenant_id = :tid"),
        {"wid": str(waitlist_id), "tid": str(tenant_id)},
    )

    ctx = await notify_context(session, section_id=section_id, student_id=student_id)
    await outbox_write(
        session,
        tenant_id=tenant_id,
        event_type="seat_filled_confirmation",
        payload={
            "student_id": str(student_id),
            "section_id": str(section_id),
            "waitlist_id": str(waitlist_id),
            **ctx,
        },
    )

    await audit_write(
        session,
        tenant_id=tenant_id,
        actor="worker:capacity_sync",
        action="waitlist.seat_filled",
        before={"status": "waiting"},
        after={
            "status": "fulfilled",
            "enrollment_id": str(enroll_id),
            "section_id": str(section_id),
        },
    )

    _log.info(
        "waitlist.fulfilled",
        waitlist_id=str(waitlist_id),
        student_id=str(student_id),
        section_id=str(section_id),
    )
    return WaitlistResult(success=True, waitlist_id=waitlist_id, message="Enrolled from waitlist.")
