"""Read-only advising tools: audit_degree, rag_search, predict_risk, gpa_estimate.

Also owns the shared DB helpers (_load_student_data, _build_engine_objects)
used by planning tools — they import from here to avoid duplication.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from keel.domain.engine.audit import audit
from keel.domain.models import Corequisite, Course, Prerequisite, Term, TranscriptEntry
from keel.domain.schemas import ToolError
from keel.infra.database.session import tenant_session
from keel.infra.rag import retrieve
from keel.logging import get_logger

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


# ---------------------------------------------------------------------------
# Shared DB query helpers (also imported by planning.py)
# ---------------------------------------------------------------------------


async def _load_student_data(
    session: AsyncSession,
    student_id: str,
    tenant_id: str,
) -> dict[str, Any]:
    """Load transcript, program, and catalog for one student."""
    row = await session.execute(
        sa.text("""
            SELECT s.program_id, s.has_hold, s.hold_reason
            FROM students s
            WHERE s.id = :sid AND s.tenant_id = :tid
        """),
        {"sid": student_id, "tid": tenant_id},
    )
    student = row.mappings().first()
    if student is None:
        return {}

    # Transcript (table is student_transcript per migration 0001)
    tx = await session.execute(
        sa.text("""
            SELECT course_code, grade, passed, term, year
            FROM student_transcript
            WHERE student_id = :sid AND tenant_id = :tid
        """),
        {"sid": student_id, "tid": tenant_id},
    )
    transcript_rows = tx.mappings().all()

    # Courses (difficulty exists in 0001; capacity is on sections, not courses)
    cr = await session.execute(
        sa.text("""
            SELECT c.code, c.name, c.credits, c.offered_terms, c.difficulty
            FROM courses c
            WHERE c.tenant_id = :tid
        """),
        {"tid": tenant_id},
    )
    course_rows = cr.mappings().all()

    # Prereqs (column is requires_code per migration 0001)
    pr = await session.execute(
        sa.text("""
            SELECT course_code, requires_code
            FROM prerequisites
            WHERE tenant_id = :tid
        """),
        {"tid": tenant_id},
    )
    prereq_rows = pr.mappings().all()

    # Coreqs
    coq = await session.execute(
        sa.text("""
            SELECT course_code, coreq_code
            FROM corequisites
            WHERE tenant_id = :tid
        """),
        {"tid": tenant_id},
    )
    coreq_rows = coq.mappings().all()

    # Program + requirements
    prog_row = None
    req_rows: list[Any] = []
    if student["program_id"]:
        p = await session.execute(
            sa.text(
                "SELECT id, code, name, total_credits_required, tenant_id "
                "FROM programs WHERE id = :pid AND tenant_id = :tid"
            ),
            {"pid": student["program_id"], "tid": tenant_id},
        )
        prog_row = p.mappings().first()
        if prog_row:
            rq = await session.execute(
                sa.text(
                    "SELECT group_name, required_credits, eligible_course_codes "
                    "FROM program_requirements "
                    "WHERE program_id = :pid AND tenant_id = :tid"
                ),
                {"pid": student["program_id"], "tid": tenant_id},
            )
            req_rows = list(rq.mappings().all())

    return {
        "student": dict(student),
        "student_id_str": student_id,
        "tenant_id_str": tenant_id,
        "transcript_rows": [dict(r) for r in transcript_rows],
        "course_rows": [dict(r) for r in course_rows],
        "prereq_rows": [dict(r) for r in prereq_rows],
        "coreq_rows": [dict(r) for r in coreq_rows],
        "program_row": dict(prog_row) if prog_row else None,
        "req_rows": [dict(r) for r in req_rows],
    }


def _build_engine_objects(
    data: dict[str, Any],
    current_term: Term,
    current_year: int,
) -> tuple[
    list[TranscriptEntry],
    dict[str, Course],
    Any,  # PrereqGraph
    list[Corequisite],
    Any,  # Program | None
]:
    """Convert raw DB rows into typed engine objects."""
    from decimal import Decimal

    from keel.domain.engine.contracts import CoreRequirement, Program
    from keel.domain.engine.graph import PrereqGraph

    tid_uuid = UUID(data["tenant_id_str"])
    sid_uuid = UUID(data["student_id_str"])

    catalog: dict[str, Course] = {}
    for r in data.get("course_rows", []):
        offered = r.get("offered_terms") or ["fall", "spring"]
        if isinstance(offered, str):
            offered = json.loads(offered)
        catalog[r["code"]] = Course(
            tenant_id=tid_uuid,
            code=r["code"],
            name=r["name"],
            credits=int(r.get("credits", 3)),
            difficulty=int(r.get("difficulty", 3)),
            offered_terms=frozenset(Term(t) for t in offered),
        )

    transcript: list[TranscriptEntry] = []
    for r in data.get("transcript_rows", []):
        try:
            term = Term(r["term"]) if r.get("term") else current_term
        except ValueError:
            term = current_term
        transcript.append(
            TranscriptEntry(
                tenant_id=tid_uuid,
                student_id=sid_uuid,
                course_code=r["course_code"],
                grade=Decimal(str(r["grade"])) if r.get("grade") is not None else None,
                passed=bool(r.get("passed", False)),
                term=term,
                year=int(r.get("year") or current_year),
            )
        )

    prerequisites = [
        Prerequisite(
            tenant_id=tid_uuid,
            course_code=r["course_code"],
            requires_code=r["requires_code"],
        )
        for r in data.get("prereq_rows", [])
    ]
    all_codes = frozenset(catalog.keys())
    graph = PrereqGraph(prerequisites=prerequisites, all_course_codes=all_codes)

    coreqs: list[Corequisite] = [
        Corequisite(tenant_id=tid_uuid, course_code=r["course_code"], coreq_code=r["coreq_code"])
        for r in data.get("coreq_rows", [])
    ]

    program: Any = None
    pr = data.get("program_row")
    if pr:
        reqs = []
        for rr in data.get("req_rows", []):
            codes = rr["eligible_course_codes"]
            if isinstance(codes, str):
                codes = json.loads(codes)
            reqs.append(
                CoreRequirement(
                    type="CORE",
                    requirement_id=rr["group_name"],
                    courses=list(codes),
                )
            )
        if not reqs:
            reqs = [
                CoreRequirement(
                    type="CORE",
                    requirement_id="all_courses",
                    courses=list(catalog.keys()),
                )
            ]
        program = Program(
            program_id=str(pr["id"]),
            tenant_id=UUID(str(pr["tenant_id"])),
            total_credits=int(pr.get("total_credits_required", 120)),
            requirements=reqs,
        )

    return transcript, catalog, graph, coreqs, program


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
        try:
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as _db:
                data = await _load_student_data(_db, student_id, tenant_id)
            if not data:
                err = ToolError(
                    error=f"Student {student_id} not found in tenant {tenant_id}",
                    retryable=False,
                    category="validation",
                )
                return err.model_dump_json()

            transcript, catalog, graph, coreqs, program = _build_engine_objects(
                data, deps.current_term, deps.current_year
            )
            if program is None:
                err = ToolError(
                    error="Student has no program assigned — cannot audit.",
                    retryable=False,
                    category="validation",
                )
                return err.model_dump_json()

            result = audit(
                transcript=transcript,
                program=program,
                graph=graph,
                catalog=catalog,
                current_term=deps.current_term,
                current_year=deps.current_year,
            )

            eligible_list = ", ".join(result.eligible_now) or "none"
            remaining = [r.requirement_id for r in result.remaining_requirements]

            summary = (
                f"Credits completed: {result.credits_completed:.1f} / "
                f"{result.total_credits_required:.1f} "
                f"({result.pct_complete * 100:.0f}%).\n"
                f"Remaining requirement groups: {', '.join(remaining) or 'none'}.\n"
                f"Courses eligible now: {eligible_list}.\n"
                f"GPA: {result.cumulative_gpa:.2f}."
            )
            if data["student"].get("has_hold"):
                reason = data["student"].get("hold_reason") or "unknown"
                summary += f"\n⚠ Student has an active hold: {reason}."

            _log.info(
                "tool.audit_degree.done",
                student_id=student_id,
                eligible_count=len(result.eligible_now),
            )
            return summary

        except Exception as exc:
            _log.error("tool.audit_degree.error", error=str(exc))
            err = ToolError(error=str(exc), retryable=True, category="engine")
            return err.model_dump_json()

    @tool(args_schema=RagSearchInput)
    async def rag_search(query: str, tenant_id: str) -> str:
        """Search the university's course catalog and policy documents.
        Use for: course descriptions, prerequisites, policies, deadlines,
        degree requirements, and any factual university information.
        Academic answers must be grounded in a rag_search result.
        """
        try:
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as _db:
                results = await retrieve(
                    query=query,
                    tenant_id=tenant_id,
                    session=_db,
                    cohere_client=deps.cohere_client,
                    settings=deps.settings,
                )
            if not results:
                _log.info("tool.rag_search.empty", query_snippet=query[:60])
                return "No relevant documents found for this query."

            parts = []
            for i, r in enumerate(results, start=1):
                label = r.code or r.doc or r.source
                parts.append(f"[{i}] {label}: {r.content}")
            return "\n\n".join(parts)

        except Exception as exc:
            _log.error("tool.rag_search.error", error=str(exc))
            err = ToolError(error=str(exc), retryable=True, category="external")
            return err.model_dump_json()

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
        try:
            from keel.domain.models import Term as _Term

            try:
                term = _Term(start_term.lower())
            except ValueError:
                err = ToolError(
                    error=f"Invalid term '{start_term}'.", retryable=False, category="validation"
                )
                return err.model_dump_json()

            async with tenant_session(deps.session_factory, UUID(tenant_id)) as _db:
                data = await _load_student_data(_db, student_id, tenant_id)
            if not data:
                err = ToolError(
                    error=f"Student {student_id} not found.", retryable=False, category="validation"
                )
                return err.model_dump_json()

            transcript, catalog, graph, coreqs, program = _build_engine_objects(
                data, term, start_year
            )
            if program is None:
                err = ToolError(
                    error="Student has no program.", retryable=False, category="validation"
                )
                return err.model_dump_json()

            from keel.domain.engine.audit import audit as _audit
            from keel.domain.engine.contracts import PlanTerm
            from keel.domain.engine.risk_inputs import score_plan_term

            audit_result = _audit(
                transcript=transcript,
                program=program,
                graph=graph,
                catalog=catalog,
                current_term=term,
                current_year=start_year,
            )

            plan_term = PlanTerm(term=term, year=start_year, course_codes=course_codes)
            features, vector = score_plan_term(audit_result, plan_term, catalog)

            # Call model-server (shared ModelClient).
            prediction = None
            if deps.model_client is not None:
                prediction = await deps.model_client.predict_grad_risk(vector.tolist())

            if prediction is None:
                return (
                    "Risk prediction is temporarily unavailable. "
                    "Your plan has been verified by the engine — please consult your advisor "
                    "for a risk assessment."
                )

            # Deterministic reasons from feature values (spec §3.2 — not LLM-invented).
            reasons = _build_risk_reasons(features, prediction.label)

            # LLM writes only the mitigation plan from those reasons.
            mitigation = await _llm_mitigation(
                llm=deps.llm_agent,
                label=prediction.label,
                score=prediction.score,
                reasons=reasons,
                course_codes=course_codes,
            )

            _log.info(
                "tool.predict_risk.done",
                student_id=student_id,
                label=prediction.label,
                score=round(prediction.score, 3),
                tenant_id=tenant_id,
            )
            return (
                f"**Risk Assessment** — {prediction.label.upper()} "
                f"(confidence: {prediction.score:.0%})\n\n"
                f"**Key factors:**\n{reasons}\n\n"
                f"**Mitigation:**\n{mitigation}"
            )

        except Exception as exc:
            _log.error("tool.predict_risk.error", error=str(exc))
            err = ToolError(error=str(exc), retryable=True, category="engine")
            return err.model_dump_json()

    @tool(args_schema=GpaEstimateInput)
    async def gpa_estimate(student_id: str, tenant_id: str, course_codes: list[str]) -> str:
        """Produce an LLM-based GPA estimate for a proposed course load.
        This is an estimate only — NOT a prediction. Always hard-caveated.
        Never present this as a guarantee or use it to gate feasibility.
        """
        try:
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as _db:
                data = await _load_student_data(_db, student_id, tenant_id)
            if not data:
                err = ToolError(
                    error=f"Student {student_id} not found.", retryable=False, category="validation"
                )
                return err.model_dump_json()

            from keel.domain.models import Term as _Term

            transcript, catalog, _, _, _ = _build_engine_objects(data, _Term.FALL, 2025)

            # Compute current GPA from transcript.
            grades = [t.grade for t in transcript if t.grade is not None]
            current_gpa = float(sum(grades) / len(grades)) if grades else 0.0

            course_details = [
                f"{c}: {catalog[c].name} (difficulty {catalog[c].difficulty}/5)"
                for c in course_codes
                if c in catalog
            ]
            course_list = "\n".join(course_details) or "No recognized courses."

            from langchain_core.messages import HumanMessage, SystemMessage

            prompt = (
                f"A student has a current cumulative GPA of {current_gpa:.2f}. "
                f"They are considering these courses:\n{course_list}\n\n"
                "Give a brief, realistic GPA estimate for this term load. "
                "You MUST include the caveat: 'This is an estimate only, not a prediction.' "
                "Keep the response under 3 sentences."
            )
            result = await deps.llm_agent.ainvoke(
                [
                    SystemMessage(
                        content="You are an academic advisor giving a rough GPA estimate."
                    ),  # noqa: E501
                    HumanMessage(content=prompt),
                ]
            )
            return f"**GPA Estimate (estimate only, not a prediction):** {result.content}"

        except Exception as exc:
            _log.error("tool.gpa_estimate.error", error=str(exc))
            err = ToolError(error=str(exc), retryable=True, category="engine")
            return err.model_dump_json()

    return [audit_degree, rag_search, predict_risk, gpa_estimate]


# ---------------------------------------------------------------------------
# Risk-reason helpers (internal — deterministic, not LLM)
# ---------------------------------------------------------------------------


def _build_risk_reasons(features: dict[str, float], label: str) -> str:
    """Derive human-readable risk reasons from salient feature values.

    Reasons are DETERMINISTIC — derived from feature thresholds, not LLM-invented.
    This is what the LLM uses to write the mitigation plan (it explains, not decides).
    """
    reasons: list[str] = []

    gpa = features.get("cumulative_gpa", 4.0)
    if gpa < 2.0:
        reasons.append(f"Cumulative GPA is low ({gpa:.2f}) — below the 2.0 threshold.")
    elif gpa < 2.5:
        reasons.append(f"Cumulative GPA is borderline ({gpa:.2f}).")

    gpa_trend = features.get("gpa_trend", 0.0)
    if gpa_trend < -0.3:
        reasons.append(f"GPA trend is declining ({gpa_trend:+.2f} per term).")

    failures = features.get("num_failures", 0)
    if failures > 0:
        reasons.append(f"{int(failures)} failed course(s) on transcript.")

    repeats = features.get("num_repeats", 0)
    if repeats > 0:
        reasons.append(f"{int(repeats)} repeated course(s) on transcript.")

    progress = features.get("progress_rate", 1.0)
    if progress < 0.8:
        reasons.append(f"Progress rate is below expected ({progress:.0%} of schedule).")

    workload = features.get("planned_workload_index", 0.0)
    if workload > 54:
        reasons.append(f"Planned workload is heavy (index {workload:.0f}).")

    hard = features.get("num_hard_courses", 0)
    if hard >= 2:
        reasons.append(f"{int(hard)} high-difficulty course(s) in the planned term.")

    if not reasons:
        reasons.append("No significant risk factors detected.")

    return "\n".join(f"• {r}" for r in reasons)


async def _llm_mitigation(
    *,
    llm: Any,
    label: str,
    score: float,
    reasons: str,
    course_codes: list[str],
) -> str:
    """Ask the LLM to write a mitigation plan given deterministic reasons.

    The LLM explains and suggests — it does NOT compute features or decide the label.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    if label == "on_track":
        return "Your plan looks healthy. Keep attending office hours and stay on schedule."

    prompt = (
        f"A student's graduation-risk model returned: {label.upper()} ({score:.0%} confidence).\n"
        f"Key risk factors (deterministic, not your analysis):\n{reasons}\n"
        f"Proposed courses: {', '.join(course_codes)}\n\n"
        "Write a concise (3-5 bullet points) mitigation plan to help the student reduce risk. "
        "Focus on actionable steps. Do not re-state the risk factors verbatim."
    )
    try:
        result = await llm.ainvoke(
            [
                SystemMessage(content="You are a supportive academic advisor."),
                HumanMessage(content=prompt),
            ]
        )
        return str(result.content)
    except Exception:
        return "Please speak with your academic advisor to discuss a mitigation strategy."
