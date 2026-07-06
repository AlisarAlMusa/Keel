"""Graduation-plan presentation: term/requirement labels and the widget card.

Pure functions moved verbatim from ``services/grad_plans.py`` — same output. The
grad-plans service re-exports these so its existing call sites are unchanged.
"""

from __future__ import annotations

from typing import Any

from keel.domain.engine.contracts import (
    CoreRequirement,
    ElectiveGroupRequirement,
    Plan,
    Program,
)
from keel.domain.engine.workload import compute_workload
from keel.domain.models import Course, Term


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
