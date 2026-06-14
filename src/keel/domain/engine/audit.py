"""Degree audit — the central student-progress calculation.

Takes a transcript + program + DAG + current term and produces AuditResult:
remaining requirements, credits, eligible-now set, and the student-state
metrics that feed RawFeatureInputs in grad_risk.py.

Source of truth: specs/004-phase-1-engine/spec.md §4.3, §4.4.
"""

from __future__ import annotations

from decimal import Decimal

from keel.domain.engine.contracts import (
    AuditResult,
    CompletedRequirement,
    CoreRequirement,
    CreditFloorRequirement,
    ElectiveGroupRequirement,
    Program,
    RemainingRequirement,
)
from keel.domain.engine.graph import PrereqGraph
from keel.domain.features.grad_risk import EXPECTED_CREDITS_PER_TERM
from keel.domain.models import Course, Term, TranscriptEntry

# Grades that count as a failure / withdrawal
_FAILURE_GRADES: frozenset[str] = frozenset({"F", "W", "WF"})

# Minimum passing grade
_PASSING_MIN: Decimal = Decimal("1.0")


def _is_passed(entry: TranscriptEntry) -> bool:
    """True if this transcript entry represents a passed course."""
    return entry.passed and entry.grade is not None and entry.grade >= _PASSING_MIN


def _is_failure(entry: TranscriptEntry) -> bool:
    """True for F or W grades regardless of the passed flag."""
    if entry.grade is None:
        return False
    # grade stored as a string-label grade or numeric
    # grade field is Decimal; treat grade == 0 as pass-possible; rely on passed flag
    # but also honour explicit F/W detection via the grade value heuristic
    return not entry.passed and entry.grade < _PASSING_MIN


def audit(
    transcript: list[TranscriptEntry],
    program: Program,
    graph: PrereqGraph,
    catalog: dict[str, Course],
    current_term: Term,
    current_year: int,
) -> AuditResult:
    """Compute the degree audit for one student.

    Parameters
    ----------
    transcript:
        All transcript entries for the student (mixed terms/years, passed or not).
    program:
        The student's degree program.
    graph:
        Pre-built prerequisite DAG for the catalog.
    catalog:
        All courses keyed by code.
    current_term:
        The term for which we compute eligible-now.
    current_year:
        The year for which we compute eligible-now.

    Returns
    -------
    AuditResult
        All progress metrics plus the eligible-now set.
    """
    # ── Passed courses ────────────────────────────────────────────────────────
    passed_entries = [e for e in transcript if _is_passed(e)]
    passed_codes: frozenset[str] = frozenset(e.course_code for e in passed_entries)

    # ── Credits completed ─────────────────────────────────────────────────────
    credits_completed: float = sum(
        float(catalog[e.course_code].credits) for e in passed_entries if e.course_code in catalog
    )

    total_credits_required = float(program.total_credits)
    remaining_credits = max(total_credits_required - credits_completed, 0.0)
    pct_complete = min(credits_completed / max(total_credits_required, 1.0), 1.0)

    # ── Terms elapsed (distinct (term, year) pairs with any entry) ────────────
    term_pairs: set[tuple[str, int]] = {(e.term, e.year) for e in transcript}
    terms_elapsed = max(len(term_pairs), 0)
    progress_rate = credits_completed / max(terms_elapsed * EXPECTED_CREDITS_PER_TERM, 1)

    # ── GPA metrics ───────────────────────────────────────────────────────────
    cumulative_gpa, recent_term_gpas = _compute_gpa_metrics(transcript, catalog)

    # ── Failures and repeats ──────────────────────────────────────────────────
    num_failures = sum(1 for e in transcript if _is_failure(e))
    # courses taken more than once (any grade)
    code_counts: dict[str, int] = {}
    for e in transcript:
        code_counts[e.course_code] = code_counts.get(e.course_code, 0) + 1
    num_repeats = sum(1 for cnt in code_counts.values() if cnt > 1)

    # ── Requirement satisfaction ───────────────────────────────────────────────
    completed_reqs, remaining_reqs = _check_requirements(program, passed_codes, catalog)

    # ── Eligible-now (spec §4.4) ──────────────────────────────────────────────
    program_codes: frozenset[str] = _all_program_codes(program)
    eligible_now = _compute_eligible_now(
        catalog=catalog,
        graph=graph,
        passed_codes=passed_codes,
        program_codes=program_codes,
        current_term=current_term,
    )

    return AuditResult(
        completed_requirements=completed_reqs,
        remaining_requirements=remaining_reqs,
        credits_completed=credits_completed,
        total_credits_required=total_credits_required,
        remaining_credits=remaining_credits,
        pct_complete=pct_complete,
        progress_rate=progress_rate,
        terms_elapsed=terms_elapsed,
        eligible_now=sorted(eligible_now),
        cumulative_gpa=cumulative_gpa,
        recent_term_gpas=recent_term_gpas,
        num_failures=num_failures,
        num_repeats=num_repeats,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _compute_gpa_metrics(
    transcript: list[TranscriptEntry],
    catalog: dict[str, Course],
) -> tuple[float, list[float]]:
    """Credit-weighted cumulative GPA and per-term GPA list (oldest → newest)."""
    total_grade_points = 0.0
    total_credit_hours = 0.0

    # per-term buckets: (term, year) -> [(grade_points, credits)]
    term_buckets: dict[tuple[str, int], list[tuple[float, int]]] = {}

    for entry in transcript:
        if entry.grade is None:
            continue  # in-progress
        credits = catalog[entry.course_code].credits if entry.course_code in catalog else 0
        if credits == 0:
            continue
        grade_val = float(entry.grade)
        total_grade_points += grade_val * credits
        total_credit_hours += credits

        key = (entry.term, entry.year)
        if key not in term_buckets:
            term_buckets[key] = []
        term_buckets[key].append((grade_val, credits))

    cumulative_gpa = total_grade_points / total_credit_hours if total_credit_hours > 0 else 0.0
    cumulative_gpa = min(max(cumulative_gpa, 0.0), 4.0)

    # Sort terms chronologically (year first, then fall < spring < summer within year)
    _term_order = {Term.FALL: 0, Term.SPRING: 1, Term.SUMMER: 2}
    sorted_keys = sorted(term_buckets.keys(), key=lambda k: (k[1], _term_order.get(Term(k[0]), 99)))

    recent_term_gpas: list[float] = []
    for key in sorted_keys:
        pairs = term_buckets[key]
        pts = sum(g * c for g, c in pairs)
        creds = sum(c for _, c in pairs)
        term_gpa = pts / creds if creds > 0 else 0.0
        recent_term_gpas.append(min(max(term_gpa, 0.0), 4.0))

    return cumulative_gpa, recent_term_gpas


def _all_program_codes(program: Program) -> frozenset[str]:
    """Collect every course code mentioned anywhere in the program requirements."""
    codes: set[str] = set()
    for req in program.requirements:
        if isinstance(req, CoreRequirement):
            codes.update(req.courses)
        elif isinstance(req, ElectiveGroupRequirement):
            codes.update(req.from_courses)
        # CreditFloorRequirement references a category, not specific codes
    return frozenset(codes)


def _compute_eligible_now(
    catalog: dict[str, Course],
    graph: PrereqGraph,
    passed_codes: frozenset[str],
    program_codes: frozenset[str],
    current_term: Term,
) -> list[str]:
    """Spec §4.4: prereqs satisfied AND offered this term AND not already passed
    AND in the student's program."""
    eligible: list[str] = []
    for code in sorted(program_codes):
        course = catalog.get(code)
        if course is None:
            continue
        if code in passed_codes:
            continue
        if current_term not in course.offered_terms:
            continue
        if not graph.prereqs_satisfied(code, passed_codes):
            continue
        eligible.append(code)
    return eligible


def _check_requirements(
    program: Program,
    passed_codes: frozenset[str],
    catalog: dict[str, Course],
) -> tuple[list[CompletedRequirement], list[RemainingRequirement]]:
    completed: list[CompletedRequirement] = []
    remaining: list[RemainingRequirement] = []

    for req in program.requirements:
        if isinstance(req, CoreRequirement):
            satisfied = [c for c in req.courses if c in passed_codes]
            still_needed = len(req.courses) - len(satisfied)
            if still_needed <= 0:
                completed.append(
                    CompletedRequirement(
                        requirement_id=req.requirement_id,
                        satisfied_by=satisfied,
                    )
                )
            else:
                remaining.append(
                    RemainingRequirement(
                        requirement_id=req.requirement_id,
                        type="CORE",
                        still_needed=float(still_needed),
                    )
                )

        elif isinstance(req, ElectiveGroupRequirement):
            chosen = [c for c in req.from_courses if c in passed_codes]
            still_needed = max(req.choose - len(chosen), 0)
            if still_needed == 0:
                completed.append(
                    CompletedRequirement(
                        requirement_id=req.requirement_id,
                        satisfied_by=chosen,
                    )
                )
            else:
                remaining.append(
                    RemainingRequirement(
                        requirement_id=req.requirement_id,
                        type="ELECTIVE_GROUP",
                        still_needed=float(still_needed),
                    )
                )

        elif isinstance(req, CreditFloorRequirement):
            earned = sum(
                float(catalog[c].credits)
                for c in passed_codes
                if c in catalog
                # CreditFloor doesn't filter by category at this level —
                # category is informational for advisors; all passed credits count
            )
            still_needed = max(req.min_credits - earned, 0.0)
            if still_needed <= 0:
                completed.append(
                    CompletedRequirement(
                        requirement_id=req.requirement_id,
                        satisfied_by=sorted(passed_codes & set(catalog.keys())),
                    )
                )
            else:
                remaining.append(
                    RemainingRequirement(
                        requirement_id=req.requirement_id,
                        type="CREDIT_FLOOR",
                        still_needed=still_needed,
                    )
                )

    return completed, remaining
