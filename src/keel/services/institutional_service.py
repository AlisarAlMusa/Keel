"""Institutional-request STAGE application services (F1-F4, stage side).

Extracted from the institutional agent tools. Each validates, drafts the paperwork,
and stages a pending action; NONE writes. The writes live in
services/actions/institutional.py. Behaviour unchanged.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from keel.agent.llm.advising_llm import _extract_advise_text
from keel.domain.engine.audit import audit
from keel.domain.engine.contracts import Plan, PlanMeta, PlanTerm
from keel.domain.engine.verifier import verify
from keel.domain.models import Term
from keel.domain.schemas import ToolError
from keel.infra.database.session import tenant_session
from keel.logging import get_logger
from keel.mappers.engine_context import _build_engine_objects
from keel.repositories.core import ActionsRepository
from keel.repositories.students import StudentRepository
from keel.services.actions import institutional as inst
from keel.services.advising_service import _compute_switch_impact, _load_program_engine
from keel.services.prompts import f3_petition_draft_prompt, f4_handoff_summary_prompt

_log = get_logger(__name__)


def _staged(action_id: UUID, action_type: str, message: str, **extra: Any) -> str:
    """Build the stage-tool JSON result the graph's stage_node reads for the
    interrupt (must contain ``action_id`` so the approval card surfaces)."""
    return json.dumps(
        {
            "action_id": str(action_id),
            "type": action_type,
            "status": "pending",
            "message": message,
            **extra,
        }
    )


def _transcript_summary(transcript: list[Any], catalog: dict[str, Any]) -> str:
    passed = [t.course_code for t in transcript if t.passed]
    grades = [float(t.grade) for t in transcript if t.grade is not None]
    gpa = sum(grades) / len(grades) if grades else 0.0
    return f"{len(passed)} courses passed ({', '.join(passed[:8])}…), GPA ~{gpa:.2f}"


async def apply_graduation(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    current_term: Term,
    current_year: int,
    student_id: str,
    tenant_id: str,
    thread_id: str,
) -> str:
    """Extracted from the apply_graduation institutional tool (behaviour unchanged)."""
    try:
        term, year = current_term, current_year
        async with tenant_session(session_factory, UUID(tenant_id)) as _db:
            data = await StudentRepository(_db, tenant_id).load_context(student_id)
        if not data:
            return ToolError(
                error=f"Student {student_id} not found.", retryable=False, category="validation"
            ).model_dump_json()
        transcript, catalog, graph, coreqs, program = _build_engine_objects(data, term, year)
        if program is None or not data.get("program_row"):
            return ToolError(
                error="Student has no program.", retryable=False, category="validation"
            ).model_dump_json()
        program_code = str(data["program_row"]["code"])
        result = audit(
            transcript=transcript,
            program=program,
            graph=graph,
            catalog=catalog,
            current_term=term,
            current_year=year,
        )
        ready = not result.remaining_requirements and result.remaining_credits <= 0
        if not ready:
            remaining = [r.requirement_id for r in result.remaining_requirements]
            return (
                f"You're not eligible to apply for graduation yet. Still needed: "
                f"{', '.join(remaining) or 'requirements'} "
                f"({int(result.remaining_credits)} credits remaining). "
                "I can't file a graduation application until these are complete."
            )
        # Stage for approval — nothing is written until the student approves.
        async with tenant_session(session_factory, UUID(tenant_id)) as _db:
            action_id = await ActionsRepository(_db, UUID(tenant_id)).insert_pending(
                student_id=UUID(student_id),
                thread_id=thread_id,
                action_type="graduation",
                payload={"program": program_code},
            )
        return _staged(
            action_id,
            "graduation",
            (
                f"✅ You've met all requirements for {program_code}. I've prepared your "
                "graduation application. Approve it and I'll file it with the registrar's "
                "office — nothing is filed until you do."
            ),
        )
    except Exception as exc:
        _log.error("tool.apply_graduation.error", error=str(exc))
        return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()


async def request_major_change(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    current_term: Term,
    current_year: int,
    student_id: str,
    tenant_id: str,
    thread_id: str,
    target_program_id: Any,
) -> str:
    """Extracted from the request_major_change institutional tool (behaviour unchanged)."""
    try:
        target_program_id = target_program_id.upper()
        term, year = current_term, current_year
        async with tenant_session(session_factory, UUID(tenant_id)) as _db:
            data = await StudentRepository(_db, tenant_id).load_context(student_id)
            if not data:
                return ToolError(
                    error=f"Student {student_id} not found.",
                    retryable=False,
                    category="validation",
                ).model_dump_json()
            transcript, catalog, graph, coreqs, _own = _build_engine_objects(data, term, year)
            target = await _load_program_engine(_db, tenant_id, target_program_id, catalog)
        if target is None:
            return ToolError(
                error=f"Unknown target program '{target_program_id}'.",
                retryable=False,
                category="validation",
            ).model_dump_json()
        target_audit = audit(
            transcript=transcript,
            program=target,
            graph=graph,
            catalog=catalog,
            current_term=term,
            current_year=year,
        )
        impact = _compute_switch_impact(
            transcript=transcript,
            catalog=catalog,
            target_program=target,
            target_audit=target_audit,
            current_term=term,
            current_year=year,
        )
        impact_summary = (
            f"Lost credits: {impact['lost_credits']}; "
            f"new graduation estimate: {impact['new_graduation_term']} "
            f"(~{impact['extra_terms']} extra term(s))."
        )
        # Stage for approval — nothing is written until the student approves.
        async with tenant_session(session_factory, UUID(tenant_id)) as _db:
            action_id = await ActionsRepository(_db, UUID(tenant_id)).insert_pending(
                student_id=UUID(student_id),
                thread_id=thread_id,
                action_type="major_change",
                payload={
                    "target_program_id": target_program_id,
                    "impact_summary": impact_summary,
                },
            )
        return _staged(
            action_id,
            "major_change",
            (
                f"Major-change request to {target_program_id} (prepared)\n{impact_summary}\n\n"
                "This is a routed request the registrar reviews — it is not auto-approved. "
                "Approve it and I'll file it; nothing is filed until you do."
            ),
        )
    except Exception as exc:
        _log.error("tool.request_major_change.error", error=str(exc))
        return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()


async def submit_petition(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    current_term: Term,
    current_year: int,
    llm: Any,
    student_id: str,
    tenant_id: str,
    thread_id: str,
    course_id: Any,
    justification: Any,
) -> str:
    """Extracted from the submit_petition institutional tool (behaviour unchanged)."""
    try:
        course_id = course_id.upper()
        term, year = current_term, current_year
        async with tenant_session(session_factory, UUID(tenant_id)) as _db:
            data = await StudentRepository(_db, tenant_id).load_context(student_id)
        if not data:
            return ToolError(
                error=f"Student {student_id} not found.", retryable=False, category="validation"
            ).model_dump_json()
        transcript, catalog, graph, coreqs, program = _build_engine_objects(data, term, year)
        if course_id not in catalog:
            return ToolError(
                error=f"Unknown course '{course_id}'.", retryable=False, category="validation"
            ).model_dump_json()

        # Engine detects the block: verify a one-course term for this student.
        probe = Plan(
            plan_id=uuid4(),
            tenant_id=UUID(tenant_id),
            student_id=UUID(student_id),
            program_id=program.program_id if program else "",
            name="petition_probe",
            version=1,
            active=False,
            terms=[PlanTerm(term=term, year=year, course_codes=[course_id])],
            meta=PlanMeta(generated_by="manual", created_at=datetime.utcnow()),
        )
        violations = verify(
            plan=probe,
            catalog=catalog,
            graph=graph,
            transcript=transcript,
            corequisites=coreqs,
            current_term=term,
            current_year=year,
        )
        blocking = [v for v in violations if course_id in v.courses]
        if not blocking:
            return (
                f"{course_id} isn't blocked for you — you're eligible to enroll normally, "
                "so no petition is needed. Want me to help you register?"
            )
        block_reason = "; ".join(v.message for v in blocking)

        prompt = f3_petition_draft_prompt.build(
            course_id=course_id,
            block_reason=block_reason,
            justification=justification,
            transcript_summary=_transcript_summary(transcript, catalog),
        )
        res = await llm.ainvoke(
            [SystemMessage(content=prompt), HumanMessage(content="Draft the petition.")]
        )
        draft = _extract_advise_text(res.content)

        # Stage for approval — NEVER write here. The prior code called the service
        # with approved=True, which let the agent (or an injection) file a petition
        # with no approval. Now the student must explicitly approve; the request_queue
        # row (and the registrar's view of it) appears only after approval.
        async with tenant_session(session_factory, UUID(tenant_id)) as _db:
            action_id = await ActionsRepository(_db, UUID(tenant_id)).insert_pending(
                student_id=UUID(student_id),
                thread_id=thread_id,
                action_type="petition",
                payload={
                    "course_id": course_id,
                    "justification": justification,
                    "draft": draft,
                },
            )
        return _staged(
            action_id,
            "petition",
            (
                f"Petition drafted for {course_id} (a request to the registrar, not an "
                f"enrollment):\n\n{draft}\n\nApprove it and I'll file it. The prerequisite "
                "block stays in place until the registrar approves it."
            ),
        )
    except Exception as exc:
        _log.error("tool.submit_petition.error", error=str(exc))
        return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()


async def escalate(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    current_term: Term,
    current_year: int,
    llm: Any,
    student_id: str,
    tenant_id: str,
    thread_id: str,
    reason: Any,
) -> str:
    """Extracted from the escalate institutional tool (behaviour unchanged)."""
    try:
        term, year = current_term, current_year
        async with tenant_session(session_factory, UUID(tenant_id)) as _db:
            data = await StudentRepository(_db, tenant_id).load_context(student_id)
            if not data:
                return ToolError(
                    error=f"Student {student_id} not found.",
                    retryable=False,
                    category="validation",
                ).model_dump_json()
            transcript, catalog, _g, _c, _p = _build_engine_objects(data, term, year)
            program_code = str(data["program_row"]["code"]) if data.get("program_row") else None
            resolved = await inst.resolve_advisor_email(
                _db, tenant_id=UUID(tenant_id), program=program_code
            )
        if resolved is None:
            return (
                "I couldn't find an advisor configured for your program. "
                "Please reach out to the advising office directly."
            )
        advisor_name, advisor_email = resolved

        student_name = str(data["student"].get("student_name") or "Unknown student")
        today = datetime.now(UTC).strftime("%B %d, %Y")
        prompt = f4_handoff_summary_prompt.build(
            recap=reason,
            transcript_summary=_transcript_summary(transcript, catalog),
            failed_constraints="",
            student_name=student_name,
            student_id=student_id,
            today=today,
        )
        res = await llm.ainvoke(
            [SystemMessage(content=prompt), HumanMessage(content="Write the handoff.")]
        )
        handoff = _extract_advise_text(res.content)

        # Stage for approval — the email is sent only after the student approves.
        async with tenant_session(session_factory, UUID(tenant_id)) as _db:
            action_id = await ActionsRepository(_db, UUID(tenant_id)).insert_pending(
                student_id=UUID(student_id),
                thread_id=thread_id,
                action_type="escalate",
                payload={
                    "reason": reason,
                    "program": program_code,
                    "handoff_summary": handoff,
                    "student_name": student_name,
                },
            )
        return _staged(
            action_id,
            "escalate",
            (
                f"I can hand this off to {advisor_name} ({advisor_email}). Draft summary:\n\n"
                f"{handoff}\n\nApprove and I'll send it — nothing is sent until you do."
            ),
        )
    except Exception as exc:
        _log.error("tool.escalate.error", error=str(exc))
        return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()
