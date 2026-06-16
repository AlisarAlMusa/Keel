"""Agent tool definitions: audit_degree, rag_search, propose_plan.

Each tool is a function returned from a factory that closes over its
dependencies (DB session, cohere client, LLM, etc.).  Call make_tools()
at agent-build time; pass the result to ToolNode.

Tool rules (spec §3.5):
- Typed Pydantic args_schema on every tool.
- Returns a structured string result, or a ToolError JSON on failure.
- Never raises — catch, log, return ToolError.
- No silent failures: every error is logged.

propose_plan repair loop (spec §3.5):
  LLM proposes → verifier validates → LLM repairs from violations (≤3 attempts)
  → greedy fallback planner → clean "no valid plan" message.
  No risk scoring or ranking in Phase 2.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import cohere
import sqlalchemy as sa
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from keel.config import Settings
from keel.domain.engine.audit import audit
from keel.domain.engine.contracts import (
    Plan,
    PlanMeta,
    PlanTerm,
    Program,
    Violation,
)
from keel.domain.engine.graph import PrereqGraph
from keel.domain.engine.planner import greedy_plan
from keel.domain.engine.verifier import verify
from keel.domain.models import Corequisite, Course, Prerequisite, Term, TranscriptEntry
from keel.domain.schemas import ToolError
from keel.infra.database.session import tenant_session
from keel.infra.rag import retrieve
from keel.logging import get_logger

_log = get_logger(__name__)

_PROMPT_VERSION = "v1"


# ---------------------------------------------------------------------------
# Tool input schemas
# ---------------------------------------------------------------------------


class AuditDegreeInput(BaseModel):
    """Input for audit_degree tool."""

    student_id: str
    tenant_id: str


class RagSearchInput(BaseModel):
    """Input for rag_search tool."""

    query: str
    tenant_id: str


class ProposePlanInput(BaseModel):
    """Input for propose_plan tool."""

    student_id: str
    tenant_id: str
    start_term: str  # e.g. "fall"
    start_year: int


# ---------------------------------------------------------------------------
# DB query helpers (raw SQL, tenant-filtered)
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
    PrereqGraph,
    list[Corequisite],
    Program | None,
]:
    """Convert raw DB rows into typed engine objects."""
    from decimal import Decimal

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

    program: Program | None = None
    pr = data.get("program_row")
    if pr:
        import json as _json

        from keel.domain.engine.contracts import CoreRequirement, Program

        reqs = []
        for rr in data.get("req_rows", []):
            codes = rr["eligible_course_codes"]
            if isinstance(codes, str):
                codes = _json.loads(codes)
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


@dataclass
class AgentDeps:
    """All injectable dependencies the tools need."""

    session_factory: Any  # async_sessionmaker — tools open their own sessions per call
    cohere_client: cohere.AsyncClientV2
    llm_agent: ChatGoogleGenerativeAI
    settings: Settings
    current_term: Term = Term.FALL
    current_year: int = 2025


def make_tools(deps: AgentDeps) -> list[Any]:
    """Build the three agent tools closed over their dependencies."""

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
                parts.append(f"[{i}] {label}: {r.content[:400]}")
            return "\n\n".join(parts)

        except Exception as exc:
            _log.error("tool.rag_search.error", error=str(exc))
            err = ToolError(error=str(exc), retryable=True, category="external")
            return err.model_dump_json()

    @tool(args_schema=ProposePlanInput)
    async def propose_plan(
        student_id: str,
        tenant_id: str,
        start_term: str,
        start_year: int,
    ) -> str:
        """Build a feasible course plan starting from the given term.
        Runs the engine verifier — only returns plans that pass all
        hard constraints (prereqs, credit cap, offering, holds).
        No risk scoring in Phase 2.
        """
        try:
            term = Term(start_term.lower())
        except ValueError:
            err = ToolError(
                error=f"Invalid term '{start_term}'. Use: fall, spring, summer.",
                retryable=False,
                category="validation",
            )
            return err.model_dump_json()

        try:
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as _db:
                data = await _load_student_data(_db, student_id, tenant_id)
            if not data:
                err = ToolError(
                    error=f"Student {student_id} not found.",
                    retryable=False,
                    category="validation",
                )
                return err.model_dump_json()

            if data["student"].get("has_hold"):
                reason = data["student"].get("hold_reason") or "unknown"
                err = ToolError(
                    error=f"Student has an active hold ({reason}). Resolve it before planning.",
                    retryable=False,
                    category="validation",
                )
                return err.model_dump_json()

            transcript, catalog, graph, coreqs, program = _build_engine_objects(
                data, term, start_year
            )
            if program is None:
                err = ToolError(
                    error="Student has no program — cannot propose a plan.",
                    retryable=False,
                    category="validation",
                )
                return err.model_dump_json()

            audit_result = audit(
                transcript=transcript,
                program=program,
                graph=graph,
                catalog=catalog,
                current_term=term,
                current_year=start_year,
            )

            if not audit_result.eligible_now:
                return (
                    "No courses are currently eligible based on your transcript "
                    "and the courses offered this term. "
                    "You may have completed all requirements or there may be "
                    "prerequisite gaps — please consult your advisor."
                )

            # --- LLM propose → verify → repair loop (≤3 attempts) ---
            valid_plan: Plan | None = None
            last_violations: list[Violation] = []

            for attempt in range(1, 4):
                proposed = await _llm_propose(
                    llm=deps.llm_agent,
                    eligible=audit_result.eligible_now,
                    catalog=catalog,
                    violations=last_violations,
                    term=term,
                    year=start_year,
                    student_id=student_id,
                    tenant_id=tenant_id,
                    program=program,
                )
                if proposed is None:
                    _log.warning("tool.propose_plan.llm_parse_failed", attempt=attempt)
                    continue

                violations = verify(
                    plan=proposed,
                    catalog=catalog,
                    graph=graph,
                    transcript=transcript,
                    corequisites=coreqs,
                    current_term=term,
                    current_year=start_year,
                )
                if not violations:
                    valid_plan = proposed
                    _log.info("tool.propose_plan.verified", attempt=attempt)
                    break
                last_violations = violations
                _log.info(
                    "tool.propose_plan.violations",
                    attempt=attempt,
                    count=len(violations),
                )

            if valid_plan is None:
                # Greedy fallback
                _log.info("tool.propose_plan.greedy_fallback")
                valid_plan = greedy_plan(
                    transcript=transcript,
                    program=program,
                    graph=graph,
                    catalog=catalog,
                    corequisites=coreqs,
                    start_term=term,
                    start_year=start_year,
                    student_id_hint=student_id,
                )

            if valid_plan is None:
                return (
                    "I was unable to build a valid plan given your current transcript "
                    "and the available courses. This may be due to prerequisite gaps, "
                    "hold issues, or no eligible courses this term. "
                    "Please speak with your academic advisor."
                )

            return _format_plan(valid_plan, catalog)

        except Exception as exc:
            _log.error("tool.propose_plan.error", error=str(exc))
            err = ToolError(error=str(exc), retryable=True, category="engine")
            return err.model_dump_json()

    return [audit_degree, rag_search, propose_plan]


# ---------------------------------------------------------------------------
# LLM propose helper (internal to propose_plan)
# ---------------------------------------------------------------------------


async def _llm_propose(
    *,
    llm: ChatGoogleGenerativeAI,
    eligible: list[str],
    catalog: dict[str, Course],
    violations: list[Violation],
    term: Term,
    year: int,
    student_id: str,
    tenant_id: str,
    program: Program,
) -> Plan | None:
    """Ask the LLM to propose a term plan and parse it into a Plan object."""
    from langchain_core.messages import HumanMessage, SystemMessage

    eligible_desc = []
    for code in eligible[:30]:  # cap at 30 to keep prompt manageable
        c = catalog.get(code)
        if c:
            eligible_desc.append(f"  {code}: {c.name} ({c.credits} cr)")

    violation_text = ""
    if violations:
        violation_text = "\n\nPrevious attempt had these violations — fix them:\n"
        for v in violations:
            violation_text += f"  - {v.code}: {v.message}\n"

    prompt = (
        f"You are helping a student plan their {term.value} {year} semester.\n"
        f"Program: {program.program_id}\n"
        f"Eligible courses (pick 3-4 appropriate ones, max 12-15 credits):\n"
        + "\n".join(eligible_desc)
        + violation_text
        + "\n\nRespond with ONLY a JSON object in this exact format:\n"
        '{"courses": ["CODE1", "CODE2", "CODE3"]}\n'
        "Choose courses that form a reasonable semester load. "
        "Respect credit limits and prerequisites."
    )

    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        result = await llm.ainvoke(
            [
                SystemMessage(
                    content=f"You are a course planning assistant. prompt_version={_PROMPT_VERSION}"
                ),
                HumanMessage(content=prompt),
            ]
        )
        text = str(result.content).strip()
        # Extract JSON (handle markdown code blocks)
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text.strip())
        codes = data.get("courses", [])
        if not codes:
            return None

        # Build a single-term Plan
        from uuid import uuid4

        return Plan(
            plan_id=uuid4(),
            tenant_id=UUID(tenant_id),
            student_id=UUID(student_id),
            program_id=program.program_id,
            name=f"Proposed {term.value.title()} {year}",
            version=1,
            active=False,
            terms=[PlanTerm(term=term, year=year, course_codes=codes)],
            meta=PlanMeta(generated_by="llm", created_at=datetime.utcnow()),
        )
    except Exception as exc:
        _log.warning("tool.propose_plan.llm_propose_error", error=str(exc))
        return None


def _format_plan(plan: Plan, catalog: dict[str, Course]) -> str:
    lines = [f"**Proposed Plan: {plan.name}** (engine-verified ✓)\n"]
    for pt in plan.terms:
        credits = sum(float(catalog[c].credits) for c in pt.course_codes if c in catalog)
        lines.append(f"{pt.term.value.title()} {pt.year} — {credits:.0f} credits:")
        for code in pt.course_codes:
            name = catalog[code].name if code in catalog else code
            cr = float(catalog[code].credits) if code in catalog else 0
            lines.append(f"  • {code}: {name} ({cr:.0f} cr)")
    lines.append("\nThis plan has passed all prerequisite, credit, and offering checks.")
    return "\n".join(lines)
