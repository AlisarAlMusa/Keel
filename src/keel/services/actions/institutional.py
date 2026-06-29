"""Institutional write actions (F1–F4) — the ONE shared action pattern (spec §3).

Four intents over the existing ``request_queue`` + ``outbox`` + ``audit_log``
subsystems — not four pipelines. The shared pattern, tested once (Appendix B):

    propose → engine-validate → STUDENT approves → BEGIN TXN:
        write request_queue row (status=pending, idempotency_key)
        write outbox event
      COMMIT
    → worker publishes outbox → audit_log row

Safety invariants enforced here:
  - ``approved`` MUST be True for any write. approved=False → zero rows (proposal only).
  - Idempotency: partial unique index on (tenant_id, student_id, type, target)
    WHERE status='pending' — a second filing with the same key is a no-op.
  - Tenant isolation: every write runs inside an RLS-scoped ``tenant_session``.
  - F3 (petition) writes a PETITION request row and NEVER an enrollment row.

The agent never calls these with approved=True. The agent's institutional tools
propose only (they cannot set ``approved``); the True path is reached solely by an
explicit student-approval action (approval UI / endpoint, Day 6) or a direct call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from keel.infra.database.session import tenant_session
from keel.logging import get_logger
from keel.services.actions import audit_write, outbox_write

_log = get_logger(__name__)

_SessionFactory = async_sessionmaker[AsyncSession]

# Canonical request types (spec Appendix A) → request_queue.type DB values
# (the DB check constraint allows 'graduation','major_change','petition').
_DB_TYPE: dict[str, str] = {
    "GRADUATION_APPLICATION": "graduation",
    "MAJOR_CHANGE": "major_change",
    "PETITION": "petition",
}
_EVENT_TYPE: dict[str, str] = {
    "GRADUATION_APPLICATION": "graduation_application",
    "MAJOR_CHANGE": "major_change",
    "PETITION": "petition",
}


@dataclass
class FileRequestResult:
    """Outcome of an institutional filing."""

    written: bool  # True = a new PENDING row was created this call
    request_id: UUID | None
    status: str  # "proposal" | "pending" | "duplicate"
    message: str


@dataclass
class EscalateResult:
    sent: bool
    advisor_email: str | None
    message: str


# ---------------------------------------------------------------------------
# Shared write primitive (the one action pattern)
# ---------------------------------------------------------------------------


async def _file_request_tx(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    student_id: UUID,
    request_type: str,  # canonical: GRADUATION_APPLICATION | MAJOR_CHANGE | PETITION
    target: str,
    payload: dict[str, Any],
    actor: str,
) -> FileRequestResult:
    """Write one PENDING request_queue row + outbox event + audit, idempotently.

    Assumes approval already granted by the caller. Runs inside the caller's
    transaction (``session`` is RLS-scoped). Idempotency comes from the partial
    unique index — a duplicate PENDING filing is a no-op (no second row, no
    second outbox event → no notification storm).
    """
    db_type = _DB_TYPE[request_type]
    idempotency_key = f"{tenant_id}:{student_id}:{request_type}:{target}"

    import json as _json

    row = await session.execute(
        sa.text(
            "INSERT INTO request_queue "
            "(tenant_id, student_id, type, target, status, payload, idempotency_key) "
            "VALUES (:tid, :sid, :type, :target, 'pending', CAST(:payload AS jsonb), :ikey) "
            "ON CONFLICT (tenant_id, student_id, type, target) WHERE status = 'pending' "
            "DO NOTHING "
            "RETURNING id"
        ),
        {
            "tid": str(tenant_id),
            "sid": str(student_id),
            "type": db_type,
            "target": target,
            "payload": _json.dumps(payload),
            "ikey": idempotency_key,
        },
    )
    new_id = row.scalar_one_or_none()

    if new_id is None:
        # Conflict — a PENDING row already exists. Idempotent no-op.
        existing = await session.execute(
            sa.text(
                "SELECT id FROM request_queue "
                "WHERE tenant_id = :tid AND student_id = :sid "
                "AND type = :type AND target = :target AND status = 'pending'"
            ),
            {"tid": str(tenant_id), "sid": str(student_id), "type": db_type, "target": target},
        )
        existing_id = existing.scalar_one_or_none()
        _log.info(
            "institutional.idempotent_skip",
            request_type=request_type,
            target=target,
            tenant_id=str(tenant_id),
        )
        return FileRequestResult(
            written=False,
            request_id=UUID(str(existing_id)) if existing_id else None,
            status="duplicate",
            message=f"A pending {request_type.replace('_', ' ').lower()} already exists.",
        )

    request_id = UUID(str(new_id))

    # Outbox event (same transaction — owed-work ledger; publisher is generic).
    await outbox_write(
        session,
        tenant_id=tenant_id,
        event_type=_EVENT_TYPE[request_type],
        payload={
            "request_id": str(request_id),
            "student_id": str(student_id),
            "request_type": request_type,
            "target": target,
        },
    )

    # Audit row.
    await audit_write(
        session,
        tenant_id=tenant_id,
        actor=actor,
        action=f"institutional.{db_type}.filed",
        before=None,
        after={"request_id": str(request_id), "target": target, "request_type": request_type},
    )

    _log.info(
        "institutional.filed",
        request_id=str(request_id),
        request_type=request_type,
        target=target,
        tenant_id=str(tenant_id),
    )
    return FileRequestResult(
        written=True,
        request_id=request_id,
        status="pending",
        message=(
            f"Your {request_type.replace('_', ' ').lower()} has been filed (target: {target}). "
            "The registrar's office will review it."
        ),
    )


# ---------------------------------------------------------------------------
# F1 — Graduation Application
# ---------------------------------------------------------------------------


async def apply_graduation(
    session_factory: _SessionFactory,
    *,
    tenant_id: UUID,
    student_id: UUID,
    program: str,
    approved: bool = False,
    payload: dict[str, Any] | None = None,
) -> FileRequestResult:
    """File a graduation application (spec F1). approved MUST be True to write.

    Idempotency key target = program. Re-apply allowed only after the prior
    PENDING request is resolved.
    """
    if not approved:
        return FileRequestResult(
            written=False,
            request_id=None,
            status="proposal",
            message="Graduation application prepared — awaiting your approval.",
        )
    async with tenant_session(session_factory, tenant_id) as session:
        return await _file_request_tx(
            session,
            tenant_id=tenant_id,
            student_id=student_id,
            request_type="GRADUATION_APPLICATION",
            target=program,
            payload=payload or {"program": program},
            actor=str(student_id),
        )


# ---------------------------------------------------------------------------
# F2 — Major-Change Request
# ---------------------------------------------------------------------------


async def request_major_change(
    session_factory: _SessionFactory,
    *,
    tenant_id: UUID,
    student_id: UUID,
    target_program_id: str,
    approved: bool = False,
    impact_summary: str = "",
) -> FileRequestResult:
    """File a major-change request (spec F2). approved MUST be True to write.

    Idempotency key target = target_program_id.
    """
    if not approved:
        return FileRequestResult(
            written=False,
            request_id=None,
            status="proposal",
            message="Major-change request prepared — awaiting your approval.",
        )
    async with tenant_session(session_factory, tenant_id) as session:
        return await _file_request_tx(
            session,
            tenant_id=tenant_id,
            student_id=student_id,
            request_type="MAJOR_CHANGE",
            target=target_program_id,
            payload={"target_program_id": target_program_id, "impact_summary": impact_summary},
            actor=str(student_id),
        )


# ---------------------------------------------------------------------------
# F3 — Petition / Prerequisite Override
# ---------------------------------------------------------------------------


async def submit_petition(
    session_factory: _SessionFactory,
    *,
    tenant_id: UUID,
    student_id: UUID,
    course_id: str,
    justification: str,
    approved: bool = False,
    draft: str = "",
) -> FileRequestResult:
    """File a prerequisite-override PETITION (spec F3). approved MUST be True to write.

    The engine's eligibility block is NEVER removed here — this writes a routed
    request to a human, NEVER an enrollment. Idempotency key target = course_id.
    """
    if not approved:
        return FileRequestResult(
            written=False,
            request_id=None,
            status="proposal",
            message=(
                "Petition drafted — awaiting your approval. This is a request, not an enrollment."
            ),
        )
    async with tenant_session(session_factory, tenant_id) as session:
        return await _file_request_tx(
            session,
            tenant_id=tenant_id,
            student_id=student_id,
            request_type="PETITION",
            target=course_id,
            payload={"course_id": course_id, "justification": justification, "draft": draft},
            actor=str(student_id),
        )


# ---------------------------------------------------------------------------
# F4 — Advisor Escalation (email handoff only; no request_queue row)
# ---------------------------------------------------------------------------


async def resolve_advisor_email(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    program: str | None,
) -> tuple[str, str] | None:
    """Resolve (name, email) for a program from the advisors table (RLS-scoped).

    Prefers a program-specific advisor; falls back to the tenant catch-all
    (program IS NULL). Returns None if no advisor is seeded.
    """
    row = await session.execute(
        sa.text(
            "SELECT name, email FROM advisors "
            "WHERE tenant_id = :tid AND (program = :program OR program IS NULL) "
            "ORDER BY (program IS NULL) ASC "  # program-specific first, catch-all last
            "LIMIT 1"
        ),
        {"tid": str(tenant_id), "program": program},
    )
    r = row.mappings().first()
    if not r:
        return None
    return str(r["name"]), str(r["email"])


async def escalate(
    session_factory: _SessionFactory,
    *,
    tenant_id: UUID,
    student_id: UUID,
    reason: str,
    program: str | None = None,
    handoff_summary: str = "",
    student_name: str = "",
    approved: bool = False,
) -> EscalateResult:
    """Escalate to a human advisor via outbox email (spec F4). approved MUST be True.

    No request_queue row, no advisor login/role — pure email handoff. The target
    email is resolved from the RLS-scoped advisors table by program.
    """
    if not approved:
        return EscalateResult(
            sent=False,
            advisor_email=None,
            message="Escalation prepared — awaiting your approval to send.",
        )
    async with tenant_session(session_factory, tenant_id) as session:
        resolved = await resolve_advisor_email(session, tenant_id=tenant_id, program=program)
        if resolved is None:
            return EscalateResult(
                sent=False,
                advisor_email=None,
                message=(
                    "No advisor is configured for your program — "
                    "please contact the office directly."
                ),
            )
        advisor_name, advisor_email = resolved

        # Two emails: the handoff to the advisor, and a short ack to the student.
        await outbox_write(
            session,
            tenant_id=tenant_id,
            event_type="escalation_email",
            payload={
                "to": advisor_email,
                "advisor_name": advisor_name,
                "student_id": str(student_id),
                "reason": reason,
                "handoff_summary": handoff_summary,
            },
        )
        await outbox_write(
            session,
            tenant_id=tenant_id,
            event_type="escalation_ack",
            payload={
                "student_id": str(student_id),
                "student_name": student_name,
            },
        )
        await audit_write(
            session,
            tenant_id=tenant_id,
            actor=str(student_id),
            action="institutional.escalation.sent",
            before=None,
            after={"advisor_email": advisor_email, "reason": reason},
        )

    _log.info(
        "institutional.escalated",
        advisor_email=advisor_email,
        student_id=str(student_id),
        tenant_id=str(tenant_id),
    )
    return EscalateResult(
        sent=True,
        advisor_email=advisor_email,
        message=f"Your request was escalated to {advisor_name} ({advisor_email}).",
    )
