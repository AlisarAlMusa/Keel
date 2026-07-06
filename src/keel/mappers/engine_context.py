"""Mappers: raw repository rows -> typed engine/domain objects.

Pure assemblers between the persistence shape (row dicts from the repositories)
and the domain shape (engine objects). No I/O, no framework. Moved verbatim from
the agent tools so services and tools share one place to turn rows into engine
objects. Cross-cutting leaf module: depends on the domain, depended on by
services/tools.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from keel.domain.models import Corequisite, Course, Prerequisite, Term, TranscriptEntry


def _build_requirement(
    *,
    group_name: str,
    codes: list[str],
    required_credits: int | None,
    catalog: dict[str, Course],
) -> Any:
    """Model one program-requirement group as CORE or an ELECTIVE_GROUP.

    A group is CORE when the student must take every listed course (the required
    credits meet or exceed everything on offer in the group). It is an ELECTIVE_GROUP
    when only *some* of the listed courses are needed (e.g. "9 credits from 7
    courses") — modelling it as CORE is the bug that made the planner schedule the
    whole elective pool. ``choose`` is the minimum number of courses (largest-credit
    first) needed to reach the required credits.
    """
    from keel.domain.engine.contracts import CoreRequirement, ElectiveGroupRequirement

    in_cat = [c for c in codes if c in catalog]
    total_credits = sum(catalog[c].credits for c in in_cat)
    # No credit cap, or it covers the whole pool → every course is required (CORE).
    if not required_credits or required_credits >= total_credits or not in_cat:
        return CoreRequirement(type="CORE", requirement_id=group_name, courses=list(codes))

    # Elective group — choose the fewest courses (largest credits first) to meet the floor.
    ordered = sorted(in_cat, key=lambda c: (-catalog[c].credits, c))
    acc = 0
    choose = 0
    for c in ordered:
        if acc >= required_credits:
            break
        acc += catalog[c].credits
        choose += 1
    return ElectiveGroupRequirement(
        type="ELECTIVE_GROUP",
        requirement_id=group_name,
        choose=max(1, choose),
        from_courses=list(in_cat),
    )


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
                _build_requirement(
                    group_name=rr["group_name"],
                    codes=list(codes),
                    required_credits=rr.get("required_credits"),
                    catalog=catalog,
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
