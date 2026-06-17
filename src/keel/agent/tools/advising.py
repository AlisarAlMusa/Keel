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


async def _load_program_engine(
    session: AsyncSession,
    tenant_id: str,
    program_code: str,
    catalog: dict[str, Course],
) -> Any:
    """Load a program (by code) as an engine Program object, or None if absent.

    Used by C4 / F2 to audit a student against a *target* program they are not
    currently enrolled in.
    """
    from keel.domain.engine.contracts import CoreRequirement, Program

    p = await session.execute(
        sa.text(
            "SELECT id, code, name, total_credits_required, tenant_id "
            "FROM programs WHERE code = :code AND tenant_id = :tid"
        ),
        {"code": program_code, "tid": tenant_id},
    )
    pr = p.mappings().first()
    if not pr:
        return None
    rq = await session.execute(
        sa.text(
            "SELECT group_name, required_credits, eligible_course_codes "
            "FROM program_requirements WHERE program_id = :pid AND tenant_id = :tid"
        ),
        {"pid": pr["id"], "tid": tenant_id},
    )
    reqs = []
    for rr in rq.mappings().all():
        codes = rr["eligible_course_codes"]
        if isinstance(codes, str):
            codes = json.loads(codes)
        reqs.append(
            CoreRequirement(type="CORE", requirement_id=rr["group_name"], courses=list(codes))
        )
    if not reqs:
        reqs = [CoreRequirement(type="CORE", requirement_id="all", courses=list(catalog.keys()))]
    return Program(
        program_id=str(pr["id"]),
        tenant_id=UUID(str(pr["tenant_id"])),
        total_credits=int(pr.get("total_credits_required", 120)),
        requirements=reqs,
    )


def _term_after(term: Term, year: int, n_terms: int) -> str:
    """Advance (term, year) by n_terms over a fall↔spring cadence; return a label."""
    order = [Term.FALL, Term.SPRING]
    try:
        idx = order.index(term)
    except ValueError:
        idx = 0
    total = idx + n_terms
    new_term = order[total % 2]
    new_year = year + (total // 2)
    return f"{new_term.value.title()} {new_year}"


def _compute_switch_impact(
    *,
    transcript: list[TranscriptEntry],
    catalog: dict[str, Course],
    target_program: Any,
    target_audit: Any,
    current_term: Term,
    current_year: int,
) -> dict[str, Any]:
    """Deterministic consequences of switching to ``target_program`` (spec C4/F2).

    lost_credits = passed credits that don't count toward the target program.
    extra_terms  = remaining target credits at ~15/term.
    delayed_courses = target requirements not yet eligible.
    """
    target_codes: set[str] = set()
    for req in target_program.requirements:
        target_codes.update(getattr(req, "courses", []) or [])

    lost_credits = 0
    for t in transcript:
        if t.passed and t.course_code not in target_codes and t.course_code in catalog:
            lost_credits += int(catalog[t.course_code].credits)

    remaining = int(getattr(target_audit, "remaining_credits", 0) or 0)
    extra_terms = max(0, -(-remaining // 15))  # ceil
    delayed = [c for c in target_codes if c not in set(target_audit.eligible_now)][:8]

    return {
        "lost_credits": lost_credits,
        "extra_terms": extra_terms,
        "new_graduation_term": _term_after(current_term, current_year, extra_terms),
        "delayed_courses": delayed,
    }


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
                f"Current term: {deps.current_term.value}, year: {deps.current_year}.\n"
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
        try:
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as _db:
                results = await retrieve(
                    query=query,
                    tenant_id=tenant_id,
                    session=_db,
                    cohere_client=deps.cohere_client,
                    settings=deps.settings,
                )
                data = await _load_student_data(_db, student_id, tenant_id)

            # Prereqs from the DAG (authoritative), keyed off any course codes in the query.
            dag_prereqs: list[str] = []
            codes_in_query = set(_COURSE_CODE_RE.findall(query.upper()))
            for r in data.get("prereq_rows", []):
                if r["course_code"] in codes_in_query:
                    dag_prereqs.append(f"{r['course_code']} requires {r['requires_code']}")

            context = "\n\n".join(
                f"[{i}] {(r.code or r.doc or r.source)}: {r.content}"
                for i, r in enumerate(results, 1)
            ) or "No catalog context found."
            sources = [r.code or r.doc or r.source for r in results]

            from langchain_core.messages import HumanMessage, SystemMessage

            prereq_note = (
                "\n\nAUTHORITATIVE PREREQUISITES (from the engine DAG — use these, "
                "not any prereqs in the context): " + "; ".join(dag_prereqs)
                if dag_prereqs
                else ""
            )
            prompt = (
                f"Answer the student's question using ONLY the catalog context below.\n"
                f"QUESTION: {query}\n\nCONTEXT:\n{context}{prereq_note}\n\n"
                "Be specific and complete. If prerequisites are stated above, use them verbatim."
            )
            res = await deps.llm_agent.ainvoke(
                [
                    SystemMessage(
                        content="You are a course advisor. Ground every claim in context."
                    ),
                    HumanMessage(content=prompt),
                ]
            )
            answer = _extract_advise_text(res.content)
            out = answer
            if dag_prereqs:
                out += "\n\n**Prerequisites (from records):** " + "; ".join(dag_prereqs)
            if sources:
                out += "\n\n_Sources: " + ", ".join(dict.fromkeys(sources)) + "_"
            return out
        except Exception as exc:
            _log.error("tool.course_advisor.error", error=str(exc))
            return ToolError(error=str(exc), retryable=True, category="external").model_dump_json()

    @tool(args_schema=DegreeAuditChatInput)
    async def degree_audit_chat(student_id: str, tenant_id: str) -> str:
        """Explain in plain language what the student still needs to graduate.
        The engine computes every number (missing requirements, remaining credits,
        eligible courses); the LLM only restates them verbatim — never recomputed.
        """
        try:
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as _db:
                data = await _load_student_data(_db, student_id, tenant_id)
            if not data:
                return ToolError(
                    error=f"Student {student_id} not found.", retryable=False, category="validation"
                ).model_dump_json()
            transcript, catalog, graph, coreqs, program = _build_engine_objects(
                data, deps.current_term, deps.current_year
            )
            if program is None:
                return ToolError(
                    error="Student has no program.", retryable=False, category="validation"
                ).model_dump_json()
            result = audit(
                transcript=transcript,
                program=program,
                graph=graph,
                catalog=catalog,
                current_term=deps.current_term,
                current_year=deps.current_year,
            )
            remaining_reqs = [r.requirement_id for r in result.remaining_requirements]
            remaining_credits = int(result.remaining_credits)
            eligible = list(result.eligible_now)

            from langchain_core.messages import HumanMessage, SystemMessage

            from keel.services.prompts import c2_audit_summary_prompt

            prompt = c2_audit_summary_prompt.build(
                remaining_requirements=remaining_reqs,
                remaining_credits=remaining_credits,
                eligible_courses=eligible,
            )
            res = await deps.llm_agent.ainvoke(
                [SystemMessage(content=prompt), HumanMessage(content="Summarize.")]
            )
            narrative = _extract_advise_text(res.content)
            return (
                f"**Degree audit**\n"
                f"- Remaining requirement groups: {', '.join(remaining_reqs) or 'none'}\n"
                f"- Remaining credits: {remaining_credits}\n"
                f"- Eligible now: {', '.join(eligible) or 'none'}\n\n{narrative}"
            )
        except Exception as exc:
            _log.error("tool.degree_audit_chat.error", error=str(exc))
            return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()

    @tool(args_schema=FailureRecoveryInput)
    async def failure_recovery(student_id: str, tenant_id: str, failed_course: str) -> str:
        """Build a concrete recovery plan after a student fails a course. The engine
        computes the downstream impact and rebuilds the eligible pool from the updated
        transcript; the recovery plan is produced by the SAME propose→verify→repair loop
        as propose_plan (failure baked into the audit) and is verifier-valid. No write.
        """
        try:
            failed_course = failed_course.upper()
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

            # Bake the failure into the transcript: the course is now NOT passed.
            failure_transcript = [t for t in transcript if t.course_code != failed_course]
            from decimal import Decimal as _Dec

            failure_transcript.append(
                TranscriptEntry(
                    tenant_id=UUID(tenant_id),
                    student_id=UUID(student_id),
                    course_code=failed_course,
                    grade=_Dec("0.0"),
                    passed=False,
                    term=term,
                    year=year - 1,
                )
            )
            recovery_audit = audit(
                transcript=failure_transcript,
                program=program,
                graph=graph,
                catalog=catalog,
                current_term=term,
                current_year=year,
            )

            # Downstream impact: courses that require the failed course (delayed).
            delayed = [
                r["course_code"]
                for r in data.get("prereq_rows", [])
                if r["requires_code"] == failed_course
            ]
            remaining_credits = int(recovery_audit.remaining_credits)
            extra_terms = max(1, -(-remaining_credits // 15))
            new_grad = _term_after(term, year, extra_terms)

            # Reuse the propose→verify→repair loop (lazy import avoids a cycle).
            from keel.agent.tools.planning import _validate_with_repair
            from keel.domain.engine.planner import greedy_plan
            from keel.domain.engine.verifier import verify
            from keel.domain.engine.workload import compute_workload

            seed_codes = ([failed_course] if failed_course in catalog else []) + [
                c for c in recovery_audit.eligible_now if c != failed_course
            ][:4]
            plan = await _validate_with_repair(
                proposed={"courses": seed_codes},
                catalog=catalog,
                graph=graph,
                transcript=failure_transcript,
                coreqs=coreqs,
                term=term,
                year=year,
                student_id=student_id,
                tenant_id=tenant_id,
                program=program,
                llm=deps.llm_agent,
            )
            if plan is None:
                plan = greedy_plan(
                    transcript=failure_transcript,
                    program=program,
                    graph=graph,
                    catalog=catalog,
                    corequisites=coreqs,
                    start_term=term,
                    start_year=year,
                    student_id_hint=student_id,
                )
            if plan is None or not plan.terms:
                delayed_txt = ", ".join(delayed) or "no downstream courses"
                return (
                    f"Failing {failed_course} delays {delayed_txt}. "
                    "I couldn't auto-build a recovery plan — please consult your advisor."
                )

            # Confirm verifier-valid (the type-level guarantee of a recovery plan).
            first_term = plan.terms[0]
            violations = verify(
                plan=plan,
                catalog=catalog,
                graph=graph,
                transcript=failure_transcript,
                corequisites=coreqs,
                current_term=term,
                current_year=year,
            )
            valid = not violations
            courses = [catalog[c] for c in first_term.course_codes if c in catalog]
            _, band = compute_workload(courses)
            course_lines = ", ".join(first_term.course_codes)
            check = "engine-verified ✓" if valid else "⚠ needs advisor review"
            next_label = f"{first_term.term.value.title()} {first_term.year}, {band.value} load"
            return (
                f"**Recovery plan after failing {failed_course}** ({check})\n"
                f"- Downstream delayed: {', '.join(delayed) or 'none'}\n"
                f"- Revised graduation estimate: {new_grad} (~{extra_terms} extra term(s))\n"
                f"- Next term ({next_label}): {course_lines}\n\n"
                "Retake the failed course as soon as it's offered. This is a plan, not a "
                "registration — say the word and I'll help you enroll."
            )
        except Exception as exc:
            _log.error("tool.failure_recovery.error", error=str(exc))
            return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()

    @tool(args_schema=MajorSwitchAdviceInput)
    async def major_switch_advice(student_id: str, tenant_id: str, target_program: str) -> str:
        """Advise on switching majors. The engine computes the consequences (lost
        credits, new timeline, delayed courses) against the target program; the LLM
        frames an explicitly ADVISORY recommendation — never a guarantee. No write.
        The action to actually switch is request_major_change (F2).
        """
        try:
            target_program = target_program.upper()
            term, year = deps.current_term, deps.current_year
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as _db:
                data = await _load_student_data(_db, student_id, tenant_id)
                if not data:
                    return ToolError(
                        error=f"Student {student_id} not found.",
                        retryable=False,
                        category="validation",
                    ).model_dump_json()
                transcript, catalog, graph, coreqs, own_program = _build_engine_objects(
                    data, term, year
                )
                target = await _load_program_engine(_db, tenant_id, target_program, catalog)
            if target is None:
                return ToolError(
                    error=f"Unknown target program '{target_program}'.",
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

            from langchain_core.messages import HumanMessage, SystemMessage

            from keel.services.prompts import c4_major_switch_prompt

            prompt = c4_major_switch_prompt.build(
                target_program=target_program,
                lost_credits=impact["lost_credits"],
                new_graduation_term=impact["new_graduation_term"],
                delayed_courses=impact["delayed_courses"],
            )
            res = await deps.llm_agent.ainvoke(
                [SystemMessage(content=prompt), HumanMessage(content="Give your recommendation.")]
            )
            recommendation = _extract_advise_text(res.content)
            return (
                f"**Switching to {target_program} — consequences (engine-computed)**\n"
                f"- Credits that wouldn't count: {impact['lost_credits']}\n"
                f"- New graduation estimate: {impact['new_graduation_term']} "
                f"(~{impact['extra_terms']} extra term(s))\n"
                f"- Courses still gated by prerequisites: "
                f"{', '.join(impact['delayed_courses']) or 'none'}\n\n"
                f"{recommendation}\n\n_Advisory only — not a guarantee._"
            )
        except Exception as exc:
            _log.error("tool.major_switch_advice.error", error=str(exc))
            return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()

    return [course_advisor, degree_audit_chat, failure_recovery, major_switch_advice]


def _extract_advise_text(content: Any) -> str:
    """Extract plain text from LLM content that may be a list of blocks (Gemini)."""
    if isinstance(content, list):
        parts = [
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
            if not isinstance(block, dict) or block.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return str(content) if content else ""


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
