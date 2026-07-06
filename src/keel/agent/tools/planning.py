"""Plan generation and graduation-plan tools.

Phase 3 (complete):
  propose_plan   — multi-candidate, risk-scored, workload-banded, LLM-ranked.
  simulate_whatif — engine re-audits a modified assumption; LLM explains.
  load_grad_plan — load the single active saved graduation plan.
  swap_grad_plan_course — edit the active graduation plan with full verification.
  delete_grad_plan — archive the active graduation plan.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from keel.agent.identity import resolve_identity
from keel.logging import get_logger
from keel.services import planning_service

from ._deps import AgentDeps

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Tool input schemas
# ---------------------------------------------------------------------------


class ProposePlanInput(BaseModel):
    """Input for propose_plan tool."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    start_term: str = Field(description="Term to plan for: 'fall', 'spring', or 'summer'.")
    start_year: int = Field(description="Calendar year of the term, e.g. 2026.")
    excluded_days: list[str] = Field(
        default_factory=list,
        description=(
            "Days the student wants NO classes. Use short lowercase names: "
            "'mon','tue','wed','thu','fri'. E.g. ['fri'] to avoid Fridays."
        ),
    )
    min_start_hour: int = Field(
        default=0,
        description=(
            "Earliest class start hour the student accepts (24-h, 0 = no restriction). "
            "E.g. 9 means no classes before 9:00 AM (avoids 8 AM sections)."
        ),
    )


class SimulateWhatIfInput(BaseModel):
    """Input for simulate_whatif tool."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    start_term: str = Field(description="Current term for context: 'fall', 'spring', or 'summer'.")
    start_year: int = Field(description="Calendar year, e.g. 2026.")
    hypothetical_courses: list[str] = Field(
        description=(
            "Course codes to pretend the student has already passed, "
            "e.g. ['CS201', 'MATH201']. Engine re-audits with these added to transcript."
        )
    )


class LoadGradPlanInput(BaseModel):
    """Input for loading the active saved graduation plan."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")


class DeleteGradPlanInput(BaseModel):
    """Input for deleting/clearing the active saved graduation plan."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")


class SwapGradPlanCourseInput(BaseModel):
    """Input for editing the active saved graduation plan."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    remove_code: str = Field(description="Course code to remove, e.g. 'CS201'.")
    add_code: str = Field(description="Course code to add, e.g. 'CS301'.")


class ProposeSectionsInput(BaseModel):
    """Input for propose_sections — build conflict-free schedules for chosen courses."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    course_codes: list[str] = Field(
        description="Course codes the student is registering for, e.g. ['CS340','CS402']."
    )
    term: str = Field(description="Term to register in: 'fall', 'spring', or 'summer'.")
    year: int = Field(description="Calendar year of the term, e.g. 2026.")
    excluded_days: list[str] = Field(
        default_factory=list,
        description="Days with NO classes, lowercase short names: 'mon'..'fri'. E.g. ['fri'].",
    )
    min_start_hour: int = Field(
        default=0,
        description="Earliest acceptable start hour (24-h, 0 = no limit). 9 = no 8 AM sections.",
    )


class PlanGraduationInput(BaseModel):
    """Input for plan_graduation — the FULL term-by-term path to graduation."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    start_term: str = Field(
        description="Term to start the path from: 'fall', 'spring', or 'summer' "
        "(use the student's current term)."
    )
    start_year: int = Field(
        description="Calendar year to start from, e.g. 2026 (use the student's current year)."
    )
    prefer_courses: list[str] = Field(
        default_factory=list,
        description=(
            "Optional elective course codes to PRIORITISE when building the plan — e.g. the "
            "courses you just recommended for a stated career goal ('I want to be an AI "
            "engineer'). Pass them so the graduation plan visibly reflects that goal; the "
            "engine still only schedules eligible, verifier-valid courses."
        ),
    )


# ---------------------------------------------------------------------------
# Section formatting helpers (deterministic — no LLM, no I/O)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def make_planning_tools(deps: AgentDeps) -> list[Any]:
    """Return planning tools closed over deps."""

    @tool(args_schema=ProposePlanInput)
    async def propose_plan(
        student_id: str,
        tenant_id: str,
        start_term: str,
        start_year: int,
        excluded_days: list[str] | None = None,
        min_start_hour: int = 0,
    ) -> str:
        """Build up to 3 feasible, risk-scored, workload-banded course plans.
        Each plan is engine-verified before risk is scored.
        Plans are LLM-ranked by feasibility + risk + workload.
        Use audit_degree first, then propose_plan, then predict_risk to score.
        Only returns plans that pass ALL hard engine constraints.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        return await planning_service.propose_plan(
            session_factory=deps.session_factory,
            llm=deps.llm_agent,
            model_client=deps.model_client,
            student_id=student_id,
            tenant_id=tenant_id,
            start_term=start_term,
            start_year=start_year,
            excluded_days=excluded_days,
            min_start_hour=min_start_hour,
        )

    @tool(args_schema=SimulateWhatIfInput)
    async def simulate_whatif(
        student_id: str,
        tenant_id: str,
        start_term: str,
        start_year: int,
        hypothetical_courses: list[str],
    ) -> str:
        """Simulate what-if: what would the degree audit look like if these courses
        were already completed? Read-only. Never presents an infeasible alternative as feasible.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        return await planning_service.simulate_whatif(
            session_factory=deps.session_factory,
            llm=deps.llm_agent,
            student_id=student_id,
            tenant_id=tenant_id,
            start_term=start_term,
            start_year=start_year,
            hypothetical_courses=hypothetical_courses,
        )

    @tool(args_schema=LoadGradPlanInput)
    async def load_grad_plan(student_id: str, tenant_id: str) -> str:
        """Load the student's active saved graduation plan.

        Use this for "show me my grad plan" / "give me my graduation plan".
        The student does not need to know a plan UUID.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        return await planning_service.load_grad_plan(
            session_factory=deps.session_factory,
            student_id=student_id,
            tenant_id=tenant_id,
        )

    @tool(args_schema=DeleteGradPlanInput)
    async def delete_grad_plan(student_id: str, tenant_id: str) -> str:
        """Delete/clear the active saved graduation plan.

        This archives the saved plan record so audit history remains intact.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        return await planning_service.delete_grad_plan(
            session_factory=deps.session_factory,
            student_id=student_id,
            tenant_id=tenant_id,
        )

    @tool(args_schema=SwapGradPlanCourseInput)
    async def swap_grad_plan_course(
        student_id: str,
        tenant_id: str,
        remove_code: str,
        add_code: str,
    ) -> str:
        """Swap one course in the active saved graduation plan and re-verify it."""
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        return await planning_service.swap_grad_plan_course(
            session_factory=deps.session_factory,
            student_id=student_id,
            tenant_id=tenant_id,
            remove_code=remove_code,
            add_code=add_code,
        )

    @tool(args_schema=ProposeSectionsInput)
    async def propose_sections(
        student_id: str,
        tenant_id: str,
        course_codes: list[str],
        term: str,
        year: int,
        excluded_days: list[str] | None = None,
        min_start_hour: int = 0,
    ) -> str:
        """Build 2-3 ready-to-register SCHEDULES for an already-agreed set of courses.

        Call this ONLY after the student has settled on WHICH courses to take (step 2 of
        registration; step 1 is agreeing on the courses). The engine returns complete,
        conflict-free schedules — one open section per course — that ALL respect the
        student's time preferences (no 8am, no Fridays). It does NOT dump every section;
        it hands you a few clean options.

        You then recommend ONE schedule in a SHORT sentence (the widget shows the options
        as cards and the student picks). To enroll, call stage_enrollment with that
        schedule's section_ids (one per course). Courses with no preference-fitting open
        section are reported separately with the correct remedy — FULL → offer the waitlist;
        NOT OFFERED this term → suggest another term (never a waitlist). Read-only.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        return await planning_service.propose_sections(
            session_factory=deps.session_factory,
            student_id=student_id,
            tenant_id=tenant_id,
            course_codes=course_codes,
            term=term,
            year=year,
            excluded_days=excluded_days,
            min_start_hour=min_start_hour,
        )

    @tool(args_schema=PlanGraduationInput)
    async def plan_graduation(
        student_id: str,
        tenant_id: str,
        start_term: str,
        start_year: int,
        prefer_courses: list[str] | None = None,
    ) -> str:
        """Map the FULL term-by-term path from now to graduation — not just next term.
        Use when the student asks for their whole degree plan, "how many semesters left",
        or "map my path to graduation". The engine builds a verifier-valid multi-term path
        (prerequisite order, offering terms, and credit caps respected end-to-end), and
        this reports each term's courses + workload band, the heaviest term, and the
        projected graduation term. Read-only — proposes a path, writes nothing.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        return await planning_service.plan_graduation(
            session_factory=deps.session_factory,
            llm=deps.llm_agent,
            model_client=deps.model_client,
            student_id=student_id,
            tenant_id=tenant_id,
            start_term=start_term,
            start_year=start_year,
            prefer_courses=prefer_courses,
        )

    return [
        propose_plan,
        plan_graduation,
        propose_sections,
        simulate_whatif,
        load_grad_plan,
        delete_grad_plan,
        swap_grad_plan_course,
    ]


# ---------------------------------------------------------------------------
# LLM helpers (internal)
# ---------------------------------------------------------------------------

