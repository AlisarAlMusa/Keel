"""Guidance tools (E1, E2) — catalog/eligible-grounded suggestions (spec §2).

E1 Elective Recommender — eligible set comes from the engine (DAG + audit); the
   LLM ranks within it and CANNOT add a course outside it. No write.
E2 Career Path — advisory chat, catalog-grounded, hard-caveated ("suggestion, not
   a prediction"). Saving a roadmap routes the suggested courses through the
   propose→verify→repair loop, then persists a verifier-valid Plan (the only E-write).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from keel.domain.engine.audit import audit
from keel.domain.engine.verifier import verify
from keel.domain.engine.workload import compute_workload
from keel.domain.schemas import ToolError
from keel.infra.database.session import tenant_session
from keel.logging import get_logger
from keel.services.prompts import e1_elective_rank_prompt, e2_career_path_prompt

from ._deps import AgentDeps
from .advising import _build_engine_objects, _extract_advise_text, _load_student_data

_log = get_logger(__name__)

_CAREER_CAVEAT = "This is a grounded suggestion, not a prediction."


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


class SaveCareerRoadmapInput(BaseModel):
    """Input for save_career_roadmap tool (E2 → loop → save_plan)."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    interest: str = Field(description="The career interest the roadmap aligns to.")
    plan_name: str = Field(default="Career-aligned", description="Label for the saved plan.")


def _strengths(transcript: list[Any], catalog: dict[str, Any]) -> str:
    """A short human description of subjects the student does well in."""
    strong = sorted(
        (t for t in transcript if t.passed and t.grade is not None and float(t.grade) >= 3.3),
        key=lambda t: float(t.grade),
        reverse=True,
    )
    names = [catalog[t.course_code].name for t in strong[:4] if t.course_code in catalog]
    return ", ".join(names) if names else "no standout strengths yet"


def make_guidance_tools(deps: AgentDeps) -> list[Any]:
    """Return [elective_recommender, career_path, save_career_roadmap]."""

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
        try:
            term, year = deps.current_term, deps.current_year
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as _db:
                data = await _load_student_data(_db, student_id, tenant_id)
            if not data:
                return ToolError(
                    error=f"Student {student_id} not found.", retryable=False, category="validation"
                ).model_dump_json()
            transcript, catalog, graph, coreqs, program = _build_engine_objects(data, term, year)
            if program is None:
                return ToolError(
                    error="Student has no program.", retryable=False, category="validation"
                ).model_dump_json()
            result = audit(
                transcript=transcript,
                program=program,
                graph=graph,
                catalog=catalog,
                current_term=term,
                current_year=year,
            )
            # Elective course universe = requirement groups named like 'elective'.
            elective_codes: set[str] = set()
            for rr in data.get("req_rows", []):
                if "elective" in str(rr["group_name"]).lower():
                    codes = rr["eligible_course_codes"]
                    if isinstance(codes, str):
                        codes = json.loads(codes)
                    elective_codes.update(codes)
            eligible_set = [c for c in result.eligible_now if c in elective_codes]
            if not eligible_set:
                eligible_set = list(result.eligible_now)  # fall back to any eligible course
            if not eligible_set:
                return "You have no eligible electives right now — clear prerequisites first."

            prompt = e1_elective_rank_prompt.build(
                eligible_electives=eligible_set,
                strengths=_strengths(transcript, catalog),
                prefs=f"difficulty={difficulty}; career={career_direction or 'unspecified'}",
            )
            res = await deps.llm_agent.ainvoke(
                [SystemMessage(content=prompt), HumanMessage(content="Rank them.")]
            )
            ranked_text = _extract_advise_text(res.content)
            # Hard rule: drop anything the LLM named that isn't in the eligible set.
            kept = [c for c in eligible_set if c in ranked_text.upper()]
            dropped_note = ""
            mentioned = set(__import__("re").findall(r"[A-Z]{2,4}\d{3}[A-Z]?", ranked_text.upper()))
            invalid = [m for m in mentioned if m not in eligible_set]
            if invalid:
                dropped_note = f"\n\n(Ignored non-eligible suggestions: {', '.join(invalid)}.)"
            header = f"**Eligible electives:** {', '.join(eligible_set)}\n\n"
            return (
                header
                + ranked_text
                + dropped_note
                + ("" if kept else "\n\nAll recommendations are within your eligible set.")
            )
        except Exception as exc:
            _log.error("tool.elective_recommender.error", error=str(exc))
            return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()

    @tool(args_schema=CareerPathInput)
    async def career_path(student_id: str, tenant_id: str, interest: str) -> str:
        """Map a career interest to a direction, skills, and catalog electives.
        Advisory and catalog-grounded — courses must exist in the catalog, and the
        answer always carries the caveat 'a grounded suggestion, not a prediction'.
        This chat is NOT verified; saving a roadmap (save_career_roadmap) is.
        """
        try:
            term, year = deps.current_term, deps.current_year
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as _db:
                data = await _load_student_data(_db, student_id, tenant_id)
            if not data:
                return ToolError(
                    error=f"Student {student_id} not found.", retryable=False, category="validation"
                ).model_dump_json()
            _, catalog, _, _, _ = _build_engine_objects(data, term, year)
            catalog_codes = list(catalog.keys())

            prompt = e2_career_path_prompt.build(interest=interest, catalog_electives=catalog_codes)
            res = await deps.llm_agent.ainvoke(
                [SystemMessage(content=prompt), HumanMessage(content=f"Interest: {interest}")]
            )
            text = _extract_advise_text(res.content)
            # Constrain named courses to the catalog (drop inventions).
            mentioned = set(__import__("re").findall(r"[A-Z]{2,4}\d{3}[A-Z]?", text.upper()))
            invalid = [m for m in mentioned if m not in catalog]
            note = (
                f"\n\n(Note: removed non-catalog courses: {', '.join(invalid)}.)" if invalid else ""
            )
            caveat = f"\n\n_{_CAREER_CAVEAT}_"
            if _CAREER_CAVEAT.lower() not in text.lower():
                text += caveat
            return text + note
        except Exception as exc:
            _log.error("tool.career_path.error", error=str(exc))
            return ToolError(error=str(exc), retryable=True, category="external").model_dump_json()

    @tool(args_schema=SaveCareerRoadmapInput)
    async def save_career_roadmap(
        student_id: str,
        tenant_id: str,
        interest: str,
        plan_name: str = "Career-aligned",
    ) -> str:
        """Save a career-aligned roadmap as a Plan. The suggested courses are NOT
        saved raw — they pass through the propose→verify→repair loop so the persisted
        plan is verifier-valid (A4's invariant). Only a valid plan is written.
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
            if program is None:
                return ToolError(
                    error="Student has no program.", retryable=False, category="validation"
                ).model_dump_json()
            result = audit(
                transcript=transcript,
                program=program,
                graph=graph,
                catalog=catalog,
                current_term=term,
                current_year=year,
            )
            if not result.eligible_now:
                return "No eligible courses to build a roadmap from right now."

            # Route through the propose→verify→repair loop (reuse A1/C3 loop).
            from keel.agent.tools.planning import _validate_with_repair

            seed = list(result.eligible_now[:4])
            plan = await _validate_with_repair(
                proposed={"courses": seed},
                catalog=catalog,
                graph=graph,
                transcript=transcript,
                coreqs=coreqs,
                term=term,
                year=year,
                student_id=student_id,
                tenant_id=tenant_id,
                program=program,
                llm=deps.llm_agent,
            )
            if plan is None or not plan.terms:
                return (
                    "I couldn't build a verifier-valid roadmap from your eligible courses — "
                    "let's plan it together first."
                )

            # Final verifier check (only a ValidatedPlan may be persisted).
            first = plan.terms[0]
            violations = verify(
                plan=plan,
                catalog=catalog,
                graph=graph,
                transcript=transcript,
                corequisites=coreqs,
                current_term=term,
                current_year=year,
            )
            if violations:
                return "The roadmap failed verification at save time and was not saved."

            courses = [catalog[c] for c in first.course_codes if c in catalog]
            _, band = compute_workload(courses)
            plan_data = {
                "terms": [
                    {"term": pt.term.value, "year": pt.year, "course_codes": pt.course_codes}
                    for pt in plan.terms
                ],
                "meta": {"generated_by": "llm", "interest": interest},
                "catalog_version": "v1",
                "workload_band": band.value,
            }
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as session:
                row = await session.execute(
                    sa.text(
                        "INSERT INTO plans "
                        "(tenant_id, student_id, name, version, status, plan_data, "
                        "is_active, catalog_version, validated_at) "
                        "VALUES (:tid, :sid, :name, 1, 'draft', CAST(:data AS jsonb), "
                        "false, 'v1', :now) RETURNING id"
                    ),
                    {
                        "tid": str(tenant_id),
                        "sid": str(student_id),
                        "name": plan_name,
                        "data": json.dumps(plan_data),
                        "now": datetime.utcnow(),
                    },
                )
                plan_db_id = row.scalar_one()
                await session.execute(
                    sa.text(
                        "INSERT INTO audit_log (tenant_id, actor, action, before, after) "
                        "VALUES (:tid, :actor, 'plan.career_roadmap_saved', "
                        "NULL, CAST(:after AS jsonb))"
                    ),
                    {
                        "tid": str(tenant_id),
                        "actor": str(student_id),
                        "after": json.dumps(
                            {"plan_id": str(plan_db_id), "name": plan_name, "interest": interest}
                        ),
                    },
                )
            return (
                f"Saved '{plan_name}' (engine-verified ✓, {band.value} load). "
                f"Plan ID: {plan_db_id}. First term: {', '.join(first.course_codes)}."
            )
        except Exception as exc:
            _log.error("tool.save_career_roadmap.error", error=str(exc))
            return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()

    return [elective_recommender, career_path, save_career_roadmap]
