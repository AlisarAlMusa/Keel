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

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from keel.logging import get_logger
from keel.services.actions import ActionRepo, audit_write, notify_context, outbox_write

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

    Precondition re-validation at execution time (spec §8 step 2): the plan was
    verifier-valid at propose/stage time, but two preconditions can change in the
    approval window and MUST be re-checked under the write transaction:

      • registration hold — a hold placed after staging blocks the whole write.
      • section capacity   — re-checked with ``SELECT … FOR UPDATE`` so two
        concurrent approvals cannot overbook a section (the previous code inserted
        the enrollment unconditionally and only the counter UPDATE was capacity-
        gated, which overbooked + drifted the counter).

    Prerequisite / time-conflict / credit-cap legality does not change between
    stage and a short approval window (transcript + catalog are stable), so those
    rest on the propose-time ``verify()`` that produced the plan.

    Idempotency: unique constraint on (tenant_id, idempotency_key) — re-execute is
    a no-op.
    """
    # --- Precondition: registration hold (re-checked at execution time) ---
    hold_row = await session.execute(
        sa.text("SELECT has_hold FROM students WHERE id = :sid AND tenant_id = :tid"),
        {"sid": str(student_id), "tid": str(tenant_id)},
    )
    student = hold_row.mappings().first()
    if student is None:
        return ExecuteResult(success=False, enrollment_ids=[], message="Student not found.")
    if student["has_hold"]:
        _log.warning(
            "enrollment.blocked_by_hold", student_id=str(student_id), tenant_id=str(tenant_id)
        )
        return ExecuteResult(
            success=False,
            enrollment_ids=[],
            message=(
                "A registration hold on your account blocks enrollment. "
                "Please resolve the hold with the registrar's office, then try again."
            ),
        )

    # Same-semester override (decision D-P6-001): the term/year this plan targets.
    # The actual drop of any superseded prior registration happens AFTER the new
    # sections are secured below — so a failed enrollment never leaves the student
    # with a dropped prior plan and nothing in its place.
    target_term_rows = await session.execute(
        sa.text(
            "SELECT DISTINCT term, year FROM sections WHERE tenant_id = :tid AND id = ANY(:ids)"
        ),
        {"tid": str(tenant_id), "ids": [str(s) for s in section_ids]},
    )
    target_terms = [(r["term"], int(r["year"])) for r in target_term_rows.mappings()]
    incoming_ids = {str(s) for s in section_ids}

    enrollment_ids: list[UUID] = []
    full_sections: list[str] = []
    secured_count = 0  # incoming sections the student now holds (new + overlap)
    enrolled_ctx: list[dict[str, Any]] = []  # one entry per NEW seat — for ONE summary email
    student_name: str = "there"

    for section_id_str in section_ids:
        section_id = UUID(section_id_str)
        idempotency_key = f"enroll:{student_id}:{section_id}"

        # Check for an existing row for this exact (student, section).
        #   enrolled  → idempotent no-op (re-execute / overlapping override).
        #   dropped   → reactivate it (the unique idempotency_key forbids a re-insert,
        #               so we flip the prior 'dropped' row back to 'enrolled').
        existing = await session.execute(
            sa.text(
                "SELECT id, status FROM enrollments "
                "WHERE tenant_id = :tid AND student_id = :sid AND section_id = :secid"
            ),
            {"tid": str(tenant_id), "sid": str(student_id), "secid": str(section_id)},
        )
        prior_row = existing.mappings().first()
        if prior_row and prior_row["status"] == "enrolled":
            _log.info(
                "enrollment.idempotent_skip",
                student_id=str(student_id),
                section_id=str(section_id),
            )
            secured_count += 1  # already holds this incoming section
            continue

        # Capacity re-check under a row lock — prevents concurrent overbooking.
        cap_row = await session.execute(
            sa.text(
                "SELECT capacity, enrolled FROM sections "
                "WHERE id = :secid AND tenant_id = :tid FOR UPDATE"
            ),
            {"secid": str(section_id), "tid": str(tenant_id)},
        )
        cap = cap_row.mappings().first()
        if cap is None:
            full_sections.append(section_id_str)
            continue
        if int(cap["enrolled"]) >= int(cap["capacity"]):
            _log.info(
                "enrollment.section_full_at_execute",
                section_id=str(section_id),
                tenant_id=str(tenant_id),
            )
            full_sections.append(section_id_str)
            continue

        # Write the enrollment. source='keel' stamps the "via Keel" provenance the
        # portal's My Schedule badge reads (the write went through Keel).
        if prior_row is not None:
            # A prior 'dropped' row exists for this section — reactivate it instead
            # of inserting (the unique idempotency_key would reject a fresh INSERT).
            await session.execute(
                sa.text(
                    "UPDATE enrollments SET status = 'enrolled', source = 'keel' WHERE id = :eid"
                ),
                {"eid": str(prior_row["id"])},
            )
            enrollment_ids.append(UUID(str(prior_row["id"])))
        else:
            enroll_row = await session.execute(
                sa.text(
                    "INSERT INTO enrollments "
                    "(tenant_id, student_id, section_id, status, idempotency_key, source) "
                    "VALUES (:tid, :sid, :secid, 'enrolled', :ikey, 'keel') "
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
        secured_count += 1

        # Increment section.enrolled — safe under the FOR UPDATE lock above.
        await session.execute(
            sa.text(
                "UPDATE sections SET enrolled = enrolled + 1 WHERE id = :secid AND tenant_id = :tid"
            ),
            {"secid": str(section_id), "tid": str(tenant_id)},
        )

        # Collect this section's details. A SINGLE consolidated confirmation email is
        # emitted after the loop (one email listing every registered course), not one
        # email per course.
        ctx = await notify_context(session, section_id=section_id, student_id=student_id)
        student_name = ctx.get("student_name") or student_name
        enrolled_ctx.append(
            {
                "section_id": str(section_id),
                "course_code": ctx.get("course_code"),
                "course_name": ctx.get("course_name"),
                "instructor": ctx.get("instructor"),
                "when": ctx.get("when"),
            }
        )

    # --- Same-semester override drop (decision D-P6-001) ---
    # Now that the new sections are secured, drop any prior enrollment for the SAME
    # term/year that is not part of this registration, so the approved plan fully
    # replaces the previous one. Gated on secured_count so a fully-failed enrollment
    # (every incoming section full) never drops the student's existing registration.
    #
    # ALSO gated on registering MORE THAN ONE section: a full-plan submission replaces
    # the term, but a SINGLE-section registration is an ADD (a freshly-opened waitlist
    # seat, or "also register me for CS301") and must never wipe the student's other
    # courses for that term. This is what keeps the waitlist→register flow additive.
    dropped_sections: list[str] = []
    if secured_count > 0 and len(incoming_ids) > 1:
        for term, year in target_terms:
            prior = await session.execute(
                sa.text(
                    "SELECT e.id AS enroll_id, e.section_id "
                    "FROM enrollments e JOIN sections s ON s.id = e.section_id "
                    "WHERE e.tenant_id = :tid AND e.student_id = :sid "
                    "AND e.status = 'enrolled' AND s.term = :term AND s.year = :yr"
                ),
                {"tid": str(tenant_id), "sid": str(student_id), "term": term, "yr": year},
            )
            for row in prior.mappings():
                if str(row["section_id"]) in incoming_ids:
                    continue  # keep overlapping sections — no churn
                await session.execute(
                    sa.text("UPDATE enrollments SET status = 'dropped' WHERE id = :eid"),
                    {"eid": str(row["enroll_id"])},
                )
                await session.execute(
                    sa.text(
                        "UPDATE sections SET enrolled = GREATEST(enrolled - 1, 0) "
                        "WHERE id = :secid AND tenant_id = :tid"
                    ),
                    {"secid": str(row["section_id"]), "tid": str(tenant_id)},
                )
                dropped_sections.append(str(row["section_id"]))
        if dropped_sections:
            _log.info(
                "enrollment.superseded_prior_registration",
                student_id=str(student_id),
                dropped_count=len(dropped_sections),
                tenant_id=str(tenant_id),
            )

    if not enrollment_ids:
        if dropped_sections:
            sync_message = await _sync_grad_plan_after_registration(
                session,
                tenant_id=tenant_id,
                student_id=student_id,
                section_ids=section_ids,
            )
            # No new rows, but the registration changed (overlap kept + prior dropped).
            audit_id = await audit_write(
                session,
                tenant_id=tenant_id,
                actor=str(student_id),
                action="enrollment.executed",
                before=None,
                after={
                    "action_id": str(action_id),
                    "section_ids": section_ids,
                    "enrollment_ids": [],
                    "dropped_section_ids": dropped_sections,
                },
            )
            await ActionRepo.set_executed(session, action_id, audit_id)
            message = (
                "Your registration for that term was updated to match the approved plan "
                f"({len(dropped_sections)} course(s) dropped)."
            )
            if sync_message:
                message += f" {sync_message}"
            return ExecuteResult(
                success=True,
                enrollment_ids=[],
                message=message,
            )
        if full_sections:
            return ExecuteResult(
                success=False,
                enrollment_ids=[],
                message=(
                    "Those sections filled up before your approval went through. "
                    "Ask me to find open sections or join the waitlist."
                ),
            )
        return ExecuteResult(success=False, enrollment_ids=[], message="No sections to enroll in.")

    # ONE consolidated confirmation email for the whole registration (same transaction),
    # listing every section just enrolled — not one email per course.
    if enrolled_ctx:
        await outbox_write(
            session,
            tenant_id=tenant_id,
            event_type="enrollment_confirmation",
            payload={
                "student_id": str(student_id),
                "student_name": student_name,
                "action_id": str(action_id),
                "sections": enrolled_ctx,
            },
        )

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
            "dropped_section_ids": dropped_sections,
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
    msg = f"Enrolled in {len(enrollment_ids)} section(s). A confirmation email is on its way."
    if dropped_sections:
        msg += (
            f" This replaced your previous registration for that term "
            f"({len(dropped_sections)} course(s) dropped)."
        )
    if full_sections:
        msg += (
            f" {len(full_sections)} section(s) had filled up and were skipped — "
            "ask me to find open alternatives or join the waitlist."
        )
    sync_message = await _sync_grad_plan_after_registration(
        session,
        tenant_id=tenant_id,
        student_id=student_id,
        section_ids=section_ids,
    )
    if sync_message:
        msg += f" {sync_message}"
    return ExecuteResult(
        success=True,
        enrollment_ids=enrollment_ids,
        message=msg,
    )


async def _sync_grad_plan_after_registration(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    student_id: UUID,
    section_ids: list[str],
) -> str:
    """Best-effort saved graduation-plan sync after a successful write."""
    try:
        from keel.services.grad_plans import sync_after_registration

        result = await sync_after_registration(
            session,
            tenant_id=tenant_id,
            student_id=student_id,
            section_ids=section_ids,
        )
        return result.message if result else ""
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "grad_plan.sync_after_registration_failed",
            student_id=str(student_id),
            tenant_id=str(tenant_id),
            error=str(exc),
        )
        return ""
