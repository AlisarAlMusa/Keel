"""Guidance tools (E1, E2) — catalog/eligible-grounded suggestions (spec §2).

E1 Elective Recommender — eligible set comes from the engine (DAG + audit); the
   LLM ranks within it and CANNOT add a course outside it. No write.
E2 Career Path — advisory chat, catalog-grounded, hard-caveated ("suggestion, not
   a prediction"). Persistence now belongs to the single active graduation-plan
   flow, not this guidance tool group.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from keel.agent.identity import resolve_identity
from keel.logging import get_logger
from keel.services import guidance_service

from ._deps import AgentDeps

_log = get_logger(__name__)


class ElectiveRecommenderInput(BaseModel):
    """Input for elective_recommender tool (E1)."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    difficulty: str = Field(
        default="balanced",
        description="Preference: 'easier', 'balanced', or 'challenging'.",
    )
    career_direction: str = Field(
        default="",
        description="Optional career interest to bias ranking, e.g. 'data engineering'.",
    )


class CareerPathInput(BaseModel):
    """Input for career_path tool (E2 advice)."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    interest: str = Field(description="The career interest, e.g. 'AI Engineer'.")


def make_guidance_tools(deps: AgentDeps) -> list[Any]:
    """Return [elective_recommender, career_path]."""

    @tool(args_schema=ElectiveRecommenderInput)
    async def elective_recommender(
        student_id: str,
        tenant_id: str,
        difficulty: str = "balanced",
        career_direction: str = "",
    ) -> str:
        """Recommend electives that FIT the student, ranked by strengths/difficulty/career.
        The eligible elective set comes from the engine (DAG + audit) — any course the
        LLM names outside that set is dropped. No write.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        return await guidance_service.elective_recommender(
            session_factory=deps.session_factory,
            current_term=deps.current_term,
            current_year=deps.current_year,
            llm=deps.llm_agent,
            student_id=student_id,
            tenant_id=tenant_id,
            difficulty=difficulty,
            career_direction=career_direction,
        )

    @tool(args_schema=CareerPathInput)
    async def career_path(student_id: str, tenant_id: str, interest: str) -> str:
        """Map a career interest to a direction, skills, and catalog electives.
        Advisory and catalog-grounded — courses must exist in the catalog, and the
        answer always carries the caveat 'a grounded suggestion, not a prediction'.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        return await guidance_service.career_path(
            session_factory=deps.session_factory,
            current_term=deps.current_term,
            current_year=deps.current_year,
            llm=deps.llm_agent,
            student_id=student_id,
            tenant_id=tenant_id,
            interest=interest,
        )

    return [elective_recommender, career_path]
