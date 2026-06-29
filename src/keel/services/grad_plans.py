"""Student-owned graduation plan helpers.

These helpers keep saved graduation plans deterministic: the LLM may propose a
path, but save/edit/sync always re-run the engine verifier before persisting.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from keel.domain.engine.contracts import (
    CoreRequirement,
    ElectiveGroupRequirement,
    Plan,
    PlanMeta,
    PlanTerm,
    Program,
)
from keel.domain.engine.planner import greedy_plan
from keel.domain.engine.verifier import verify
from keel.domain.engine.workload import compute_workload
from keel.domain.models import Course, Term, TranscriptEntry
from keel.logging import get_logger

_log = get_logger(__name__)

_CATALOG_VERSION = "v1"


class GradPlanConflict(Exception):
    """Raised when saving would replace an existing active grad plan."""

    def __init__(self, existing_id: str, existing_name: str) -> None:
        super().__init__("Active graduation plan already exists.")
        self.existing_id = existing_id
        self.existing_name = existing_name


@dataclass(frozen=True)
class GradPlanMutation:
    message: str
    card: dict[str, Any] | None = None


def next_term(term: Term, year: int) -> tuple[Term, int]:
    if term == Term.FALL:
        return Term.SPRING, year + 1
    if term == Term.SPRING:
        return Term.SUMMER, year
    return Term.FALL, year


def term_sort_key(term: str, year: int) -> tuple[int, int]:
    order = {"spring": 1, "summer": 2, "fall": 3}
    return (int(year), order.get(str(term).lower(), 99))


def term_label(term: str | Term, year: int) -> str:
    value = term.value if isinstance(term, Term) else str(term)
    return f"{value.title()} {year}"


def requirement_label(code: str, program: Program | None) -> str:
    if program is None:
        return "Requirement"

    elective = False
    fallback = "Requirement"
    for req in program.requirements:
        if isinstance(req, CoreRequirement) and code in req.courses:
            name = req.requirement_id.lower()
            if "major" in name:
                return "Major"
            if "core" in name:
                return "Core"
            return "Required"
        if isinstance(req, ElectiveGroupRequirement) and code in req.from_courses:
            elective = True
        elif code in getattr(req, "from_courses", []):
            fallback = "Requirement"

    if elective:
        return "Elective"
    return fallback


def build_grad_plan_card(
    plan: Plan,
    catalog: dict[str, Course],
    program: Program | None,
    *,
    card_id: str,
    label: str,
    blurb: str,
    status_by_term: dict[tuple[str, int], str] | None = None,
    saved: bool = False,
) -> dict[str, Any]:
    """Convert a verifier-valid multi-term plan into the widget card contract.

    ``saved=True`` marks a card that represents the student's already-saved active plan
    (loaded, swapped, or synced) — the widget hides the "Save this plan" button for it,
    since there is nothing new to save. Freshly proposed plans default to ``saved=False``.
    """
    band_rank = {"light": 1, "medium": 2, "heavy": 3}
    status_by_term = status_by_term or {}
    terms_out: list[dict[str, Any]] = []
    heaviest: tuple[int, str] | None = None
    total_credits = 0

    for pt in plan.terms:
        courses = [catalog[c] for c in pt.course_codes if c in catalog]
        credits = sum(int(c.credits) for c in courses)
        total_credits += credits
        _, band = compute_workload(courses)
        label_text = term_label(pt.term, pt.year)
        terms_out.append(
            {
                "term": label_text,
                "termKey": pt.term.value,
                "year": pt.year,
                "status": status_by_term.get((pt.term.value, pt.year), "upcoming"),
                "courses": [
                    {
                        "code": c.code,
                        "title": c.name,
                        "credits": int(c.credits),
                        "requirement": requirement_label(c.code, program),
                    }
                    for c in courses
                ],
                "credits": credits,
                "workload": band.value,
            }
        )
        rank = band_rank.get(band.value, 2)
        if heaviest is None or rank > heaviest[0]:
            heaviest = (rank, label_text)

    grad = plan.terms[-1]
    return {
        "kind": "gradplan",
        "id": card_id,
        "label": label,
        "blurb": blurb,
        "termsToGrad": len(plan.terms),
        "graduates": term_label(grad.term, grad.year),
        "heaviestTerm": heaviest[1] if heaviest else None,
        "totalCredits": total_credits,
        "terms": terms_out,
        "saved": saved,
    }


def plan_from_terms(
    *,
    tenant_id: UUID,
    student_id: UUID,
    program: Program,
    name: str,
    terms: list[dict[str, Any]],
    generated_by: str = "manual",
) -> Plan:
    plan_terms = [
        PlanTerm(
            term=Term(str(t["term"]).lower()),
            year=int(t["year"]),
            course_codes=[str(c) for c in t.get("course_codes", [])],
        )
        for t in sorted(terms, key=lambda t: term_sort_key(str(t["term"]), int(t["year"])))
    ]
    return Plan(
        plan_id=uuid4(),
        tenant_id=tenant_id,
        student_id=student_id,
        program_id=program.program_id,
        name=name,
        version=1,
        active=True,
        terms=plan_terms,
        meta=PlanMeta(generated_by=generated_by, created_at=datetime.now(UTC)),
    )


def serialize_plan_data(
    plan: Plan,
    *,
    scope: str = "graduation",
    term_status: dict[tuple[str, int], str] | None = None,
) -> dict[str, Any]:
    term_status = term_status or {}
    return {
        "scope": scope,
        "terms": [
            {
                "term": pt.term.value,
                "year": pt.year,
                "course_codes": list(pt.course_codes),
                "status": term_status.get((pt.term.value, pt.year), "upcoming"),
            }
            for pt in plan.terms
        ],
        "meta": {
            "generated_by": plan.meta.generated_by,
            "updated_by": "keel",
        },
        "catalog_version": _CATALOG_VERSION,
    }


async def _engine_context(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    student_id: UUID,
    current_term: Term | None = None,
    current_year: int | None = None,
) -> tuple[
    list[TranscriptEntry],
    dict[str, Course],
    Any,
    list[Any],
    Program | None,
    dict[str, Any],
]:
    """Load student data and build engine objects.

    The shared loader currently lives with the advising tools; importing it here
    keeps this service on the same engine contract without duplicating SQL.
    """
    from keel.agent.tools.advising import _build_engine_objects, _load_student_data

    data = await _load_student_data(session, str(student_id), str(tenant_id))
    if not data:
        raise ValueError("Student not found.")
    student = data["student"]
    term = current_term
    if term is None:
        term = Term(str(student.get("current_term") or "fall").lower())
    year = current_year or int(student.get("current_year") or datetime.now(UTC).year)
    transcript, catalog, graph, coreqs, program = _build_engine_objects(data, term, year)
    return transcript, catalog, graph, coreqs, program, data


async def _active_grad_plan_row(
    session: AsyncSession, *, tenant_id: UUID, student_id: UUID
) -> dict[str, Any] | None:
    row = await session.execute(
        sa.text(
            "SELECT id, name, plan_data FROM plans "
            "WHERE tenant_id = :tid AND student_id = :sid "
            "AND is_active = true AND plan_data->>'scope' = 'graduation' "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"tid": str(tenant_id), "sid": str(student_id)},
    )
    found = row.mappings().first()
    return dict(found) if found else None


async def save_active_grad_plan(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    student_id: UUID,
    name: str,
    terms: list[dict[str, Any]],
    replace: bool = False,
) -> GradPlanMutation:
    transcript, catalog, graph, coreqs, program, data = await _engine_context(
        session, tenant_id=tenant_id, student_id=student_id
    )
    if program is None:
        raise ValueError("Student has no program.")

    existing = await _active_grad_plan_row(session, tenant_id=tenant_id, student_id=student_id)
    if existing and not replace:
        raise GradPlanConflict(str(existing["id"]), str(existing["name"]))

    plan = plan_from_terms(
        tenant_id=tenant_id,
        student_id=student_id,
        program=program,
        name=name,
        terms=terms,
        generated_by="llm",
    )
    start = plan.terms[0]
    violations = verify(
        plan=plan,
        catalog=catalog,
        graph=graph,
        transcript=transcript,
        corequisites=coreqs,
        current_term=start.term,
        current_year=start.year,
        credit_cap=int(data["student"].get("max_credits_per_term") or 18),
    )
    if violations:
        raise ValueError("; ".join(v.message for v in violations))

    await session.execute(
        sa.text(
            "UPDATE plans SET is_active = false, status = 'archived' "
            "WHERE tenant_id = :tid AND student_id = :sid AND is_active = true"
        ),
        {"tid": str(tenant_id), "sid": str(student_id)},
    )

    plan_data = serialize_plan_data(plan)
    row = await session.execute(
        sa.text(
            "INSERT INTO plans "
            "(tenant_id, student_id, name, version, status, plan_data, "
            "is_active, catalog_version, validated_at) "
            "VALUES (:tid, :sid, :name, 1, 'active', CAST(:data AS jsonb), "
            "true, :catalog_version, :now) RETURNING id"
        ),
        {
            "tid": str(tenant_id),
            "sid": str(student_id),
            "name": name,
            "data": json.dumps(plan_data),
            "catalog_version": _CATALOG_VERSION,
            "now": datetime.now(UTC),
        },
    )
    saved_id = row.scalar_one()
    await session.execute(
        sa.text(
            "INSERT INTO audit_log (tenant_id, actor, action, before, after) "
            "VALUES (:tid, :actor, 'grad_plan.saved', CAST(:before AS jsonb), "
            "CAST(:after AS jsonb))"
        ),
        {
            "tid": str(tenant_id),
            "actor": str(student_id),
            "before": json.dumps({"replaced_plan_id": str(existing["id"])} if existing else None),
            "after": json.dumps({"plan_id": str(saved_id), "name": name}),
        },
    )
    card = build_grad_plan_card(
        plan,
        catalog,
        program,
        card_id=str(saved_id),
        label=name,
        blurb="Saved graduation plan",
        saved=True,
    )
    verb = "Replaced" if existing else "Saved"
    return GradPlanMutation(message=f"{verb} your graduation plan.", card=card)


async def load_active_grad_plan(
    session: AsyncSession, *, tenant_id: UUID, student_id: UUID
) -> GradPlanMutation | None:
    existing = await _active_grad_plan_row(session, tenant_id=tenant_id, student_id=student_id)
    if not existing:
        return None
    transcript, catalog, graph, coreqs, program, data = await _engine_context(
        session, tenant_id=tenant_id, student_id=student_id
    )
    if program is None:
        raise ValueError("Student has no program.")
    plan_data = dict(existing["plan_data"] or {})
    plan = plan_from_terms(
        tenant_id=tenant_id,
        student_id=student_id,
        program=program,
        name=str(existing["name"]),
        terms=list(plan_data.get("terms", [])),
        generated_by="manual",
    )
    start = plan.terms[0]
    violations = verify(
        plan=plan,
        catalog=catalog,
        graph=graph,
        transcript=transcript,
        corequisites=coreqs,
        current_term=start.term,
        current_year=start.year,
        credit_cap=int(data["student"].get("max_credits_per_term") or 18),
    )
    if violations:
        await session.execute(
            sa.text(
                "UPDATE plans SET status = 'stale' WHERE id = :pid AND tenant_id = :tid"
            ),
            {"pid": str(existing["id"]), "tid": str(tenant_id)},
        )
        return GradPlanMutation(
            message="Your saved graduation plan is now stale: "
            + "; ".join(v.message for v in violations)
        )

    status_by_term = {
        (str(t["term"]), int(t["year"])): str(t.get("status") or "upcoming")
        for t in plan_data.get("terms", [])
    }
    card = build_grad_plan_card(
        plan,
        catalog,
        program,
        card_id=str(existing["id"]),
        label=str(existing["name"]),
        blurb="Saved graduation plan",
        status_by_term=status_by_term,
        saved=True,
    )
    return GradPlanMutation(
        message=f"Loaded your saved graduation plan: {existing['name']}.", card=card
    )


async def delete_active_grad_plan(
    session: AsyncSession, *, tenant_id: UUID, student_id: UUID
) -> GradPlanMutation:
    row = await session.execute(
        sa.text(
            "UPDATE plans SET is_active = false, status = 'archived' "
            "WHERE tenant_id = :tid AND student_id = :sid "
            "AND is_active = true AND plan_data->>'scope' = 'graduation' "
            "RETURNING id, name"
        ),
        {"tid": str(tenant_id), "sid": str(student_id)},
    )
    found = row.mappings().first()
    if not found:
        return GradPlanMutation(message="You do not have an active saved graduation plan.")
    await session.execute(
        sa.text(
            "INSERT INTO audit_log (tenant_id, actor, action, before, after) "
            "VALUES (:tid, :actor, 'grad_plan.deleted', CAST(:before AS jsonb), NULL)"
        ),
        {
            "tid": str(tenant_id),
            "actor": str(student_id),
            "before": json.dumps({"plan_id": str(found["id"]), "name": str(found["name"])}),
        },
    )
    return GradPlanMutation(message=f"Deleted saved graduation plan '{found['name']}'.")


async def swap_active_grad_plan_course(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    student_id: UUID,
    remove_code: str,
    add_code: str,
) -> GradPlanMutation:
    existing = await _active_grad_plan_row(session, tenant_id=tenant_id, student_id=student_id)
    if not existing:
        raise ValueError("No active saved graduation plan found.")
    transcript, catalog, graph, coreqs, program, data = await _engine_context(
        session, tenant_id=tenant_id, student_id=student_id
    )
    if program is None:
        raise ValueError("Student has no program.")

    plan_data = dict(existing["plan_data"] or {})
    terms = [dict(t) for t in plan_data.get("terms", [])]
    remove_code = remove_code.upper()
    add_code = add_code.upper()

    # Whole-plan guards (fast, deterministic) BEFORE touching any term — these make the
    # tool fail in one turn with an actionable message instead of the agent flailing, and
    # they prevent the duplicate-course bug: a course may not be added if it is already
    # scheduled in ANY term, not just the target term.
    all_codes = {str(c).upper() for t in terms for c in t.get("course_codes", [])}
    if remove_code not in all_codes:
        raise ValueError(
            f"{remove_code} is not in your graduation plan, so there is nothing to swap "
            f"out. Your plan currently has: {', '.join(sorted(all_codes))}."
        )
    if add_code == remove_code:
        raise ValueError(f"{add_code} is already in your plan in that spot — nothing to swap.")
    if add_code in all_codes:
        raise ValueError(
            f"{add_code} is already in your graduation plan — swapping it in would schedule "
            "it twice. Pick a course that isn't already planned."
        )
    if add_code not in catalog:
        raise ValueError(f"{add_code} is not a course in the catalog.")

    changed = False
    for term in terms:
        codes = [str(c).upper() for c in term.get("course_codes", [])]
        if remove_code not in codes:
            continue
        codes[codes.index(remove_code)] = add_code
        term["course_codes"] = codes
        changed = True
        break
    if not changed:  # defensive — remove_code was in all_codes, so this should not happen
        raise ValueError(f"{remove_code} is not in your active graduation plan.")

    plan = plan_from_terms(
        tenant_id=tenant_id,
        student_id=student_id,
        program=program,
        name=str(existing["name"]),
        terms=terms,
    )
    start = plan.terms[0]
    violations = verify(
        plan=plan,
        catalog=catalog,
        graph=graph,
        transcript=transcript,
        corequisites=coreqs,
        current_term=start.term,
        current_year=start.year,
        credit_cap=int(data["student"].get("max_credits_per_term") or 18),
    )
    if violations:
        raise ValueError("Swap rejected: " + "; ".join(v.message for v in violations))

    await session.execute(
        sa.text(
            "UPDATE plans SET plan_data = CAST(:data AS jsonb), validated_at = :now "
            "WHERE id = :pid AND tenant_id = :tid"
        ),
        {
            "pid": str(existing["id"]),
            "tid": str(tenant_id),
            "data": json.dumps(serialize_plan_data(plan)),
            "now": datetime.now(UTC),
        },
    )
    await session.execute(
        sa.text(
            "INSERT INTO audit_log (tenant_id, actor, action, before, after) "
            "VALUES (:tid, :actor, 'grad_plan.swap_course', CAST(:before AS jsonb), "
            "CAST(:after AS jsonb))"
        ),
        {
            "tid": str(tenant_id),
            "actor": str(student_id),
            "before": json.dumps({"removed": remove_code}),
            "after": json.dumps({"added": add_code, "plan_id": str(existing["id"])}),
        },
    )
    card = build_grad_plan_card(
        plan,
        catalog,
        program,
        card_id=str(existing["id"]),
        label=str(existing["name"]),
        blurb="Updated saved graduation plan",
        saved=True,
    )
    return GradPlanMutation(
        message=f"Swapped {remove_code} for {add_code} and re-verified your graduation plan.",
        card=card,
    )


async def sync_after_registration(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    student_id: UUID,
    section_ids: list[str],
) -> GradPlanMutation | None:
    """Update the active grad plan after successful registration.

    The registered term is frozen to the actual enrolled courses, then the
    deterministic greedy planner repairs the remaining path from the next term.
    """
    existing = await _active_grad_plan_row(session, tenant_id=tenant_id, student_id=student_id)
    if not existing or not section_ids:
        return None

    sec_rows = await session.execute(
        sa.text(
            "SELECT DISTINCT term, year FROM sections "
            "WHERE tenant_id = :tid AND id = ANY(:ids) "
            "ORDER BY year, term LIMIT 1"
        ),
        {"tid": str(tenant_id), "ids": [str(s) for s in section_ids]},
    )
    target = sec_rows.mappings().first()
    if not target:
        return None
    target_term = Term(str(target["term"]).lower())
    target_year = int(target["year"])

    enrolled_rows = await session.execute(
        sa.text(
            "SELECT DISTINCT s.course_code FROM enrollments e "
            "JOIN sections s ON s.id = e.section_id "
            "WHERE e.tenant_id = :tid AND e.student_id = :sid "
            "AND e.status = 'enrolled' AND s.term = :term AND s.year = :yr "
            "ORDER BY s.course_code"
        ),
        {
            "tid": str(tenant_id),
            "sid": str(student_id),
            "term": target_term.value,
            "yr": target_year,
        },
    )
    registered_codes = [str(r[0]) for r in enrolled_rows.fetchall()]
    if not registered_codes:
        return None

    transcript, catalog, graph, coreqs, program, data = await _engine_context(
        session,
        tenant_id=tenant_id,
        student_id=student_id,
        current_term=target_term,
        current_year=target_year,
    )
    if program is None:
        return None

    synthetic = list(transcript)
    for code in registered_codes:
        synthetic.append(
            TranscriptEntry(
                tenant_id=tenant_id,
                student_id=student_id,
                course_code=code,
                term=target_term,
                year=target_year,
                grade=None,
                passed=True,
            )
        )

    tail_term, tail_year = next_term(target_term, target_year)
    tail = greedy_plan(
        transcript=synthetic,
        program=program,
        graph=graph,
        catalog=catalog,
        corequisites=coreqs,
        start_term=tail_term,
        start_year=tail_year,
        credit_cap=15,
        student_id_hint=str(student_id),
    )
    fixed = PlanTerm(term=target_term, year=target_year, course_codes=registered_codes)
    terms = [fixed] + (tail.terms if tail is not None else [])
    repaired = Plan(
        plan_id=UUID(str(existing["id"])),
        tenant_id=tenant_id,
        student_id=student_id,
        program_id=program.program_id,
        name=str(existing["name"]),
        version=1,
        active=True,
        terms=terms,
        meta=PlanMeta(generated_by="greedy", created_at=datetime.now(UTC)),
    )
    violations = verify(
        plan=repaired,
        catalog=catalog,
        graph=graph,
        transcript=transcript,
        corequisites=coreqs,
        current_term=target_term,
        current_year=target_year,
        credit_cap=int(data["student"].get("max_credits_per_term") or 18),
    )
    if violations:
        await session.execute(
            sa.text(
                "UPDATE plans SET status = 'stale' WHERE id = :pid AND tenant_id = :tid"
            ),
            {"pid": str(existing["id"]), "tid": str(tenant_id)},
        )
        msg = (
            "Your registration succeeded, but your saved graduation plan is now stale: "
            + "; ".join(v.message for v in violations)
        )
        return GradPlanMutation(message=msg)

    status = {(target_term.value, target_year): "registered"}
    for pt in repaired.terms:
        status.setdefault((pt.term.value, pt.year), "upcoming")
    plan_data = serialize_plan_data(repaired, term_status=status)
    await session.execute(
        sa.text(
            "UPDATE plans SET plan_data = CAST(:data AS jsonb), validated_at = :now, "
            "status = 'active' WHERE id = :pid AND tenant_id = :tid"
        ),
        {
            "pid": str(existing["id"]),
            "tid": str(tenant_id),
            "data": json.dumps(plan_data),
            "now": datetime.now(UTC),
        },
    )
    await session.execute(
        sa.text(
            "INSERT INTO audit_log (tenant_id, actor, action, before, after) "
            "VALUES (:tid, :actor, 'grad_plan.synced_after_registration', "
            "CAST(:before AS jsonb), CAST(:after AS jsonb))"
        ),
        {
            "tid": str(tenant_id),
            "actor": str(student_id),
            "before": json.dumps({"registered_term": term_label(target_term, target_year)}),
            "after": json.dumps(
                {"plan_id": str(existing["id"]), "course_codes": registered_codes}
            ),
        },
    )
    card = build_grad_plan_card(
        repaired,
        catalog,
        program,
        card_id=str(existing["id"]),
        label=str(existing["name"]),
        blurb="Updated after registration",
        status_by_term=status,
        saved=True,
    )
    return GradPlanMutation(
        message="I updated your saved graduation plan to match the registration.",
        card=card,
    )
