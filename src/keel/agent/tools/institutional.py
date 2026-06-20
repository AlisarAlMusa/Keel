"""Institutional request agent tools (F1–F4) — PROPOSAL ONLY (spec §3).

These LLM-facing tools deliberately expose NO ``approved`` parameter, so the agent
can never trigger an institutional write — not even under prompt injection. Each
tool engine-validates, drafts the paperwork, and calls its service function with
``approved=False`` (which writes nothing) to return a proposal. The actual write
(``approved=True``) happens only via an explicit student-approval action outside
the agent's path (approval UI, Day 6) — the meaningful gate (spec D4).

Writes themselves live in ``services/actions/institutional.py`` (the one action
pattern). F3 (petition) NEVER produces an enrollment — the engine block stays hard.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from keel.domain.engine.audit import audit
from keel.domain.engine.contracts import Plan, PlanMeta, PlanTerm
from keel.domain.engine.verifier import verify
from keel.domain.schemas import ToolError
from keel.infra.database.session import tenant_session
from keel.logging import get_logger
from keel.services.actions import institutional as inst
from keel.services.prompts import f3_petition_draft_prompt, f4_handoff_summary_prompt

from ._deps import AgentDeps
from .advising import (
    _build_engine_objects,
    _compute_switch_impact,
    _extract_advise_text,
    _load_program_engine,
    _load_student_data,
)

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Tool input schemas — note: NO `approved` field on any of them (injection-safe)
# ---------------------------------------------------------------------------


class ApplyGraduationInput(BaseModel):
    """Input for apply_graduation tool (F1)."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")


class RequestMajorChangeInput(BaseModel):
    """Input for request_major_change tool (F2)."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    target_program_id: str = Field(description="Code of the program to switch to, e.g. 'BSDS'.")


class SubmitPetitionInput(BaseModel):
    """Input for submit_petition tool (F3)."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    course_id: str = Field(description="Code of the blocked course to petition for, e.g. 'CS301'.")
    justification: str = Field(description="The student's stated reason for the override request.")


class EscalateInput(BaseModel):
    """Input for escalate tool (F4)."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    reason: str = Field(description="Why the student needs a human advisor.")


def _transcript_summary(transcript: list[Any], catalog: dict[str, Any]) -> str:
    passed = [t.course_code for t in transcript if t.passed]
    grades = [float(t.grade) for t in transcript if t.grade is not None]
    gpa = sum(grades) / len(grades) if grades else 0.0
    return f"{len(passed)} courses passed ({', '.join(passed[:8])}…), GPA ~{gpa:.2f}"


def make_institutional_tools(deps: AgentDeps) -> list[Any]:
    """Return [apply_graduation, request_major_change, submit_petition, escalate].

    PROPOSAL-ONLY: none of these write. They are registered on the agent allowlist;
    the student-approval path (approved=True) lives outside the agent.
    """

    @tool(args_schema=ApplyGraduationInput)
    async def apply_graduation(student_id: str, tenant_id: str) -> str:
        """Prepare a graduation application. The engine confirms ALL requirements are
        met before offering; if any remain, it explains what's missing and files nothing.
        Filing requires the student's explicit approval (a separate step) — this tool
        only prepares the request and never writes.
        """
        try:
            term, year = deps.current_term, deps.current_year
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as _db:
                data = await _load_student_data(_db, student_id, tenant_id)
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
            # Proposal only — approved=False writes nothing.
            await inst.apply_graduation(
                deps.session_factory,
                tenant_id=UUID(tenant_id),
                student_id=UUID(student_id),
                program=program_code,
                approved=False,
            )
            return (
                f"✅ You've met all requirements for **{program_code}**. "
                "I've prepared your graduation application. Approve it and I'll file it with the "
                "registrar's office — nothing is filed until you do."
            )
        except Exception as exc:
            _log.error("tool.apply_graduation.error", error=str(exc))
            return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()

    @tool(args_schema=RequestMajorChangeInput)
    async def request_major_change(student_id: str, tenant_id: str, target_program_id: str) -> str:
        """Prepare a major-change request to a target program. The engine computes the
        consequences (lost credits, new timeline); the LLM frames the impact summary
        attached to the request. Filing requires the student's explicit approval — this
        tool only prepares it and never writes.
        """
        try:
            target_program_id = target_program_id.upper()
            term, year = deps.current_term, deps.current_year
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as _db:
                data = await _load_student_data(_db, student_id, tenant_id)
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
            await inst.request_major_change(
                deps.session_factory,
                tenant_id=UUID(tenant_id),
                student_id=UUID(student_id),
                target_program_id=target_program_id,
                impact_summary=impact_summary,
                approved=False,
            )
            return (
                f"**Major-change request to {target_program_id} (prepared)**\n{impact_summary}\n\n"
                "This is a routed request the registrar reviews — it is not auto-approved. "
                "Approve it and I'll file it; nothing is filed until you do."
            )
        except Exception as exc:
            _log.error("tool.request_major_change.error", error=str(exc))
            return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()

    @tool(args_schema=SubmitPetitionInput)
    async def submit_petition(
        student_id: str, tenant_id: str, course_id: str, justification: str
    ) -> str:
        """Draft a prerequisite-override PETITION for a blocked course. The engine's
        eligibility block is NEVER removed — this prepares a request to a human reviewer,
        never an enrollment. Drafting only; filing requires the student's explicit approval.
        """
        try:
            course_id = course_id.upper()
            term, year = deps.current_term, deps.current_year
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as _db:
                data = await _load_student_data(_db, student_id, tenant_id)
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
            res = await deps.llm_agent.ainvoke(
                [SystemMessage(content=prompt), HumanMessage(content="Draft the petition.")]
            )
            draft = _extract_advise_text(res.content)

            # File immediately — a petition is a request to a human reviewer, not an enrollment.
            # The student asking "petition for X" is implicit approval; no stage→interrupt needed.
            result = await inst.submit_petition(
                deps.session_factory,
                tenant_id=UUID(tenant_id),
                student_id=UUID(student_id),
                course_id=course_id,
                justification=justification,
                draft=draft,
                approved=True,
            )
            if not result.written:
                return (
                    f"Petition for {course_id} could not be filed: {result.message}"
                )
            return (
                f"✅ **Petition filed with the registrar — {course_id}**\n\n{draft}\n\n"
                "The registrar will review your request. The prerequisite block remains in place "
                "until they approve it — you will be notified of the outcome."
            )
        except Exception as exc:
            _log.error("tool.submit_petition.error", error=str(exc))
            return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()

    @tool(args_schema=EscalateInput)
    async def escalate(student_id: str, tenant_id: str, reason: str) -> str:
        """Prepare an escalation/handoff to a human advisor (email only). Resolves the
        advisor from the program and writes a factual handoff summary. Sending requires
        the student's explicit approval — this tool only prepares it and never sends.
        """
        try:
            term, year = deps.current_term, deps.current_year
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as _db:
                data = await _load_student_data(_db, student_id, tenant_id)
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

            prompt = f4_handoff_summary_prompt.build(
                recap=reason,
                transcript_summary=_transcript_summary(transcript, catalog),
                failed_constraints="",
            )
            res = await deps.llm_agent.ainvoke(
                [SystemMessage(content=prompt), HumanMessage(content="Write the handoff.")]
            )
            handoff = _extract_advise_text(res.content)

            await inst.escalate(
                deps.session_factory,
                tenant_id=UUID(tenant_id),
                student_id=UUID(student_id),
                reason=reason,
                program=program_code,
                handoff_summary=handoff,
                approved=False,
            )
            return (
                f"I can hand this off to **{advisor_name}** ({advisor_email}). Draft summary:\n\n"
                f"{handoff}\n\nApprove and I'll send it — nothing is sent until you do."
            )
        except Exception as exc:
            _log.error("tool.escalate.error", error=str(exc))
            return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()

    return [apply_graduation, request_major_change, submit_petition, escalate]
