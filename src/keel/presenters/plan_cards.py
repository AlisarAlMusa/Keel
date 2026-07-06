"""Course-plan presentation: widget plan cards, section/time formatting, and the
plain-text plan rendering used in the ranked-plan message.

Pure functions moved verbatim from ``agent/tools/planning.py`` — same output. No
I/O, no LLM. The planning tools import these instead of defining them inline.
"""

from __future__ import annotations

from typing import Any

from keel.domain.engine.contracts import Plan
from keel.domain.models import Course
from keel.presenters.grad_card import requirement_label


def _risk_level(risk_summary: str) -> str:
    """Map a risk summary string (e.g. 'at_risk (35%)') to the widget's enum.

    The widget renders only 'on_track' | 'at_risk'. Anything not clearly at-risk
    (including 'prediction unavailable') is shown as on_track so the card stays
    honest about the weaker signal rather than over-warning.
    """
    s = risk_summary.lower()
    return "at_risk" if ("at_risk" in s or "at risk" in s) else "on_track"


def _build_plan_cards(
    scored: list[dict[str, Any]],
    catalog: dict[str, Course],
    *,
    start_term: str,
    start_year: int,
    program: Any | None = None,
) -> list[dict[str, Any]]:
    """Convert engine-verified scored candidates into widget PlanData dicts.

    Keys match the widget contract (camelCase ``totalCredits``); only the first
    term is shown per card (the immediate registration term).
    """
    cards: list[dict[str, Any]] = []
    for i, sc in enumerate(scored):
        plan = sc["plan"]
        first_term = plan.terms[0] if plan.terms else None
        codes = list(first_term.course_codes) if first_term else []
        courses: list[dict[str, Any]] = []
        total = 0
        for code in codes:
            course = catalog.get(code)
            if course is None:
                continue
            credits = int(getattr(course, "credits", 3))
            total += credits
            courses.append(
                {
                    "code": code,
                    "title": course.name,
                    "credits": credits,
                    "requirement": requirement_label(code, program),
                }
            )
        label = str(sc.get("label") or "balanced")
        cards.append(
            {
                "id": f"plan-{i + 1}",
                "name": label.replace("_", " ").title(),
                "term": f"{start_term.title()} {start_year}",
                "totalCredits": total,
                "courses": courses,
                "risk": _risk_level(str(sc.get("risk", ""))),
                "workload": str(sc.get("workload", "medium")),
                "explanation": None,
            }
        )
    return cards


def _fmt_section_slots(slots: list[dict[str, Any]]) -> str:
    """Render meeting slots like 'Mon/Wed 9:00 AM–10:15 AM'."""
    if not slots:
        return "TBA"

    def _hm(mins: int) -> str:
        h, m = divmod(mins, 60)
        ampm = "AM" if h < 12 else "PM"
        return f"{h % 12 or 12}:{m:02d} {ampm}"

    ordered = sorted(slots, key=lambda s: (s["start_min"], s["day"]))
    days = "/".join(s["day"].capitalize() for s in ordered)
    start = _hm(ordered[0]["start_min"])
    end = _hm(ordered[0]["end_min"])
    return f"{days} {start}–{end}"


def _pref_summary(excluded_days: set[str], min_start_hour: int) -> str:
    """Human-readable summary of the student's time preferences (e.g. 'no 9:00 AM, no Fri')."""
    parts: list[str] = []
    if min_start_hour > 0:
        h12 = min_start_hour % 12 or 12
        ampm = "AM" if min_start_hour < 12 else "PM"
        parts.append(f"no classes before {h12}:00 {ampm}")
    if excluded_days:
        names = {"mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu", "fri": "Fri"}
        parts.append("no " + "/".join(names.get(d, d.capitalize()) for d in sorted(excluded_days)))
    return ", ".join(parts)


def _format_plan(plan: Plan, catalog: dict[str, Course]) -> str:
    lines = [f"**{plan.name}** (engine-verified ✓)"]
    for pt in plan.terms:
        credits = sum(float(catalog[c].credits) for c in pt.course_codes if c in catalog)
        lines.append(f"{pt.term.value.title()} {pt.year} — {credits:.0f} credits:")
        for code in pt.course_codes:
            name = catalog[code].name if code in catalog else code
            cr = float(catalog[code].credits) if code in catalog else 0
            lines.append(f"  • {code}: {name} ({cr:.0f} cr)")
    return "\n".join(lines)
