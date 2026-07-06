"""Guidance STAGE application services (E1 elective recommender, E2 career path).

Extracted from the guidance agent tools. Read-only, catalog/eligible-grounded.
Behaviour unchanged.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from keel.agent.llm.advising_llm import _extract_advise_text
from keel.domain.engine.audit import audit
from keel.domain.models import Term
from keel.domain.schemas import ToolError
from keel.infra.database.session import tenant_session
from keel.logging import get_logger
from keel.mappers.engine_context import _build_engine_objects
from keel.repositories.students import StudentRepository
from keel.services.prompts import e1_elective_rank_prompt, e2_career_path_prompt

_log = get_logger(__name__)

_CAREER_CAVEAT = "This is a grounded suggestion, not a prediction."


def _strengths(transcript: list[Any], catalog: dict[str, Any]) -> str:
    """A short human description of subjects the student does well in."""
    strong = sorted(
        (t for t in transcript if t.passed and t.grade is not None and float(t.grade) >= 3.3),
        key=lambda t: float(t.grade),
        reverse=True,
    )
    names = [catalog[t.course_code].name for t in strong[:4] if t.course_code in catalog]
    return ", ".join(names) if names else "no standout strengths yet"


async def elective_recommender(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    current_term: Term,
    current_year: int,
    llm: Any,
    student_id: str,
    tenant_id: str,
    difficulty: Any,
    career_direction: Any,
) -> str:
    """Extracted from the elective_recommender guidance tool (behaviour unchanged)."""
    try:
        term, year = current_term, current_year
        async with tenant_session(session_factory, UUID(tenant_id)) as _db:
            data = await StudentRepository(_db, tenant_id).load_context(student_id)
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

        # Pass CODE — Name (not bare codes) so the model can recognise what each course
        # IS (e.g. CS360 = Introduction to Artificial Intelligence) and rank by real
        # relevance to the stated career — otherwise it ranks opaque codes by guesswork.
        elig_display = [f"{c} — {catalog[c].name}" if c in catalog else c for c in eligible_set]
        prompt = e1_elective_rank_prompt.build(
            eligible_electives=elig_display,
            strengths=_strengths(transcript, catalog),
            prefs=f"difficulty={difficulty}; career={career_direction or 'unspecified'}",
        )
        res = await llm.ainvoke([SystemMessage(content=prompt), HumanMessage(content="Rank them.")])
        ranked_text = _extract_advise_text(res.content)
        # Hard rule: drop anything the LLM named that isn't in the eligible set.
        kept = [c for c in eligible_set if c in ranked_text.upper()]
        dropped_note = ""
        mentioned = set(__import__("re").findall(r"[A-Z]{2,4}\d{3}[A-Z]?", ranked_text.upper()))
        invalid = [m for m in mentioned if m not in eligible_set]
        if invalid:
            dropped_note = f"\n\n(Ignored non-eligible suggestions: {', '.join(invalid)}.)"
        header = "**Eligible electives:** " + ", ".join(elig_display) + "\n\n"
        return (
            header
            + ranked_text
            + dropped_note
            + ("" if kept else "\n\nAll recommendations are within your eligible set.")
        )
    except Exception as exc:
        _log.error("tool.elective_recommender.error", error=str(exc))
        return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()


async def career_path(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    current_term: Term,
    current_year: int,
    llm: Any,
    student_id: str,
    tenant_id: str,
    interest: Any,
) -> str:
    """Extracted from the career_path guidance tool (behaviour unchanged)."""
    try:
        term, year = current_term, current_year
        async with tenant_session(session_factory, UUID(tenant_id)) as _db:
            data = await StudentRepository(_db, tenant_id).load_context(student_id)
        if not data:
            return ToolError(
                error=f"Student {student_id} not found.", retryable=False, category="validation"
            ).model_dump_json()
        _, catalog, _, _, _ = _build_engine_objects(data, term, year)
        # CODE — Name so the model recognises AI-relevant courses by what they are
        # (CS360 = Introduction to Artificial Intelligence), not opaque codes.
        catalog_codes = [f"{c.code} — {c.name}" for c in catalog.values()]

        prompt = e2_career_path_prompt.build(interest=interest, catalog_electives=catalog_codes)
        res = await llm.ainvoke(
            [SystemMessage(content=prompt), HumanMessage(content=f"Interest: {interest}")]
        )
        text = _extract_advise_text(res.content)
        # Constrain named courses to the catalog (drop inventions).
        mentioned = set(__import__("re").findall(r"[A-Z]{2,4}\d{3}[A-Z]?", text.upper()))
        invalid = [m for m in mentioned if m not in catalog]
        note = f"\n\n(Note: removed non-catalog courses: {', '.join(invalid)}.)" if invalid else ""
        caveat = f"\n\n_{_CAREER_CAVEAT}_"
        if _CAREER_CAVEAT.lower() not in text.lower():
            text += caveat
        # After narrating the career direction, nudge the agent to turn it into action:
        # offer to fold these courses into the student's graduation plan toward the goal.
        followup = (
            "\n\n[next step: after presenting this, OFFER to update the student's "
            f"graduation plan toward '{interest}' — ask if they want you to build/refresh "
            "a grad plan built around these recommended courses. If yes, call "
            "plan_graduation AND pass prefer_courses=[the recommended course codes above] "
            "so the plan actually prioritises them; the student saves it from the plan "
            "card. Ask once; do not assume.]"
        )
        return text + note + followup
    except Exception as exc:
        _log.error("tool.career_path.error", error=str(exc))
        return ToolError(error=str(exc), retryable=True, category="external").model_dump_json()
