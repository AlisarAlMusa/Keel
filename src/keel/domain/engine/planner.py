"""Greedy fallback planner — produces a verifier-valid plan or fails cleanly.

Goal: generate *a* valid plan, not the optimal one. The LLM will produce
better plans; this is the safety net when the LLM fails or is unavailable.

Algorithm (plan.md §3):
  From the eligible-now pool, repeatedly:
  1. Pick courses by priority: (unlocks-most-downstream DESC, required-before-
     elective DESC, code ASC for stable tie-break).
  2. Add until the credit cap for this term is reached.
  3. Validate the term with the verifier. If violations remain, fall back.
  4. Advance: the chosen courses become "passed" for the next term's eligibility.
  5. Advance the term (FALL → SPRING → next-year FALL).
  6. Repeat until all requirements are met or max_terms is hit.

Returns None if no valid plan is found (caller falls back / escalates).

Source of truth: specs/004-phase-1-engine/spec.md §5; plan.md §3.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from keel.domain.engine.contracts import (
    Plan,
    PlanMeta,
    PlanTerm,
    Program,
)
from keel.domain.engine.graph import PrereqGraph
from keel.domain.engine.verifier import verify
from keel.domain.models import (
    Corequisite,
    Course,
    Term,
    TranscriptEntry,
)

# Hard cap on plan length — prevents infinite loops
_MAX_TERMS: int = 16

# Term rotation: fall → spring → (next year) fall → spring …
_NEXT_TERM: dict[Term, tuple[Term, int]] = {
    Term.FALL: (Term.SPRING, 0),  # same year
    Term.SPRING: (Term.FALL, 1),  # next year
    Term.SUMMER: (Term.FALL, 0),  # treat summer as transitional; go to fall same year
}


def _advance_term(term: Term, year: int) -> tuple[Term, int]:
    next_t, year_delta = _NEXT_TERM[term]
    return next_t, year + year_delta


def _all_program_codes(program: Program) -> frozenset[str]:
    from keel.domain.engine.contracts import (
        CoreRequirement,
        ElectiveGroupRequirement,
    )

    codes: set[str] = set()
    for req in program.requirements:
        if isinstance(req, CoreRequirement):
            codes.update(req.courses)
        elif isinstance(req, ElectiveGroupRequirement):
            codes.update(req.from_courses)
    return frozenset(codes)


def _is_core_code(code: str, program: Program) -> bool:
    """True if this code appears in any CORE requirement."""
    from keel.domain.engine.contracts import CoreRequirement

    return any(
        isinstance(req, CoreRequirement) and code in req.courses for req in program.requirements
    )


def _course_priority(
    code: str,
    graph: PrereqGraph,
    program: Program,
) -> tuple[int, int, str]:
    """Lower tuple = higher priority (for sorted() ascending)."""
    unlocks = graph.unlocks_count(code)
    is_core = _is_core_code(code, program)
    return (-unlocks, 0 if is_core else 1, code)


def greedy_plan(
    transcript: list[TranscriptEntry],
    program: Program,
    graph: PrereqGraph,
    catalog: dict[str, Course],
    corequisites: list[Corequisite],
    start_term: Term,
    start_year: int,
    credit_cap: int = 15,
    student_id_hint: str | None = None,
) -> Plan | None:
    """Produce a verifier-valid plan or return None.

    Parameters
    ----------
    transcript:
        The student's existing transcript.
    program:
        The degree program to satisfy.
    graph:
        Pre-built prerequisite DAG.
    catalog:
        Full course catalog for the tenant.
    corequisites:
        All corequisite edges.
    start_term / start_year:
        The first planning term.
    credit_cap:
        Max credits allowed per term (default 15 for greedy safety).
    student_id_hint:
        Optional UUID string for the Plan entity (for testing).

    Returns
    -------
    Plan if a valid plan was found, else None.
    """
    from uuid import UUID

    student_id = UUID(student_id_hint) if student_id_hint else uuid4()
    program_codes = _all_program_codes(program)

    # Working copy of passed codes — grows as we schedule terms
    working_passed: set[str] = {e.course_code for e in transcript if e.passed}
    working_transcript = list(transcript)

    current_term = start_term
    current_year = start_year
    plan_terms: list[PlanTerm] = []

    for _ in range(_MAX_TERMS):
        # Remaining courses to schedule
        remaining = program_codes - working_passed
        if not remaining:
            break  # all requirements satisfied

        # Eligible this term: in remaining, prereqs met, offered this term
        eligible: list[str] = []
        for code in sorted(remaining):
            course = catalog.get(code)
            if course is None:
                continue
            if current_term not in course.offered_terms:
                continue
            if not graph.prereqs_satisfied(code, frozenset(working_passed)):
                continue
            eligible.append(code)

        if not eligible:
            # Nothing available this term — skip to next term
            current_term, current_year = _advance_term(current_term, current_year)
            continue

        # Sort by priority: unlocks DESC, core-before-elective, code ASC
        eligible.sort(key=lambda c: _course_priority(c, graph, program))

        # Greedily fill up to credit_cap — also handle corequisites
        coreq_map: dict[str, set[str]] = {}
        for cq in corequisites:
            coreq_map.setdefault(cq.course_code, set()).add(cq.coreq_code)

        chosen: list[str] = []
        credits_used = 0

        for code in eligible:
            course = catalog[code]
            # Pull in any required coreqs that are also eligible and not yet chosen
            coreqs_needed: list[str] = [
                cq_code
                for cq_code in sorted(coreq_map.get(code, set()))
                if cq_code not in working_passed and cq_code not in chosen
            ]
            # Check combined credit budget
            extra = sum(catalog[cq].credits for cq in coreqs_needed if cq in catalog)
            if credits_used + course.credits + extra > credit_cap:
                continue
            chosen.append(code)
            credits_used += course.credits
            for coreq_code in coreqs_needed:
                if coreq_code in catalog and coreq_code not in chosen:
                    chosen.append(coreq_code)
                    credits_used += catalog[coreq_code].credits

        if not chosen:
            current_term, current_year = _advance_term(current_term, current_year)
            continue

        plan_term = PlanTerm(term=current_term, year=current_year, course_codes=chosen)

        # Validate this term with the verifier before committing
        candidate_plan = Plan(
            plan_id=uuid4(),
            tenant_id=program.tenant_id,
            student_id=student_id,
            program_id=program.program_id,
            name="greedy",
            version=1,
            active=False,
            terms=plan_terms + [plan_term],
            meta=PlanMeta(
                generated_by="greedy",
                created_at=datetime.now(UTC),
            ),
        )
        violations = verify(
            candidate_plan,
            catalog,
            graph,
            working_transcript,
            corequisites,
            current_term,
            current_year,
            scope="course",
            credit_cap=credit_cap,
        )
        if violations:
            # Verifier rejected — skip term (shouldn't happen with correct logic)
            current_term, current_year = _advance_term(current_term, current_year)
            continue

        plan_terms.append(plan_term)

        # Advance working state
        for code in chosen:
            working_passed.add(code)

        current_term, current_year = _advance_term(current_term, current_year)

    remaining_after = program_codes - working_passed
    if remaining_after:
        return None  # Could not complete the program within max_terms

    return Plan(
        plan_id=uuid4(),
        tenant_id=program.tenant_id,
        student_id=student_id,
        program_id=program.program_id,
        name="greedy",
        version=1,
        active=False,
        terms=plan_terms,
        meta=PlanMeta(
            generated_by="greedy",
            created_at=datetime.now(UTC),
        ),
    )
