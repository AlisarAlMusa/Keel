"""Institutional request agent tools (F1–F4) — STAGE ONLY (spec §3 / §8).

These LLM-facing tools deliberately expose NO ``approved`` parameter, and they
NEVER call the institutional write services directly. Each tool engine-validates,
drafts the paperwork, then **stages a pending action** (frozen payload) and the
graph interrupts for explicit student approval — the same gated pattern as
enrollment. The actual write (``approved=True``) happens only inside
``execute_node`` after the student approves via ``POST /actions/{id}/approve``.

This closes the prior F3 hole where ``submit_petition`` called the service with
``approved=True`` directly, letting the agent (or an injection) file a request
with no approval. Now every institutional filing is gated; petitions appear in the
registrar's queue only after the student approves.

Writes themselves live in ``services/actions/institutional.py`` (the one action
pattern). F3 (petition) NEVER produces an enrollment — the engine block stays hard.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from keel.agent.identity import resolve_identity, resolve_thread_id
from keel.logging import get_logger
from keel.services import institutional_service

from ._deps import AgentDeps

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Tool input schemas — note: NO `approved` field on any of them (injection-safe)
# ---------------------------------------------------------------------------


class ApplyGraduationInput(BaseModel):
    """Input for apply_graduation tool (F1)."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    thread_id: str = Field(
        description="LangGraph thread ID for this conversation — used to resume after approval."
    )


class RequestMajorChangeInput(BaseModel):
    """Input for request_major_change tool (F2)."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    target_program_id: str = Field(description="Code of the program to switch to, e.g. 'BSDS'.")
    thread_id: str = Field(
        description="LangGraph thread ID for this conversation — used to resume after approval."
    )


class SubmitPetitionInput(BaseModel):
    """Input for submit_petition tool (F3)."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    course_id: str = Field(description="Code of the blocked course to petition for, e.g. 'CS301'.")
    justification: str = Field(description="The student's stated reason for the override request.")
    thread_id: str = Field(
        description="LangGraph thread ID for this conversation — used to resume after approval."
    )


class EscalateInput(BaseModel):
    """Input for escalate tool (F4)."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    reason: str = Field(description="Why the student needs a human advisor.")
    thread_id: str = Field(
        description="LangGraph thread ID for this conversation — used to resume after approval."
    )


def make_institutional_tools(deps: AgentDeps) -> list[Any]:
    """Return [apply_graduation, request_major_change, submit_petition, escalate].

    PROPOSAL-ONLY: none of these write. They are registered on the agent allowlist;
    the student-approval path (approved=True) lives outside the agent.
    """

    @tool(args_schema=ApplyGraduationInput)
    async def apply_graduation(student_id: str, tenant_id: str, thread_id: str) -> str:
        """Prepare a graduation application. The engine confirms ALL requirements are
        met before offering; if any remain, it explains what's missing and files nothing.
        Filing requires the student's explicit approval (a separate step) — this tool
        only stages the request and never writes.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        thread_id = resolve_thread_id(thread_id)
        return await institutional_service.apply_graduation(
            session_factory=deps.session_factory,
            current_term=deps.current_term,
            current_year=deps.current_year,
            student_id=student_id,
            tenant_id=tenant_id,
            thread_id=thread_id,
        )

    @tool(args_schema=RequestMajorChangeInput)
    async def request_major_change(
        student_id: str, tenant_id: str, target_program_id: str, thread_id: str
    ) -> str:
        """Prepare a major-change request to a target program. The engine computes the
        consequences (lost credits, new timeline); the LLM frames the impact summary
        attached to the request. Filing requires the student's explicit approval — this
        tool only stages it and never writes.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        thread_id = resolve_thread_id(thread_id)
        return await institutional_service.request_major_change(
            session_factory=deps.session_factory,
            current_term=deps.current_term,
            current_year=deps.current_year,
            student_id=student_id,
            tenant_id=tenant_id,
            thread_id=thread_id,
            target_program_id=target_program_id,
        )

    @tool(args_schema=SubmitPetitionInput)
    async def submit_petition(
        student_id: str, tenant_id: str, course_id: str, justification: str, thread_id: str
    ) -> str:
        """Draft a prerequisite-override PETITION for a blocked course. The engine's
        eligibility block is NEVER removed — this prepares a request to a human reviewer,
        never an enrollment. Drafting only; filing requires the student's explicit approval.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        thread_id = resolve_thread_id(thread_id)
        return await institutional_service.submit_petition(
            session_factory=deps.session_factory,
            current_term=deps.current_term,
            current_year=deps.current_year,
            llm=deps.llm_agent,
            student_id=student_id,
            tenant_id=tenant_id,
            thread_id=thread_id,
            course_id=course_id,
            justification=justification,
        )

    @tool(args_schema=EscalateInput)
    async def escalate(student_id: str, tenant_id: str, reason: str, thread_id: str) -> str:
        """Prepare an escalation/handoff to a human advisor (email only). Resolves the
        advisor from the program and writes a factual handoff summary. Sending requires
        the student's explicit approval — this tool only stages it and never sends.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        thread_id = resolve_thread_id(thread_id)
        return await institutional_service.escalate(
            session_factory=deps.session_factory,
            current_term=deps.current_term,
            current_year=deps.current_year,
            llm=deps.llm_agent,
            student_id=student_id,
            tenant_id=tenant_id,
            thread_id=thread_id,
            reason=reason,
        )

    return [apply_graduation, request_major_change, submit_petition, escalate]
