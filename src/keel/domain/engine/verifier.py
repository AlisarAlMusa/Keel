"""Plan verifier — the kernel of the engine.

Every producer (LLM, greedy planner, swap, what-if, replan, manual edit,
registration) calls verify(). One source of feasibility truth.

scope="course"  → course-level checks only (planning).
scope="section" → course checks + section checks (registration).

Never raises on well-formed input — always returns a list of Violation objects.
Empty list = valid.

Source of truth: specs/004-phase-1-engine/spec.md §4.2, §5; plan.md §2.
"""

from __future__ import annotations

from typing import Literal

from keel.domain.engine.contracts import (
    Plan,
    PlanTerm,
    Violation,
    ViolationCode,
    ViolationScope,
)
from keel.domain.engine.graph import PrereqGraph
from keel.domain.models import (
    Corequisite,
    Course,
    Section,
    Term,
    TimeSlot,
    TranscriptEntry,
)

# Default per-term credit cap if the student record does not override it
DEFAULT_CREDIT_CAP: int = 18

VerifierScope = Literal["course", "section"]


def verify(
    plan: Plan,
    catalog: dict[str, Course],
    graph: PrereqGraph,
    transcript: list[TranscriptEntry],
    corequisites: list[Corequisite],
    current_term: Term,
    current_year: int,
    scope: VerifierScope = "course",
    sections_by_code: dict[str, Section] | None = None,
    credit_cap: int = DEFAULT_CREDIT_CAP,
) -> list[Violation]:
    """Verify a plan and return all violations. Empty list = valid.

    Parameters
    ----------
    plan:
        The multi-term plan to verify.
    catalog:
        All courses in the tenant's catalog, keyed by code.
    graph:
        Pre-built prerequisite DAG.
    transcript:
        Full student transcript (passed + failed).
    corequisites:
        All corequisite edges for the catalog.
    current_term / current_year:
        The term being planned (only the first upcoming PlanTerm is checked
        against offering schedule; historical terms in the plan are skipped).
    scope:
        "course" = course-level checks only.
        "section" = course + section checks (requires sections_by_code).
    sections_by_code:
        Chosen sections keyed by course code. Required when scope="section".
    credit_cap:
        Maximum credits allowed per term (default 18).
    """
    violations: list[Violation] = []

    passed_codes: frozenset[str] = frozenset(
        e.course_code for e in transcript if e.passed
    )

    # Repeats: courses taken more than once (already in transcript at all)
    all_transcript_codes: frozenset[str] = frozenset(e.course_code for e in transcript)

    # Build coreq map: course -> set of its corequisites
    coreq_map: dict[str, set[str]] = {}
    for cq in corequisites:
        coreq_map.setdefault(cq.course_code, set()).add(cq.coreq_code)

    # Accumulate passed codes as we walk forward through plan terms,
    # so prereqs can be satisfied by earlier plan terms.
    earned_codes = set(passed_codes)

    for plan_term in plan.terms:
        term_codes = list(plan_term.course_codes)
        term_set = set(term_codes)

        # --- UNKNOWN_COURSE ---------------------------------------------------
        for code in term_codes:
            if code not in catalog:
                violations.append(
                    Violation(
                        code=ViolationCode.UNKNOWN_COURSE,
                        scope=ViolationScope.COURSE,
                        term=plan_term.term,
                        year=plan_term.year,
                        courses=[code],
                        detail={"unknown_course": code},
                        message=f"Course {code!r} is not in the catalog.",
                    )
                )

        # Only check known courses further
        known_codes = [c for c in term_codes if c in catalog]

        # --- REPEAT_PASSED ----------------------------------------------------
        for code in known_codes:
            if code in passed_codes:
                violations.append(
                    Violation(
                        code=ViolationCode.REPEAT_PASSED,
                        scope=ViolationScope.COURSE,
                        term=plan_term.term,
                        year=plan_term.year,
                        courses=[code],
                        detail={"repeated_course": code},
                        message=f"{code} was already passed and cannot be re-planned.",
                    )
                )

        # --- PREREQ_MISSING ---------------------------------------------------
        for code in known_codes:
            for prereq in sorted(graph.direct_prereqs(code)):
                if prereq not in earned_codes and prereq not in term_set:
                    violations.append(
                        Violation(
                            code=ViolationCode.PREREQ_MISSING,
                            scope=ViolationScope.COURSE,
                            term=plan_term.term,
                            year=plan_term.year,
                            courses=[code],
                            detail={"missing_prereq": prereq, "for": code},
                            message=(
                                f"{code} requires {prereq}, which is not completed "
                                f"before {plan_term.term.value} {plan_term.year}."
                            ),
                        )
                    )
                elif prereq in term_set:
                    # Prereq is in the SAME term — not allowed
                    violations.append(
                        Violation(
                            code=ViolationCode.PREREQ_MISSING,
                            scope=ViolationScope.COURSE,
                            term=plan_term.term,
                            year=plan_term.year,
                            courses=[code, prereq],
                            detail={
                                "missing_prereq": prereq,
                                "for": code,
                                "reason": "same_term",
                            },
                            message=(
                                f"{code} requires {prereq}, but both are in the same term."
                            ),
                        )
                    )

        # --- COREQ_MISSING ----------------------------------------------------
        for code in known_codes:
            for coreq in sorted(coreq_map.get(code, set())):
                if coreq not in term_set and coreq not in earned_codes:
                    violations.append(
                        Violation(
                            code=ViolationCode.COREQ_MISSING,
                            scope=ViolationScope.COURSE,
                            term=plan_term.term,
                            year=plan_term.year,
                            courses=[code, coreq],
                            detail={"missing_coreq": coreq, "for": code},
                            message=(
                                f"{code} requires {coreq} as a corequisite "
                                f"(same term or already passed)."
                            ),
                        )
                    )

        # --- NOT_OFFERED_THIS_TERM --------------------------------------------
        for code in known_codes:
            course = catalog[code]
            if plan_term.term not in course.offered_terms:
                violations.append(
                    Violation(
                        code=ViolationCode.NOT_OFFERED_THIS_TERM,
                        scope=ViolationScope.COURSE,
                        term=plan_term.term,
                        year=plan_term.year,
                        courses=[code],
                        detail={
                            "course": code,
                            "placed_term": plan_term.term.value,
                            "offered_terms": sorted(
                                t.value for t in course.offered_terms
                            ),
                        },
                        message=(
                            f"{code} is not offered in {plan_term.term.value}. "
                            f"Offered: {sorted(t.value for t in course.offered_terms)}."
                        ),
                    )
                )

        # --- CREDIT_CAP_EXCEEDED ----------------------------------------------
        term_credits = sum(
            catalog[c].credits for c in known_codes if c in catalog
        )
        if term_credits > credit_cap:
            violations.append(
                Violation(
                    code=ViolationCode.CREDIT_CAP_EXCEEDED,
                    scope=ViolationScope.COURSE,
                    term=plan_term.term,
                    year=plan_term.year,
                    courses=sorted(known_codes),
                    detail={
                        "term_credits": term_credits,
                        "cap": credit_cap,
                        "excess": term_credits - credit_cap,
                    },
                    message=(
                        f"Term has {term_credits} credits; cap is {credit_cap}."
                    ),
                )
            )

        # After processing this term, courses here count as earned for future terms
        earned_codes.update(known_codes)

        # ── Section-scope checks ───────────────────────────────────────────────
        if scope == "section" and sections_by_code:
            term_sections = [
                sections_by_code[c]
                for c in known_codes
                if c in sections_by_code
            ]

            # --- CAPACITY_FULL ------------------------------------------------
            for section in term_sections:
                if not section.is_open:
                    violations.append(
                        Violation(
                            code=ViolationCode.CAPACITY_FULL,
                            scope=ViolationScope.SECTION,
                            term=plan_term.term,
                            year=plan_term.year,
                            courses=[section.course_code],
                            detail={
                                "section_id": str(section.id),
                                "enrolled": section.enrolled,
                                "capacity": section.capacity,
                            },
                            message=(
                                f"Section for {section.course_code} is full "
                                f"({section.enrolled}/{section.capacity})."
                            ),
                        )
                    )

            # --- TIME_CONFLICT ------------------------------------------------
            # Collect all (slot, course_code) pairs for this term
            all_slots: list[tuple[TimeSlot, str]] = []
            for section in term_sections:
                for slot in section.slots:
                    all_slots.append((slot, section.course_code))

            # O(n²) is fine at this scale (≤ 7 courses × 3 meetings each)
            for i, (slot_a, code_a) in enumerate(all_slots):
                for slot_b, code_b in all_slots[i + 1 :]:
                    if code_a == code_b:
                        continue  # same course, different meetings — skip
                    if slot_a.overlaps(slot_b):
                        violations.append(
                            Violation(
                                code=ViolationCode.TIME_CONFLICT,
                                scope=ViolationScope.SECTION,
                                term=plan_term.term,
                                year=plan_term.year,
                                courses=sorted({code_a, code_b}),
                                detail={
                                    "course_a": code_a,
                                    "slot_a": {
                                        "day": slot_a.day.value,
                                        "start_min": slot_a.start_min,
                                        "end_min": slot_a.end_min,
                                    },
                                    "course_b": code_b,
                                    "slot_b": {
                                        "day": slot_b.day.value,
                                        "start_min": slot_b.start_min,
                                        "end_min": slot_b.end_min,
                                    },
                                },
                                message=(
                                    f"{code_a} and {code_b} have a time conflict "
                                    f"on {slot_a.day.value}."
                                ),
                            )
                        )

    # Deduplicate: same (code, courses tuple) should not appear twice
    seen: set[tuple[str, tuple[str, ...]]] = set()
    deduped: list[Violation] = []
    for v in violations:
        key = (v.code.value, tuple(sorted(v.courses)))
        if key not in seen:
            seen.add(key)
            deduped.append(v)

    return deduped
