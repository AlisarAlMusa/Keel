"""Read-only advising tools (audit_degree, rag_search, predict_risk, gpa_estimate,
my_info + the C1–C4 chat tools). Each tool is a thin adapter that resolves identity
and delegates to ``keel.services.advising_service``.

``_load_student_data`` and ``_build_engine_objects`` are retained here only as
backward-compatible names that the frozen write-safety tests patch; the real
implementations now live in the repository and mapper layers.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from keel.agent.identity import resolve_identity, resolve_tenant
from keel.logging import get_logger

# Re-exported for backward compatibility: the row->engine-object mapper now lives in
# keel.mappers, but callers/tests still reference keel.agent.tools.advising._build_engine_objects.
from keel.mappers.engine_context import _build_engine_objects  # noqa: F401
from keel.repositories.students import StudentRepository
from keel.services import advising_service

from ._deps import AgentDeps

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Tool input schemas
# ---------------------------------------------------------------------------


class AuditDegreeInput(BaseModel):
    """Input for audit_degree tool."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")


class RagSearchInput(BaseModel):
    """Input for rag_search tool."""

    query: str = Field(
        description=(
            "The specific topic to search for — e.g. 'CS301 prerequisites', "
            "'late withdrawal policy', 'BSCS degree requirements'. "
            "Be specific: use the course code or exact policy name."
        )
    )
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")


class PredictRiskInput(BaseModel):
    """Input for predict_risk tool."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    start_term: str = Field(description="Term to score: 'fall', 'spring', or 'summer'.")
    start_year: int = Field(description="Calendar year of the term, e.g. 2026.")
    course_codes: list[str] = Field(
        description=(
            "List of course codes in the proposed term to score for risk, "
            "e.g. ['CS201', 'CS210', 'CS301']. Use codes from propose_plan output."
        )
    )


class GpaEstimateInput(BaseModel):
    """Input for gpa_estimate tool."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    course_codes: list[str] = Field(
        description="Course codes to estimate GPA impact for, e.g. ['CS201', 'CS210']."
    )


class CourseAdvisorInput(BaseModel):
    """Input for course_advisor tool (C1)."""

    query: str = Field(
        description="The course question, e.g. 'What does CS301 cover and require?'."
    )
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    student_id: str = Field(description="The student's UUID — copy from the system prompt.")


class DegreeAuditChatInput(BaseModel):
    """Input for degree_audit_chat tool (C2)."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")


class FailureRecoveryInput(BaseModel):
    """Input for failure_recovery tool (C3)."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    failed_course: str = Field(description="Code of the course the student failed, e.g. 'CS102'.")


class MajorSwitchAdviceInput(BaseModel):
    """Input for major_switch_advice tool (C4)."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    target_program: str = Field(
        description="Code of the program the student is considering, e.g. 'BSDS'."
    )


class MyInfoInput(BaseModel):
    """Input for my_info tool — the student's own account facts."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")


# ---------------------------------------------------------------------------
# Backward-compatible shim retained for the frozen write-safety tests
# ---------------------------------------------------------------------------


async def _load_student_data(
    session: AsyncSession,
    student_id: str,
    tenant_id: str,
) -> dict[str, Any]:
    """Load transcript, program, and catalog for one student.

    Thin adapter over ``StudentRepository.load_context`` (the SQL lives in the
    repository layer per CLAUDE.md §5). Production code calls the repository
    directly; this name is retained only because the write-safety tests patch
    ``keel.agent.tools.advising._load_student_data``.
    """
    return await StudentRepository(session, UUID(tenant_id)).load_context(student_id)


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def make_advising_tools(deps: AgentDeps) -> list[Any]:
    """Return [audit_degree, rag_search, predict_risk, gpa_estimate] closed over deps."""

    @tool(args_schema=AuditDegreeInput)
    async def audit_degree(student_id: str, tenant_id: str) -> str:
        """Show the student's degree progress: completed requirements,
        remaining credits, and courses eligible to take right now.
        Use this before proposing any plan.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        return await advising_service.audit_degree(
            session_factory=deps.session_factory,
            current_term=deps.current_term,
            current_year=deps.current_year,
            student_id=student_id,
            tenant_id=tenant_id,
        )

    @tool(args_schema=RagSearchInput)
    async def rag_search(query: str, tenant_id: str) -> str:
        """Search the university's course catalog and policy documents.
        Use for: course descriptions, prerequisites, policies, deadlines,
        degree requirements, and any factual university information.
        Academic answers must be grounded in a rag_search result.
        """
        tenant_id = resolve_tenant(tenant_id)
        return await advising_service.rag_search(
            session_factory=deps.session_factory,
            cohere_client=deps.cohere_client,
            settings=deps.settings,
            tenant_id=tenant_id,
            query=query,
        )

    @tool(args_schema=PredictRiskInput)
    async def predict_risk(
        student_id: str,
        tenant_id: str,
        start_term: str,
        start_year: int,
        course_codes: list[str],
    ) -> str:
        """Predict graduation risk for a student given a proposed course load.
        Returns on_track / at_risk label with a confidence score, deterministic
        reasons derived from feature values, and an LLM-generated mitigation plan.
        Features are NEVER LLM-computed — they come from the engine + shared compute_features.
        Use after propose_plan to score each candidate plan.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        return await advising_service.predict_risk(
            session_factory=deps.session_factory,
            model_client=deps.model_client,
            llm=deps.llm_agent,
            student_id=student_id,
            tenant_id=tenant_id,
            start_term=start_term,
            start_year=start_year,
            course_codes=course_codes,
        )

    @tool(args_schema=GpaEstimateInput)
    async def gpa_estimate(student_id: str, tenant_id: str, course_codes: list[str]) -> str:
        """Produce an LLM-based GPA estimate for a proposed course load.
        This is an estimate only — NOT a prediction. Always hard-caveated.
        Never present this as a guarantee or use it to gate feasibility.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        return await advising_service.gpa_estimate(
            session_factory=deps.session_factory,
            llm=deps.llm_agent,
            student_id=student_id,
            tenant_id=tenant_id,
            course_codes=course_codes,
        )

    @tool(args_schema=MyInfoInput)
    async def my_info(student_id: str, tenant_id: str) -> str:
        """Return the student's own account facts — major/program, GPA, completed credits,
        current term, courses failed, and any holds. Use for 'what's my major',
        'what's my GPA', 'do I have a hold', 'what's my standing', 'what's my info'.
        Pure DB lookup — reads the authoritative student record (reflects an approved
        major change). Read-only.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        return await advising_service.my_info(
            session_factory=deps.session_factory,
            student_id=student_id,
            tenant_id=tenant_id,
        )

    return [audit_degree, rag_search, predict_risk, gpa_estimate, my_info]


# ---------------------------------------------------------------------------
# Advising chat tools (C1–C4) — read-only, engine numbers + grounded narration
# ---------------------------------------------------------------------------

import re  # noqa: E402

_COURSE_CODE_RE = re.compile(r"[A-Z]{2,4}\d{3}[A-Z]?")


def make_advising_chat_tools(deps: AgentDeps) -> list[Any]:
    """Return [course_advisor, degree_audit_chat, failure_recovery, major_switch_advice].

    All four are READ-ONLY (spec §1): the engine owns every number; the LLM only
    narrates; nothing is written.
    """

    @tool(args_schema=CourseAdvisorInput)
    async def course_advisor(query: str, tenant_id: str, student_id: str) -> str:
        """Answer a question about a course — what it covers, unlocks, or requires —
        grounded in the catalog (RAG). Prerequisite facts are injected from the engine
        DAG, never from prose. Use for 'what does CS301 cover/require?' style questions.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        return await advising_service.course_advisor(
            session_factory=deps.session_factory,
            cohere_client=deps.cohere_client,
            settings=deps.settings,
            llm=deps.llm_agent,
            student_id=student_id,
            tenant_id=tenant_id,
            query=query,
        )

    @tool(args_schema=DegreeAuditChatInput)
    async def degree_audit_chat(student_id: str, tenant_id: str) -> str:
        """Explain in plain language what the student still needs to graduate.
        The engine computes every number (missing requirements, remaining credits,
        eligible courses); the LLM only restates them verbatim — never recomputed.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        return await advising_service.degree_audit_chat(
            session_factory=deps.session_factory,
            current_term=deps.current_term,
            current_year=deps.current_year,
            llm=deps.llm_agent,
            student_id=student_id,
            tenant_id=tenant_id,
        )

    @tool(args_schema=FailureRecoveryInput)
    async def failure_recovery(student_id: str, tenant_id: str, failed_course: str) -> str:
        """Build a concrete recovery plan after a student fails a course. The engine
        computes the downstream impact and rebuilds the eligible pool from the updated
        transcript; the recovery plan is produced by the SAME propose→verify→repair loop
        as propose_plan (failure baked into the audit) and is verifier-valid. No write.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        return await advising_service.failure_recovery(
            session_factory=deps.session_factory,
            current_term=deps.current_term,
            current_year=deps.current_year,
            llm=deps.llm_agent,
            student_id=student_id,
            tenant_id=tenant_id,
            failed_course=failed_course,
        )

    @tool(args_schema=MajorSwitchAdviceInput)
    async def major_switch_advice(student_id: str, tenant_id: str, target_program: str) -> str:
        """Advise on switching majors. The engine computes the consequences (lost
        credits, new timeline, delayed courses) against the target program; the LLM
        frames an explicitly ADVISORY recommendation — never a guarantee. No write.
        The action to actually switch is request_major_change (F2).
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        return await advising_service.major_switch_advice(
            session_factory=deps.session_factory,
            current_term=deps.current_term,
            current_year=deps.current_year,
            llm=deps.llm_agent,
            student_id=student_id,
            tenant_id=tenant_id,
            target_program=target_program,
        )

    return [course_advisor, degree_audit_chat, failure_recovery, major_switch_advice]

