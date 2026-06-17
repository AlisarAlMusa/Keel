"""Enrollment write action — the deterministic service function (spec §2 dual-caller rule).

Two callers: the agent (via execute_node today) and the portal button (Day 6).
The function owns validation / transaction / idempotency / outbox / audit.
Neither caller can weaken these guarantees.

Public API:
  execute_enrollment_tx(session, *, action_id, tenant_id, student_id, section_ids)
    → ExecuteResult

Called ONLY from execute_node after action.status == 'approved'.
The section_ids come from the FROZEN payload on the action row — never from LLM args.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from keel.services.actions import ActionRepo, audit_write, outbox_write
from keel.logging import get_logger

_log = get_logger(__name__)


@dataclass
class ExecuteResult:
    success: bool
    enrollment_ids: list[UUID]
    message: str


async def execute_enrollment_tx(
    session: AsyncSession,
    *,
    action_id: UUID,
    tenant_id: UUID,
    student_id: UUID,
    section_ids: list[str],  # from frozen payload — never LLM-supplied
) -> ExecuteResult:
    """Write enrollment + outbox + audit in one transaction; mark action executed.

    Idempotency: unique constraint on (student_id, section_id) — re-execute is a no-op.
    Re-validation (capacity, holds, conflicts) is the caller's (execute_node) responsibility
    before calling this function.  This function only does the transactional write.
    """
    enrollment_ids: list[UUID] = []

    for section_id_str in section_ids:
        section_id = UUID(section_id_str)
        idempotency_key = f"enroll:{student_id}:{section_id}"

        # Check for existing enrollment (idempotency — re-execute is a no-op).
        existing = await session.execute(
            sa.text(
                "SELECT id FROM enrollments "
                "WHERE tenant_id = :tid AND student_id = :sid AND section_id = :secid "
                "AND status = 'enrolled'"
            ),
            {"tid": str(tenant_id), "sid": str(student_id), "secid": str(section_id)},
        )
        if existing.scalar_one_or_none():
            _log.info(
                "enrollment.idempotent_skip",
                student_id=str(student_id),
                section_id=str(section_id),
            )
            continue

        # Insert enrollment row.
        enroll_row = await session.execute(
            sa.text(
                "INSERT INTO enrollments "
                "(tenant_id, student_id, section_id, status, idempotency_key) "
                "VALUES (:tid, :sid, :secid, 'enrolled', :ikey) "
                "ON CONFLICT DO NOTHING "
                "RETURNING id"
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
            continue  # conflict — already existed
        enrollment_ids.append(UUID(str(enroll_id)))

        # Increment section.enrolled counter.
        await session.execute(
            sa.text(
                "UPDATE sections SET enrolled = enrolled + 1 "
                "WHERE id = :secid AND tenant_id = :tid AND enrolled < capacity"
            ),
            {"secid": str(section_id), "tid": str(tenant_id)},
        )

        # Outbox row (same transaction — owed-work ledger).
        await outbox_write(
            session,
            tenant_id=tenant_id,
            event_type="enrollment_confirmation",
            payload={
                "student_id": str(student_id),
                "section_id": str(section_id),
                "action_id": str(action_id),
            },
        )

    if not enrollment_ids and not section_ids:
        return ExecuteResult(success=False, enrollment_ids=[], message="No sections to enroll in.")

    # Audit row.
    audit_id = await audit_write(
        session,
        tenant_id=tenant_id,
        actor=str(student_id),
        action="enrollment.executed",
        before=None,
        after={
            "action_id": str(action_id),
            "section_ids": section_ids,
            "enrollment_ids": [str(e) for e in enrollment_ids],
        },
    )

    # Mark action executed (references audit row).
    await ActionRepo.set_executed(session, action_id, audit_id)

    _log.info(
        "enrollment.executed",
        action_id=str(action_id),
        student_id=str(student_id),
        count=len(enrollment_ids),
        tenant_id=str(tenant_id),
    )
    return ExecuteResult(
        success=True,
        enrollment_ids=enrollment_ids,
        message=(
            f"Enrolled in {len(enrollment_ids)} section(s). "
            "A confirmation email is on its way."
        ),
    )
