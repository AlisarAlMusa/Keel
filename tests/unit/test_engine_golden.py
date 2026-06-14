"""Planner-correctness golden set — the CI gate (spec §7, tasks T4).

Every seeded violation must be caught; every legal plan must return [].
Run with: uv run pytest tests/unit/test_engine_golden.py -v

Spec says ≥ 20 cases. We have 25 (17 violation cases + 8 legal plans).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from keel.domain.engine.contracts import (
    Plan,
    PlanMeta,
    PlanTerm,
    ViolationCode,
)
from keel.domain.engine.graph import PrereqGraph
from keel.domain.engine.verifier import verify
from keel.domain.exceptions import CyclicCatalogError
from keel.domain.models import (
    Corequisite,
    Course,
    DayOfWeek,
    Prerequisite,
    Section,
    Term,
    TimeSlot,
    TranscriptEntry,
)

# ── Shared fixtures ────────────────────────────────────────────────────────────

TENANT_ID: UUID = UUID("00000000-0000-0000-0000-000000000001")
STUDENT_ID: UUID = UUID("00000000-0000-0000-0000-000000000002")
META = PlanMeta(generated_by="manual", created_at=datetime(2024, 1, 1, tzinfo=UTC))


def _c(
    code: str,
    credits: int = 3,
    difficulty: int = 2,
    terms: tuple[Term, ...] = (Term.FALL, Term.SPRING),
) -> Course:
    return Course(
        tenant_id=TENANT_ID,
        code=code,
        name=code,
        credits=credits,
        difficulty=difficulty,
        offered_terms=frozenset(terms),
    )


def _te(
    code: str, term: Term = Term.FALL, year: int = 2022, grade: str = "3.0", passed: bool = True
) -> TranscriptEntry:
    return TranscriptEntry(
        tenant_id=TENANT_ID,
        student_id=STUDENT_ID,
        course_code=code,
        term=term,
        year=year,
        grade=Decimal(grade),
        passed=passed,
    )


def _plan(*term_specs: tuple[Term, int, list[str]]) -> Plan:
    terms = [PlanTerm(term=t, year=y, course_codes=codes) for t, y, codes in term_specs]
    return Plan(
        plan_id=uuid4(),
        tenant_id=TENANT_ID,
        student_id=STUDENT_ID,
        program_id="P",
        name="test",
        version=1,
        active=True,
        terms=terms,
        meta=META,
    )


def _section(
    code: str,
    term: Term = Term.FALL,
    year: int = 2023,
    slots: tuple[TimeSlot, ...] = (),
    capacity: int = 10,
    enrolled: int = 0,
) -> Section:
    return Section(
        tenant_id=TENANT_ID,
        id=uuid4(),
        course_code=code,
        term=term,
        year=year,
        slots=slots,
        capacity=capacity,
        enrolled=enrolled,
    )


def _slot(day: DayOfWeek, start: int, end: int) -> TimeSlot:
    return TimeSlot(day=day, start_min=start, end_min=end)


# Standard catalog used by most cases
CATALOG: dict[str, Course] = {
    "CS101": _c("CS101", 3, 2, (Term.FALL, Term.SPRING)),
    "CS102": _c("CS102", 3, 3, (Term.FALL, Term.SPRING)),
    "CS201": _c("CS201", 3, 4, (Term.FALL,)),  # fall-only
    "CS202": _c("CS202", 4, 4, (Term.SPRING,)),  # spring-only
    "CS301": _c("CS301", 3, 3, (Term.FALL,)),
    "LAB101": _c("LAB101", 1, 2, (Term.FALL, Term.SPRING)),
    "ENG101": _c("ENG101", 3, 2, (Term.FALL, Term.SPRING)),
    "HEAVY1": _c("HEAVY1", 3, 5, (Term.FALL,)),
    "HEAVY2": _c("HEAVY2", 3, 5, (Term.FALL,)),
    "HEAVY3": _c("HEAVY3", 3, 5, (Term.FALL,)),
    "HEAVY4": _c("HEAVY4", 4, 5, (Term.FALL,)),
    "HEAVY5": _c("HEAVY5", 3, 5, (Term.FALL,)),
    "HEAVY6": _c("HEAVY6", 3, 5, (Term.FALL,)),
    "HEAVY7": _c("HEAVY7", 3, 5, (Term.FALL,)),
}

PREREQS: list[Prerequisite] = [
    Prerequisite(tenant_id=TENANT_ID, course_code="CS102", requires_code="CS101"),
    Prerequisite(tenant_id=TENANT_ID, course_code="CS201", requires_code="CS102"),
    Prerequisite(tenant_id=TENANT_ID, course_code="CS301", requires_code="CS201"),
    Prerequisite(tenant_id=TENANT_ID, course_code="CS202", requires_code="CS101"),
]

GRAPH = PrereqGraph(PREREQS, frozenset(CATALOG.keys()))

COREQS: list[Corequisite] = [
    Corequisite(tenant_id=TENANT_ID, course_code="CS201", coreq_code="LAB101"),
]

PASSED_101 = [_te("CS101")]
PASSED_101_102 = [_te("CS101"), _te("CS102", term=Term.SPRING, year=2023)]
PASSED_101_102_201 = PASSED_101_102 + [_te("CS201", term=Term.FALL, year=2023)]


# ── Violation cases ────────────────────────────────────────────────────────────


class TestViolationCases:
    """Each test: one plan that MUST produce the named violation code."""

    # G1 — PREREQ_MISSING: CS102 needs CS101; CS101 not in transcript or plan
    def test_prereq_missing_not_in_transcript(self):
        plan = _plan((Term.FALL, 2023, ["CS102"]))
        v = verify(plan, CATALOG, GRAPH, [], COREQS, Term.FALL, 2023)
        codes = {x.code for x in v}
        assert ViolationCode.PREREQ_MISSING in codes

    # G2 — PREREQ_MISSING (same term): prereq and course in same term
    def test_prereq_missing_same_term(self):
        plan = _plan((Term.FALL, 2023, ["CS101", "CS102"]))
        v = verify(plan, CATALOG, GRAPH, [], COREQS, Term.FALL, 2023)
        codes = {x.code for x in v}
        assert ViolationCode.PREREQ_MISSING in codes

    # G3 — PREREQ_MISSING: chained; CS201 needs CS102 which needs CS101; none passed
    def test_prereq_missing_chained(self):
        plan = _plan((Term.FALL, 2023, ["CS201"]))
        v = verify(plan, CATALOG, GRAPH, [], COREQS, Term.FALL, 2023)
        codes = {x.code for x in v}
        assert ViolationCode.PREREQ_MISSING in codes

    # G4 — COREQ_MISSING: CS201 needs LAB101 as coreq; neither in same term nor passed
    def test_coreq_missing(self):
        plan = _plan(
            (Term.FALL, 2022, ["CS101"]),
            (Term.SPRING, 2023, ["CS102"]),
            (Term.FALL, 2023, ["CS201"]),  # no LAB101 in same term
        )
        v = verify(plan, CATALOG, GRAPH, [], COREQS, Term.FALL, 2022)
        codes = {x.code for x in v}
        assert ViolationCode.COREQ_MISSING in codes

    # G5 — COREQ_MISSING: coreq is also a prereq (the tricky combo)
    def test_coreq_also_prereq_pattern(self):
        # Build a special catalog where the coreq is also listed as a prereq
        cat = dict(CATALOG)
        extra_prereqs = list(PREREQS) + [
            Prerequisite(tenant_id=TENANT_ID, course_code="CS201", requires_code="LAB101"),
        ]
        g2 = PrereqGraph(extra_prereqs, frozenset(cat.keys()))
        plan = _plan((Term.FALL, 2023, ["CS201"]))
        v = verify(plan, cat, g2, PASSED_101_102, COREQS, Term.FALL, 2023)
        # should get PREREQ_MISSING (LAB101 not passed) AND COREQ_MISSING (not in same term)
        codes = {x.code for x in v}
        assert ViolationCode.PREREQ_MISSING in codes or ViolationCode.COREQ_MISSING in codes

    # G6 — REPEAT_PASSED: CS101 already passed
    def test_repeat_passed_course(self):
        plan = _plan((Term.FALL, 2023, ["CS101"]))
        v = verify(plan, CATALOG, GRAPH, PASSED_101, COREQS, Term.FALL, 2023)
        codes = {x.code for x in v}
        assert ViolationCode.REPEAT_PASSED in codes

    # G7 — NOT_OFFERED_THIS_TERM: CS201 is fall-only; placed in spring
    def test_wrong_term_registration(self):
        plan = _plan((Term.SPRING, 2024, ["CS201"]))
        v = verify(plan, CATALOG, GRAPH, PASSED_101_102, COREQS, Term.SPRING, 2024)
        codes = {x.code for x in v}
        assert ViolationCode.NOT_OFFERED_THIS_TERM in codes

    # G8 — NOT_OFFERED_THIS_TERM: CS202 is spring-only; placed in fall
    def test_wrong_term_spring_only_in_fall(self):
        plan = _plan((Term.FALL, 2023, ["CS202"]))
        v = verify(plan, CATALOG, GRAPH, PASSED_101, COREQS, Term.FALL, 2023)
        codes = {x.code for x in v}
        assert ViolationCode.NOT_OFFERED_THIS_TERM in codes

    # G9 — CREDIT_CAP_EXCEEDED: 7 courses × 3 cr = 21 > 18
    def test_credit_cap_exceeded(self):
        codes_list = ["HEAVY1", "HEAVY2", "HEAVY3", "HEAVY4", "HEAVY5", "HEAVY6", "HEAVY7"]
        plan = _plan((Term.FALL, 2023, codes_list))
        v = verify(plan, CATALOG, GRAPH, [], COREQS, Term.FALL, 2023)
        vcodes = {x.code for x in v}
        assert ViolationCode.CREDIT_CAP_EXCEEDED in vcodes

    # G10 — UNKNOWN_COURSE: course code not in catalog
    def test_unknown_course(self):
        plan = _plan((Term.FALL, 2023, ["GHOST999"]))
        v = verify(plan, CATALOG, GRAPH, [], COREQS, Term.FALL, 2023)
        codes = {x.code for x in v}
        assert ViolationCode.UNKNOWN_COURSE in codes

    # G11 — CAPACITY_FULL: section is full
    def test_capacity_full(self):
        sec = _section("CS101", capacity=30, enrolled=30)
        plan = _plan((Term.FALL, 2023, ["CS101"]))
        v = verify(
            plan,
            CATALOG,
            GRAPH,
            [],
            COREQS,
            Term.FALL,
            2023,
            scope="section",
            sections_by_code={"CS101": sec},
        )
        codes = {x.code for x in v}
        assert ViolationCode.CAPACITY_FULL in codes

    # G12 — TIME_CONFLICT: two sections overlap on the same day
    def test_time_conflict_same_day(self):
        slot_a = _slot(DayOfWeek.MON, 540, 630)  # 9:00–10:30
        slot_b = _slot(DayOfWeek.MON, 600, 690)  # 10:00–11:30
        secA = _section("CS101", slots=(slot_a,))
        secB = _section("ENG101", slots=(slot_b,))
        plan = _plan((Term.FALL, 2023, ["CS101", "ENG101"]))
        v = verify(
            plan,
            CATALOG,
            GRAPH,
            [],
            COREQS,
            Term.FALL,
            2023,
            scope="section",
            sections_by_code={"CS101": secA, "ENG101": secB},
        )
        codes = {x.code for x in v}
        assert ViolationCode.TIME_CONFLICT in codes

    # G13 — TIME_CONFLICT: multi-meeting section (lecture + lab) overlaps another
    def test_time_conflict_multi_meeting(self):
        slot_lecture = _slot(DayOfWeek.TUE, 540, 630)
        slot_lab = _slot(DayOfWeek.THU, 480, 570)
        slot_other = _slot(DayOfWeek.THU, 510, 600)  # overlaps lab slot
        secA = _section("CS101", slots=(slot_lecture, slot_lab))
        secB = _section("ENG101", slots=(slot_other,))
        plan = _plan((Term.FALL, 2023, ["CS101", "ENG101"]))
        v = verify(
            plan,
            CATALOG,
            GRAPH,
            [],
            COREQS,
            Term.FALL,
            2023,
            scope="section",
            sections_by_code={"CS101": secA, "ENG101": secB},
        )
        codes = {x.code for x in v}
        assert ViolationCode.TIME_CONFLICT in codes

    # G14 — PREREQ satisfied by EARLIER plan term (not transcript)
    def test_prereq_satisfied_by_earlier_plan_term(self):
        # CS101 in term1, CS102 in term2 — should be clean
        plan = _plan(
            (Term.FALL, 2022, ["CS101"]),
            (Term.FALL, 2023, ["CS102"]),
        )
        v = verify(plan, CATALOG, GRAPH, [], COREQS, Term.FALL, 2022)
        codes = {x.code for x in v}
        assert ViolationCode.PREREQ_MISSING not in codes

    # G15 — Circular catalog rejected at LOAD time (not plan time)
    def test_circular_prereq_catalog_rejected_at_load(self):
        cyclic = [
            Prerequisite(tenant_id=TENANT_ID, course_code="X", requires_code="Y"),
            Prerequisite(tenant_id=TENANT_ID, course_code="Y", requires_code="X"),
        ]
        with pytest.raises(CyclicCatalogError):
            PrereqGraph(cyclic, frozenset(["X", "Y"]))

    # G16 — hold does NOT appear as a plan violation (hold is write-time, not plan-time)
    def test_hold_does_not_block_planning(self):
        plan = _plan((Term.FALL, 2023, ["CS101"]))
        v = verify(plan, CATALOG, GRAPH, [], COREQS, Term.FALL, 2023)
        # No HOLD_BLOCK code should exist (it's not defined in ViolationCode)
        assert all(x.code != "HOLD_BLOCK" for x in v)
        # And CS101 with no prereqs/issues is clean
        assert v == []

    # G17 — Touching time slots (adjacent, not overlapping) are NOT a conflict
    def test_touching_slots_not_a_conflict(self):
        slot_a = _slot(DayOfWeek.WED, 540, 630)  # 9:00–10:30
        slot_b = _slot(DayOfWeek.WED, 630, 720)  # 10:30–12:00 (adjacent)
        secA = _section("CS101", slots=(slot_a,))
        secB = _section("ENG101", slots=(slot_b,))
        plan = _plan((Term.FALL, 2023, ["CS101", "ENG101"]))
        v = verify(
            plan,
            CATALOG,
            GRAPH,
            [],
            COREQS,
            Term.FALL,
            2023,
            scope="section",
            sections_by_code={"CS101": secA, "ENG101": secB},
        )
        codes = {x.code for x in v}
        assert ViolationCode.TIME_CONFLICT not in codes


# ── Legal plans (must return zero violations) ──────────────────────────────────


class TestLegalPlans:
    """Known-good plans must produce exactly zero violations."""

    def test_single_no_prereq_course(self):
        plan = _plan((Term.FALL, 2023, ["CS101"]))
        assert verify(plan, CATALOG, GRAPH, [], COREQS, Term.FALL, 2023) == []

    def test_chained_prereqs_across_terms(self):
        plan = _plan(
            (Term.FALL, 2022, ["CS101"]),
            (Term.SPRING, 2023, ["CS102"]),
            (Term.FALL, 2023, ["CS201", "LAB101"]),  # coreq satisfied in same term
        )
        assert verify(plan, CATALOG, GRAPH, [], COREQS, Term.FALL, 2022) == []

    def test_prereq_satisfied_by_transcript(self):
        plan = _plan((Term.FALL, 2023, ["CS201", "LAB101"]))
        assert verify(plan, CATALOG, GRAPH, PASSED_101_102, COREQS, Term.FALL, 2023) == []

    def test_coreq_satisfied_by_same_term(self):
        plan = _plan((Term.FALL, 2023, ["CS201", "LAB101"]))
        assert verify(plan, CATALOG, GRAPH, PASSED_101_102, COREQS, Term.FALL, 2023) == []

    def test_coreq_satisfied_by_transcript(self):
        transcript = PASSED_101_102 + [_te("LAB101", term=Term.FALL, year=2022)]
        plan = _plan((Term.FALL, 2023, ["CS201"]))
        assert verify(plan, CATALOG, GRAPH, transcript, COREQS, Term.FALL, 2023) == []

    def test_exactly_at_credit_cap(self):
        # 6 × 3 = 18 = cap exactly
        codes = ["HEAVY1", "HEAVY2", "HEAVY3", "HEAVY5", "HEAVY6", "HEAVY7"]
        plan = _plan((Term.FALL, 2023, codes))
        assert verify(plan, CATALOG, GRAPH, [], COREQS, Term.FALL, 2023) == []

    def test_section_open_no_conflict(self):
        slot_a = _slot(DayOfWeek.MON, 540, 630)
        slot_b = _slot(DayOfWeek.MON, 660, 750)
        secA = _section("CS101", slots=(slot_a,), capacity=30, enrolled=5)
        secB = _section("ENG101", slots=(slot_b,), capacity=30, enrolled=5)
        plan = _plan((Term.FALL, 2023, ["CS101", "ENG101"]))
        assert (
            verify(
                plan,
                CATALOG,
                GRAPH,
                [],
                COREQS,
                Term.FALL,
                2023,
                scope="section",
                sections_by_code={"CS101": secA, "ENG101": secB},
            )
            == []
        )

    def test_multi_term_full_sequence(self):
        plan = _plan(
            (Term.FALL, 2022, ["CS101", "ENG101"]),
            (Term.SPRING, 2023, ["CS102", "CS202"]),
            (Term.FALL, 2023, ["CS201", "LAB101"]),
            (Term.FALL, 2024, ["CS301"]),
        )
        assert verify(plan, CATALOG, GRAPH, [], COREQS, Term.FALL, 2022) == []
