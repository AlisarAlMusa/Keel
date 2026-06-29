"""Plan generation and graduation-plan tools.

Phase 3 (complete):
  propose_plan   — multi-candidate, risk-scored, workload-banded, LLM-ranked.
  simulate_whatif — engine re-audits a modified assumption; LLM explains.
  load_grad_plan — load the single active saved graduation plan.
  swap_grad_plan_course — edit the active graduation plan with full verification.
  delete_grad_plan — archive the active graduation plan.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from keel.agent.identity import resolve_identity
from keel.agent.plan_channel import emit_plans
from keel.domain.engine.audit import audit
from keel.domain.engine.contracts import Plan, PlanMeta, PlanTerm
from keel.domain.engine.planner import greedy_plan
from keel.domain.engine.sections import find_conflict_free_combinations
from keel.domain.engine.verifier import verify
from keel.domain.engine.workload import compute_workload
from keel.domain.models import Course, DayOfWeek, Section, Term, TimeSlot, TranscriptEntry
from keel.domain.schemas import ToolError
from keel.infra.database.session import tenant_session
from keel.logging import get_logger
from keel.services.grad_plans import (
    build_grad_plan_card,
    delete_active_grad_plan,
    load_active_grad_plan,
    requirement_label,
    swap_active_grad_plan_course,
)

from ._deps import AgentDeps
from .advising import _build_engine_objects, _load_student_data

_log = get_logger(__name__)

_PROMPT_VERSION = "v2"
_MAX_CANDIDATES = 3
_MAX_REPAIR_ATTEMPTS = 3


def _extract_llm_text(content: Any) -> str:
    """Extract plain text from LLM content that may be a list of blocks (Gemini)."""
    if isinstance(content, list):
        parts = [
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
            if not isinstance(block, dict) or block.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return str(content) if content else ""


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


# ---------------------------------------------------------------------------
# Tool input schemas
# ---------------------------------------------------------------------------


class ProposePlanInput(BaseModel):
    """Input for propose_plan tool."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    start_term: str = Field(description="Term to plan for: 'fall', 'spring', or 'summer'.")
    start_year: int = Field(description="Calendar year of the term, e.g. 2026.")
    excluded_days: list[str] = Field(
        default_factory=list,
        description=(
            "Days the student wants NO classes. Use short lowercase names: "
            "'mon','tue','wed','thu','fri'. E.g. ['fri'] to avoid Fridays."
        ),
    )
    min_start_hour: int = Field(
        default=0,
        description=(
            "Earliest class start hour the student accepts (24-h, 0 = no restriction). "
            "E.g. 9 means no classes before 9:00 AM (avoids 8 AM sections)."
        ),
    )


class SimulateWhatIfInput(BaseModel):
    """Input for simulate_whatif tool."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    start_term: str = Field(description="Current term for context: 'fall', 'spring', or 'summer'.")
    start_year: int = Field(description="Calendar year, e.g. 2026.")
    hypothetical_courses: list[str] = Field(
        description=(
            "Course codes to pretend the student has already passed, "
            "e.g. ['CS201', 'MATH201']. Engine re-audits with these added to transcript."
        )
    )


class LoadGradPlanInput(BaseModel):
    """Input for loading the active saved graduation plan."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")


class DeleteGradPlanInput(BaseModel):
    """Input for deleting/clearing the active saved graduation plan."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")


class SwapGradPlanCourseInput(BaseModel):
    """Input for editing the active saved graduation plan."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    remove_code: str = Field(description="Course code to remove, e.g. 'CS201'.")
    add_code: str = Field(description="Course code to add, e.g. 'CS301'.")


class ProposeSectionsInput(BaseModel):
    """Input for propose_sections — build conflict-free schedules for chosen courses."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    course_codes: list[str] = Field(
        description="Course codes the student is registering for, e.g. ['CS340','CS402']."
    )
    term: str = Field(description="Term to register in: 'fall', 'spring', or 'summer'.")
    year: int = Field(description="Calendar year of the term, e.g. 2026.")
    excluded_days: list[str] = Field(
        default_factory=list,
        description="Days with NO classes, lowercase short names: 'mon'..'fri'. E.g. ['fri'].",
    )
    min_start_hour: int = Field(
        default=0,
        description="Earliest acceptable start hour (24-h, 0 = no limit). 9 = no 8 AM sections.",
    )


class PlanGraduationInput(BaseModel):
    """Input for plan_graduation — the FULL term-by-term path to graduation."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    start_term: str = Field(
        description="Term to start the path from: 'fall', 'spring', or 'summer' "
        "(use the student's current term)."
    )
    start_year: int = Field(
        description="Calendar year to start from, e.g. 2026 (use the student's current year)."
    )
    prefer_courses: list[str] = Field(
        default_factory=list,
        description=(
            "Optional elective course codes to PRIORITISE when building the plan — e.g. the "
            "courses you just recommended for a stated career goal ('I want to be an AI "
            "engineer'). Pass them so the graduation plan visibly reflects that goal; the "
            "engine still only schedules eligible, verifier-valid courses."
        ),
    )


# ---------------------------------------------------------------------------
# Section formatting helpers (deterministic — no LLM, no I/O)
# ---------------------------------------------------------------------------


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


def _section_meets_prefs(
    slots: list[dict[str, Any]], excluded_days: set[str], min_start_min: int
) -> bool:
    """True if a section violates no hard time preference (day / earliest-start)."""
    days = {str(s["day"]).lower() for s in slots}
    if days & excluded_days:
        return False
    earliest = min((int(s["start_min"]) for s in slots), default=0)
    return earliest >= min_start_min


def _row_to_section(row: dict[str, Any], term: Term, year: int, tenant_id: UUID) -> Section:
    """Build an engine Section domain object from a raw sections row."""
    slots = tuple(
        TimeSlot(
            day=DayOfWeek(str(s["day"]).lower()),
            start_min=int(s["start_min"]),
            end_min=int(s["end_min"]),
        )
        for s in (row["slots"] or [])
    )
    sec_id = row["id"] if isinstance(row["id"], UUID) else UUID(str(row["id"]))
    return Section(
        tenant_id=tenant_id,
        id=sec_id,
        course_code=row["course_code"],
        term=term,
        year=year,
        slots=slots,
        capacity=int(row["capacity"]),
        enrolled=int(row["enrolled"]),
    )


def _sections_conflict(a: Section, b: Section) -> bool:
    """True if any meeting slot of section a overlaps any meeting slot of section b."""
    return any(ta.overlaps(tb) for ta in a.slots for tb in b.slots)


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


def _build_grad_card(
    plan: Plan,
    catalog: dict[str, Course],
    *,
    program: Any | None = None,
    card_id: str,
    label: str,
    blurb: str,
) -> dict[str, Any]:
    """Convert a verifier-valid multi-term Plan into a widget grad-plan card."""
    return build_grad_plan_card(
        plan,
        catalog,
        program,
        card_id=card_id,
        label=label,
        blurb=blurb,
    )


async def _llm_propose_grad_paths(
    *,
    llm: Any,
    courses: list[str],
    catalog: dict[str, Course],
    graph: Any,
    start_term: Term,
    start_year: int,
) -> list[dict[str, Any]]:
    """LLM arranges the full remaining course set into 2–3 goal-paced multi-term paths.

    The engine supplies WHICH courses must be taken (the greedy closure); the LLM decides
    HOW to pace them across terms (the soft/generative part). Each path is engine-validated
    by the caller — this only proposes.
    """
    desc: list[str] = []
    for code in courses:
        c = catalog.get(code)
        if not c:
            continue
        prereqs = sorted(graph.direct_prereqs(code))
        offered = "/".join(sorted(t.value for t in c.offered_terms))
        desc.append(
            f"  {code}: {c.credits}cr, difficulty {c.difficulty}/5, offered {offered}, "
            f"prereqs: {', '.join(prereqs) or 'none'}"
        )
    total_credits = sum(catalog[c].credits for c in courses if c in catalog)
    prompt = (
        f"Arrange ALL of these remaining courses into a term-by-term path to graduation, "
        f"starting {start_term.value.title()} {start_year}:\n" + "\n".join(desc) + "\n\n"
        f"There are {total_credits} credits total to schedule.\n\n"
        "RULES (hard):\n"
        "- A course's prerequisites must be in an EARLIER term than the course.\n"
        "- Schedule a course only in a term it is offered (fall/spring).\n"
        "- Never exceed 18 credits in a term.\n"
        "- Terms run consecutively: Fall <Y> → Spring <Y+1> → Fall <Y+1> → …\n"
        "- Include EVERY listed course exactly once.\n\n"
        "LEVELING (very important for a good plan):\n"
        f"- Spread credits EVENLY across terms. Aim each term close to {total_credits} ÷ "
        "(number of terms) — do NOT front-load one heavy term and leave tiny tail terms.\n"
        "- Avoid terms with fewer than 9 credits unless it is the final term or prereq "
        "chains genuinely force it. A trailing 3-credit term after several full terms is a "
        "bad plan.\n"
        "- Use the FEWEST terms that keep the load even (prefer fuller, balanced terms over "
        "many half-empty ones).\n\n"
        f"Produce {_MAX_CANDIDATES} labelled variants that differ by pace (each still "
        "evenly leveled within itself):\n"
        "  'Fastest'  — ~16–18 credits/term, fewest semesters\n"
        "  'Balanced' — ~14 credits/term, steady\n"
        "  'Lighter'  — ~11–12 credits/term, more semesters\n\n"
        'Respond with ONLY a JSON array:\n'
        '[{"label":"Balanced","terms":[{"term":"fall","year":2026,"courses":["CS101"]},'
        '{"term":"spring","year":2027,"courses":["CS102"]}]}]'
    )
    try:
        result = await llm.ainvoke(
            [
                SystemMessage(
                    content=f"You are a graduation planner. prompt_version={_PROMPT_VERSION}"
                ),
                HumanMessage(content=prompt),
            ]
        )
        text = _extract_llm_text(result.content).strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()
        try:
            paths = json.loads(text)
        except json.JSONDecodeError:
            import ast

            paths = ast.literal_eval(text)
        if isinstance(paths, list):
            return paths[:_MAX_CANDIDATES]
    except Exception as exc:  # noqa: BLE001
        _log.warning("tool.plan_graduation.llm_propose_failed", error=str(exc))
    return []


def _validate_grad_path(
    *,
    path: dict[str, Any],
    required: set[str],
    catalog: dict[str, Course],
    graph: Any,
    transcript: list[TranscriptEntry],
    student_id: str,
    tenant_id: str,
    program: Any,
) -> Plan | None:
    """Engine-validate an LLM-proposed multi-term path. Returns a Plan or None.

    Cumulative checks per term (the engine's rules, not the LLM's word): every course is
    offered that term, its prerequisites are satisfied by earlier terms + transcript, the
    term is within the credit cap, and the path covers ALL required courses exactly.
    """
    passed: set[str] = {e.course_code for e in transcript if e.passed}
    acc = set(passed)
    scheduled: set[str] = set()
    plan_terms: list[PlanTerm] = []
    for t in path.get("terms", []):
        try:
            term_enum = Term(str(t.get("term", "")).lower())
            year = int(t.get("year"))
        except (ValueError, TypeError):
            return None
        # Drop any course the LLM listed that the student has ALREADY passed (re-planning a
        # passed course is a "repeat" the save-time verifier rejects), AND de-duplicate within
        # the term (the LLM sometimes lists the same course twice in one term — e.g. PHYS201L —
        # which must never reach the card or its save). dict.fromkeys preserves order.
        codes = list(
            dict.fromkeys(
                c for c in (t.get("courses") or []) if c in catalog and c not in passed
            )
        )
        if sum(catalog[c].credits for c in codes) > 18:
            return None
        for code in codes:
            if term_enum not in catalog[code].offered_terms:
                return None
            if not graph.prereqs_satisfied(code, frozenset(acc)):
                return None
            if code in scheduled:
                return None  # duplicate
        plan_terms.append(PlanTerm(term=term_enum, year=year, course_codes=codes))
        acc |= set(codes)
        scheduled |= set(codes)

    if not plan_terms or not required.issubset(scheduled):
        return None  # incomplete — doesn't cover every required course
    return Plan(
        plan_id=uuid4(),
        tenant_id=UUID(tenant_id),
        student_id=UUID(student_id),
        program_id=program.program_id,
        name="grad",
        version=1,
        active=False,
        terms=plan_terms,
        meta=PlanMeta(generated_by="llm", created_at=datetime.utcnow()),
    )


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def make_planning_tools(deps: AgentDeps) -> list[Any]:
    """Return planning tools closed over deps."""

    @tool(args_schema=ProposePlanInput)
    async def propose_plan(
        student_id: str,
        tenant_id: str,
        start_term: str,
        start_year: int,
        excluded_days: list[str] | None = None,
        min_start_hour: int = 0,
    ) -> str:
        """Build up to 3 feasible, risk-scored, workload-banded course plans.
        Each plan is engine-verified before risk is scored.
        Plans are LLM-ranked by feasibility + risk + workload.
        Use audit_degree first, then propose_plan, then predict_risk to score.
        Only returns plans that pass ALL hard engine constraints.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        try:
            term = Term(start_term.lower())
        except ValueError:
            return ToolError(
                error=f"Invalid term '{start_term}'. Use: fall, spring, summer.",
                retryable=False,
                category="validation",
            ).model_dump_json()

        try:
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as _db:
                data = await _load_student_data(_db, student_id, tenant_id)
            if not data:
                return ToolError(
                    error=f"Student {student_id} not found.",
                    retryable=False,
                    category="validation",
                ).model_dump_json()

            if data["student"].get("has_hold"):
                reason = data["student"].get("hold_reason") or "unknown"
                return ToolError(
                    error=f"Student has an active hold ({reason}). Resolve before planning.",
                    retryable=False,
                    category="validation",
                ).model_dump_json()

            # Anchor planning to the student's ACTUAL current term/year (authoritative,
            # from the record) so we never plan a past/empty term and the term is
            # consistent across students — same convention as plan_graduation.
            stu = data["student"]
            if stu.get("current_term"):
                try:
                    term = Term(str(stu["current_term"]).lower())
                except ValueError:
                    pass
            if stu.get("current_year"):
                start_year = int(stu["current_year"])
            # Keep the string form in sync — later section queries/cards use start_term.
            start_term = term.value

            transcript, catalog, graph, coreqs, program = _build_engine_objects(
                data, term, start_year
            )
            if program is None:
                return ToolError(
                    error="Student has no program — cannot propose a plan.",
                    retryable=False,
                    category="validation",
                ).model_dump_json()

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
                    "No courses are currently eligible. You may have completed all requirements "
                    "or there may be prerequisite gaps — please consult your advisor."
                )

            # Ask LLM for up to 3 candidates (balanced / graduation-focused / lighter).
            raw_candidates = await _llm_propose_multi(
                llm=deps.llm_agent,
                eligible=audit_result.eligible_now,
                catalog=catalog,
                term=term,
                year=start_year,
                student_id=student_id,
                tenant_id=tenant_id,
                program=program,
            )

            # Validate each candidate; repair if needed; greedy fallback ensures ≥1.
            valid_candidates: list[tuple[Plan, str, str]] = []  # (plan, workload_band, label)

            for raw in raw_candidates:
                plan = await _validate_with_repair(
                    proposed=raw,
                    catalog=catalog,
                    graph=graph,
                    transcript=transcript,
                    coreqs=coreqs,
                    term=term,
                    year=start_year,
                    student_id=student_id,
                    tenant_id=tenant_id,
                    program=program,
                    llm=deps.llm_agent,
                )
                if plan is not None:
                    courses = [catalog[c] for c in plan.terms[0].course_codes if c in catalog]
                    _, band = compute_workload(courses)
                    valid_candidates.append((plan, band.value, raw.get("label", "balanced")))

            # Greedy fallback — try full multi-year plan first, then single-term.
            if not valid_candidates:
                _log.info("tool.propose_plan.greedy_fallback")
                fb = greedy_plan(
                    transcript=transcript,
                    program=program,
                    graph=graph,
                    catalog=catalog,
                    corequisites=coreqs,
                    start_term=term,
                    start_year=start_year,
                    student_id_hint=student_id,
                )
                if fb and fb.terms:
                    courses = [catalog[c] for c in fb.terms[0].course_codes if c in catalog]
                    _, band = compute_workload(courses)
                    valid_candidates.append((fb, band.value, "balanced"))

            # Last resort: pick eligible courses directly for just this term.
            if not valid_candidates and audit_result.eligible_now:
                _log.info("tool.propose_plan.single_term_fallback")
                codes = audit_result.eligible_now[:5]
                fallback_plan = _make_plan(codes, term, start_year, student_id, tenant_id, program)
                violations = verify(
                    plan=fallback_plan,
                    catalog=catalog,
                    graph=graph,
                    transcript=transcript,
                    corequisites=coreqs,
                    current_term=term,
                    current_year=start_year,
                )
                if not violations:
                    courses = [catalog[c] for c in codes if c in catalog]
                    _, band = compute_workload(courses)
                    valid_candidates.append((fallback_plan, band.value, "balanced"))

            if not valid_candidates:
                return (
                    "I was unable to build a valid plan. This may be due to prerequisite gaps "
                    "or no eligible courses this term. Please speak with your advisor."
                )

            # Score risk on valid candidates only (spec §3.3).
            scored: list[dict[str, Any]] = []
            for plan, workload_band, label in valid_candidates:
                risk_summary = "Risk prediction unavailable."
                if deps.model_client is not None:
                    from keel.domain.engine.risk_inputs import score_plan_term

                    _, vector = score_plan_term(audit_result, plan.terms[0], catalog)
                    pred = await deps.model_client.predict_grad_risk(vector.tolist())
                    if pred:
                        risk_summary = f"{pred.label} ({pred.score:.0%})"

                scored.append(
                    {
                        "plan": plan,
                        "workload": workload_band,
                        "label": label,
                        "risk": risk_summary,
                    }
                )

            # LLM ranks + explains using feasibility + risk + workload.
            ranking = await _llm_rank(
                llm=deps.llm_agent,
                candidates=scored,
                catalog=catalog,
                audit_result=audit_result,
            )

            # --- Registrability check (always, regardless of preferences) ---
            # A plan can be academically valid yet contain a course with no OPEN section
            # in the target term (verify() checks prereqs/credits/offering, not live
            # seats). Flag those so the plan is honest about what can actually be
            # registered now — keep the course, don't silently drop it.
            if scored and scored[0]["plan"].terms:
                top_codes = list(scored[0]["plan"].terms[0].course_codes)
                if top_codes:
                    try:
                        async with tenant_session(deps.session_factory, UUID(tenant_id)) as _db:
                            rows = await _db.execute(
                                sa.text(
                                    "SELECT course_code, "
                                    "bool_or(enrolled < capacity) AS has_open "
                                    "FROM sections "
                                    "WHERE tenant_id = :tid AND term = :term AND year = :yr "
                                    "AND course_code = ANY(:codes) GROUP BY course_code"
                                ),
                                {
                                    "tid": tenant_id,
                                    "term": start_term.lower(),
                                    "yr": start_year,
                                    "codes": top_codes,
                                },
                            )
                            seen_codes: dict[str, bool] = {r[0]: bool(r[1]) for r in rows}
                        # FULL = course has sections this term but none open → waitlist.
                        # NOT OFFERED = course has no section this term at all → another term.
                        full = [c for c in top_codes if c in seen_codes and not seen_codes[c]]
                        not_offered = [c for c in top_codes if c not in seen_codes]
                        notes: list[str] = []
                        if full:
                            notes.append(
                                f"all sections full for {', '.join(full)} — you can join the "
                                "waitlist for these"
                            )
                        if not_offered:
                            verb = "is" if len(not_offered) == 1 else "are"
                            notes.append(
                                f"{', '.join(not_offered)} {verb} not offered in "
                                f"{start_term.title()} {start_year} — plan these in a term "
                                "when they are offered (no waitlist applies)"
                            )
                        if notes:
                            ranking += "\n\n⚠ **Heads up:** " + "; ".join(notes) + "."
                    except Exception as exc:  # noqa: BLE001
                        _log.warning(
                            "tool.propose_plan.registrability_check_failed", error=str(exc)
                        )

            # --- Section time preference check ---
            # Query sections for the first candidate's courses and report availability
            # filtered by the student's time preferences (excluded_days, min_start_hour).
            ex_days = {d.lower() for d in (excluded_days or [])}
            min_min = (min_start_hour or 0) * 60  # convert hour → minutes-since-midnight

            if scored and (ex_days or min_min > 0):
                first_codes = (
                    scored[0]["plan"].terms[0].course_codes if scored[0]["plan"].terms else []
                )
                section_notes: list[str] = []
                if first_codes:
                    try:
                        async with tenant_session(deps.session_factory, UUID(tenant_id)) as _db:
                            rows = await _db.execute(
                                sa.text(
                                    "SELECT course_code, slots FROM sections "
                                    "WHERE tenant_id = :tid AND term = :term AND year = :yr "
                                    "AND course_code = ANY(:codes) AND enrolled < capacity"
                                ),
                                {
                                    "tid": tenant_id,
                                    "term": start_term.lower(),
                                    "yr": start_year,
                                    "codes": list(first_codes),
                                },
                            )
                            sections_by_code: dict[str, list[list[dict[str, Any]]]] = {}
                            for code, slots in rows:
                                sections_by_code.setdefault(code, []).append(slots)

                        for code in first_codes:
                            slot_lists = sections_by_code.get(code, [])
                            ok = []
                            bad = []
                            for slots in slot_lists:
                                days = {s["day"] for s in slots}
                                earliest = min((s["start_min"] for s in slots), default=0)
                                violates_day = bool(days & ex_days)
                                violates_time = earliest < min_min
                                if violates_day or violates_time:
                                    bad.append(slots)
                                else:
                                    ok.append(slots)

                            def _fmt(slots: list[dict[str, Any]]) -> str:
                                parts = []
                                for s in sorted(slots, key=lambda x: x["start_min"]):
                                    h, m = divmod(s["start_min"], 60)
                                    ampm = "AM" if h < 12 else "PM"
                                    h12 = h % 12 or 12
                                    parts.append(f"{s['day'].capitalize()} {h12}:{m:02d}{ampm}")
                                return ", ".join(parts)

                            if ok:
                                section_notes.append(
                                    f"  {code}: {len(ok)} section(s) meeting"
                                    f" your schedule — e.g. {_fmt(ok[0])}"
                                )
                            elif bad:
                                section_notes.append(
                                    f"  {code}: only sections that conflict with your preferences "
                                    f"(e.g. {_fmt(bad[0])}). Consider swapping this course."
                                )
                            else:
                                section_notes.append(f"  {code}: no open sections this term.")

                    except Exception as exc:  # noqa: BLE001
                        _log.warning("tool.propose_plan.section_check_failed", error=str(exc))

                if section_notes:
                    pref_desc = []
                    if ex_days:
                        pref_desc.append("no " + "/".join(sorted(ex_days)) + " classes")
                    if min_min > 0:
                        h12 = min_start_hour % 12 or 12
                        ampm = "AM" if min_start_hour < 12 else "PM"
                        pref_desc.append(f"nothing before {h12}:00 {ampm}")
                    ranking += (
                        f"\n\n**Section availability (preference: {', '.join(pref_desc)}):**\n"
                        + "\n".join(section_notes)
                    )

            # G3: surface the structured candidates so the widget renders rich plan
            # cards (codes, credits, risk, workload) — not just the LLM's prose.
            emit_plans(
                _build_plan_cards(
                    scored,
                    catalog,
                    start_term=start_term,
                    start_year=start_year,
                    program=program,
                )
            )

            _log.info(
                "tool.propose_plan.done",
                student_id=student_id,
                candidate_count=len(scored),
                tenant_id=tenant_id,
            )
            return ranking

        except Exception as exc:
            _log.error("tool.propose_plan.error", error=str(exc))
            return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()

    @tool(args_schema=SimulateWhatIfInput)
    async def simulate_whatif(
        student_id: str,
        tenant_id: str,
        start_term: str,
        start_year: int,
        hypothetical_courses: list[str],
    ) -> str:
        """Simulate what-if: what would the degree audit look like if these courses
        were already completed? Read-only. Never presents an infeasible alternative as feasible.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        try:
            term = Term(start_term.lower())
        except ValueError:
            return ToolError(
                error=f"Invalid term '{start_term}'.", retryable=False, category="validation"
            ).model_dump_json()

        try:
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as _db:
                data = await _load_student_data(_db, student_id, tenant_id)
            if not data:
                return ToolError(
                    error=f"Student {student_id} not found.", retryable=False, category="validation"
                ).model_dump_json()

            transcript, catalog, graph, coreqs, program = _build_engine_objects(
                data, term, start_year
            )
            if program is None:
                return ToolError(
                    error="Student has no program.", retryable=False, category="validation"
                ).model_dump_json()

            # Build hypothetical transcript: add passed courses.
            from decimal import Decimal

            from keel.domain.models import TranscriptEntry

            hypo_transcript = list(transcript)
            tid_uuid = UUID(tenant_id)
            sid_uuid = UUID(student_id)
            for code in hypothetical_courses:
                if code in catalog and not any(t.course_code == code for t in hypo_transcript):
                    hypo_transcript.append(
                        TranscriptEntry(
                            tenant_id=tid_uuid,
                            student_id=sid_uuid,
                            course_code=code,
                            grade=Decimal("3.0"),
                            passed=True,
                            term=term,
                            year=start_year - 1,
                        )
                    )

            hypo_audit = audit(
                transcript=hypo_transcript,
                program=program,
                graph=graph,
                catalog=catalog,
                current_term=term,
                current_year=start_year,
            )

            # LLM explains the delta.
            hypo_eligible = ", ".join(hypo_audit.eligible_now[:10]) or "none"
            prompt = (
                f"What-if simulation: if the student had completed {hypothetical_courses}, "
                f"they would have {hypo_audit.credits_completed:.0f} credits "
                f"({hypo_audit.pct_complete:.0%} complete). "
                f"New eligible courses: {hypo_eligible}. "
                "Explain the impact in 2-3 sentences. This is a simulation — not a real change."
            )
            result = await deps.llm_agent.ainvoke(
                [
                    SystemMessage(content="You are an academic advisor."),
                    HumanMessage(content=prompt),
                ]
            )
            return f"**What-if simulation (read-only):**\n{_extract_llm_text(result.content)}"

        except Exception as exc:
            _log.error("tool.simulate_whatif.error", error=str(exc))
            return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()

    @tool(args_schema=LoadGradPlanInput)
    async def load_grad_plan(student_id: str, tenant_id: str) -> str:
        """Load the student's active saved graduation plan.

        Use this for "show me my grad plan" / "give me my graduation plan".
        The student does not need to know a plan UUID.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        try:
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as session:
                result = await load_active_grad_plan(
                    session,
                    tenant_id=UUID(tenant_id),
                    student_id=UUID(student_id),
                )
            if result is None:
                return "You do not have a saved graduation plan yet."
            if result.card:
                emit_plans([result.card])
            return result.message
        except Exception as exc:
            _log.error("tool.load_grad_plan.error", error=str(exc))
            return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()

    @tool(args_schema=DeleteGradPlanInput)
    async def delete_grad_plan(student_id: str, tenant_id: str) -> str:
        """Delete/clear the active saved graduation plan.

        This archives the saved plan record so audit history remains intact.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        try:
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as session:
                result = await delete_active_grad_plan(
                    session,
                    tenant_id=UUID(tenant_id),
                    student_id=UUID(student_id),
                )
            return result.message
        except Exception as exc:
            _log.error("tool.delete_grad_plan.error", error=str(exc))
            return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()

    @tool(args_schema=SwapGradPlanCourseInput)
    async def swap_grad_plan_course(
        student_id: str,
        tenant_id: str,
        remove_code: str,
        add_code: str,
    ) -> str:
        """Swap one course in the active saved graduation plan and re-verify it."""
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        try:
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as session:
                result = await swap_active_grad_plan_course(
                    session,
                    tenant_id=UUID(tenant_id),
                    student_id=UUID(student_id),
                    remove_code=remove_code,
                    add_code=add_code,
                )
            if result.card:
                emit_plans([result.card])
            return result.message
        except ValueError as exc:
            return ToolError(
                error=str(exc), retryable=False, category="validation"
            ).model_dump_json()
        except Exception as exc:
            _log.error("tool.swap_grad_plan_course.error", error=str(exc))
            return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()

    @tool(args_schema=ProposeSectionsInput)
    async def propose_sections(
        student_id: str,
        tenant_id: str,
        course_codes: list[str],
        term: str,
        year: int,
        excluded_days: list[str] | None = None,
        min_start_hour: int = 0,
    ) -> str:
        """Build 2-3 ready-to-register SCHEDULES for an already-agreed set of courses.

        Call this ONLY after the student has settled on WHICH courses to take (step 2 of
        registration; step 1 is agreeing on the courses). The engine returns complete,
        conflict-free schedules — one open section per course — that ALL respect the
        student's time preferences (no 8am, no Fridays). It does NOT dump every section;
        it hands you a few clean options.

        You then recommend ONE schedule in a SHORT sentence (the widget shows the options
        as cards and the student picks). To enroll, call stage_enrollment with that
        schedule's section_ids (one per course). Courses with no preference-fitting open
        section are reported separately with the correct remedy — FULL → offer the waitlist;
        NOT OFFERED this term → suggest another term (never a waitlist). Read-only.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        if not course_codes:
            return ToolError(
                error="No course codes provided.", retryable=False, category="validation"
            ).model_dump_json()
        try:
            term_enum = Term(term.lower())
        except ValueError:
            return ToolError(
                error=f"Invalid term '{term}'. Use: fall, spring, summer.",
                retryable=False,
                category="validation",
            ).model_dump_json()

        ex_days = {d.lower() for d in (excluded_days or [])}
        min_min = (min_start_hour or 0) * 60
        has_prefs = bool(ex_days or min_min)
        tid = UUID(tenant_id)
        try:
            async with tenant_session(deps.session_factory, tid) as session:
                rows = await session.execute(
                    sa.text(
                        "SELECT id, course_code, slots, instructor, capacity, enrolled "
                        "FROM sections WHERE tenant_id = :tid AND term = :term "
                        "AND year = :yr AND course_code = ANY(:codes) "
                        "ORDER BY course_code, created_at"
                    ),
                    {
                        "tid": tenant_id,
                        "term": term.lower(),
                        "yr": year,
                        "codes": list(course_codes),
                    },
                )
                by_code: dict[str, list[dict[str, Any]]] = {}
                for r in rows.mappings():
                    by_code.setdefault(r["course_code"], []).append(dict(r))
                data = await _load_student_data(session, student_id, tenant_id)

            if not data:
                return ToolError(
                    error=f"Student {student_id} not found.",
                    retryable=False,
                    category="validation",
                ).model_dump_json()
            _transcript, catalog, _graph, _coreqs, program = _build_engine_objects(
                data, term_enum, year
            )

            # Partition each course into preference-fitting open sections vs unavailable
            # (with the WHY → so the agent offers the right remedy).
            fitting: dict[str, list[Section]] = {}
            meta: dict[str, dict[str, Any]] = {}  # section_id → {when, instructor, seats}
            unavailable: list[dict[str, str]] = []
            for code in course_codes:
                secs = by_code.get(code, [])
                open_secs = [s for s in secs if int(s["enrolled"]) < int(s["capacity"])]
                pref_secs = [
                    s
                    for s in open_secs
                    if _section_meets_prefs(list(s["slots"] or []), ex_days, min_min)
                ]
                if pref_secs:
                    objs: list[Section] = []
                    for s in pref_secs:
                        objs.append(_row_to_section(s, term_enum, year, tid))
                        meta[str(s["id"])] = {
                            "when": _fmt_section_slots(list(s["slots"] or [])),
                            "instructor": s["instructor"] or "TBA",
                            "seats": int(s["capacity"]) - int(s["enrolled"]),
                        }
                    fitting[code] = objs
                    continue
                if not secs:
                    unavailable.append(
                        {"code": code, "reason": "not offered this term", "remedy": "another term"}
                    )
                elif not open_secs:
                    unavailable.append(
                        {"code": code, "reason": "all sections full", "remedy": "waitlist"}
                    )
                else:
                    unavailable.append(
                        {
                            "code": code,
                            "reason": "no section matches your time preferences",
                            "remedy": "relax preferences",
                        }
                    )

            # Engine builds complete, conflict-free combinations across the fitting sections.
            combos = find_conflict_free_combinations(fitting) if fitting else []
            # Fallback: if the fitting sections can't all coexist conflict-free, greedily
            # place as many as possible and report the ones that had to be dropped.
            if fitting and not combos:
                picked: dict[str, Section] = {}
                chosen: list[Section] = []
                for code in [c for c in course_codes if c in fitting]:
                    placed = False
                    for sec in fitting[code]:
                        if not any(_sections_conflict(sec, c) for c in chosen):
                            picked[code] = sec
                            chosen.append(sec)
                            placed = True
                            break
                    if not placed:
                        unavailable.append(
                            {
                                "code": code,
                                "reason": "time conflict with the rest of your schedule",
                                "remedy": "drop or swap a course",
                            }
                        )
                if picked:
                    combos = [picked]

            # Dedupe and cap at 3 schedule bundles.
            schedules: list[dict[str, Any]] = []
            seen: set[tuple[str, ...]] = set()
            for combo in combos:
                sig = tuple(sorted(str(sec.id) for sec in combo.values()))
                if sig in seen:
                    continue
                seen.add(sig)
                items: list[dict[str, Any]] = []
                for code in course_codes:
                    chosen_sec = combo.get(code)
                    if chosen_sec is None:
                        continue
                    m = meta[str(chosen_sec.id)]
                    items.append(
                        {
                            "code": code,
                            "title": catalog[code].name if code in catalog else code,
                            "credits": int(catalog[code].credits) if code in catalog else 0,
                            "requirement": requirement_label(code, program),
                            "section_id": str(chosen_sec.id),
                            "when": m["when"],
                            "instructor": m["instructor"],
                            "seats": m["seats"],
                        }
                    )
                schedules.append(
                    {
                        "id": f"sched-{len(schedules) + 1}",
                        "label": f"Option {len(schedules) + 1}",
                        "items": items,
                    }
                )
                if len(schedules) >= 3:
                    break

            pref_summary = _pref_summary(ex_days, min_start_hour) if has_prefs else None

            emit_plans(
                [
                    {
                        "kind": "sections",
                        "id": "sections-1",
                        "term": f"{term.title()} {year}",
                        "hasPrefs": has_prefs,
                        "prefSummary": pref_summary,
                        "schedules": schedules,
                        "unavailable": unavailable,
                    }
                ]
            )

            # Compact text for the agent — it recommends ONE; the card shows them all.
            header = f"Section schedules for {term.title()} {year}"
            if pref_summary:
                header += f" (preferences: {pref_summary})"
            lines: list[str] = [header + ":"]
            if schedules:
                for sch in schedules:
                    parts = [
                        f"{it['code']} {it['when']} · {it['instructor']} "
                        f"[section_id: {it['section_id']}]"
                        for it in sch["items"]
                    ]
                    lines.append(f"- {sch['label']}: " + "; ".join(parts))
                lines.append(
                    "Recommend ONE option to the student. To enroll, call stage_enrollment "
                    "with that option's section_ids (one per course)."
                )
            else:
                lines.append(
                    "No conflict-free, preference-fitting schedule could be built "
                    "for these courses."
                )
            if unavailable:
                lines.append(
                    "Unavailable: "
                    + "; ".join(
                        f"{u['code']} — {u['reason']} (remedy: {u['remedy']})" for u in unavailable
                    )
                )

            _log.info(
                "tool.propose_sections.done",
                student_id=student_id,
                courses=len(course_codes),
                schedules=len(schedules),
                unavailable=len(unavailable),
                tenant_id=tenant_id,
            )
            return "\n".join(lines)

        except Exception as exc:
            _log.error("tool.propose_sections.error", error=str(exc))
            return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()

    @tool(args_schema=PlanGraduationInput)
    async def plan_graduation(
        student_id: str,
        tenant_id: str,
        start_term: str,
        start_year: int,
        prefer_courses: list[str] | None = None,
    ) -> str:
        """Map the FULL term-by-term path from now to graduation — not just next term.
        Use when the student asks for their whole degree plan, "how many semesters left",
        or "map my path to graduation". The engine builds a verifier-valid multi-term path
        (prerequisite order, offering terms, and credit caps respected end-to-end), and
        this reports each term's courses + workload band, the heaviest term, and the
        projected graduation term. Read-only — proposes a path, writes nothing.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        try:
            term = Term(start_term.lower())
        except ValueError:
            return ToolError(
                error=f"Invalid term '{start_term}'. Use: fall, spring, summer.",
                retryable=False,
                category="validation",
            ).model_dump_json()
        try:
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as _db:
                data = await _load_student_data(_db, student_id, tenant_id)
            if not data:
                return ToolError(
                    error=f"Student {student_id} not found.",
                    retryable=False,
                    category="validation",
                ).model_dump_json()
            if data["student"].get("has_hold"):
                reason = data["student"].get("hold_reason") or "unknown"
                return ToolError(
                    error=f"Student has an active hold ({reason}). Resolve it before planning.",
                    retryable=False,
                    category="validation",
                ).model_dump_json()

            # Anchor the path to the student's ACTUAL current term/year (authoritative,
            # from the record) — never a past term, regardless of what the LLM passed.
            stu = data["student"]
            if stu.get("current_term"):
                try:
                    term = Term(str(stu["current_term"]).lower())
                except ValueError:
                    pass
            if stu.get("current_year"):
                start_year = int(stu["current_year"])

            transcript, catalog, graph, coreqs, program = _build_engine_objects(
                data, term, start_year
            )
            if program is None:
                return ToolError(
                    error="Student has no program — cannot plan a path.",
                    retryable=False,
                    category="validation",
                ).model_dump_json()

            # Graduation paths: the LLM PROPOSES the pacing, the engine VERIFIES (mirrors
            # the planning loop). The engine first computes WHICH courses must be taken (the
            # greedy closure incl. prereqs); the LLM arranges them into 2–3 goal-paced
            # multi-term paths; each is engine-validated. Greedy is the FALLBACK only.
            blurbs = {
                "Fastest": "Heaviest terms, finish soonest",
                "Balanced": "A steady, manageable load",
                "Lighter": "Fewer courses per term",
            }
            # Career-aligned electives to prioritise (capped + catalog-checked, so a bad hint
            # can't distort the plan — the engine still only schedules valid courses).
            prefer_codes = frozenset(
                c.upper() for c in (prefer_courses or []) if c.upper() in catalog
            )
            base = greedy_plan(
                transcript=transcript,
                program=program,
                graph=graph,
                catalog=catalog,
                corequisites=coreqs,
                start_term=term,
                start_year=start_year,
                student_id_hint=student_id,
                prefer_codes=prefer_codes,
            )
            if base is None or not base.terms:
                return (
                    "I couldn't compute a path to graduation — there may be prerequisite gaps "
                    "or requirements I can't schedule. Please consult your advisor."
                )
            required = {c for pt in base.terms for c in pt.course_codes}

            cards: list[dict[str, Any]] = []
            seen: set[tuple[Any, ...]] = set()

            def _add_card(plan: Plan, label: str) -> None:
                sig = tuple(
                    (pt.term.value, pt.year, tuple(sorted(pt.course_codes))) for pt in plan.terms
                )
                if sig in seen:
                    return
                seen.add(sig)
                cards.append(
                    _build_grad_card(
                        plan,
                        catalog,
                        program=program,
                        card_id=f"grad-{len(cards) + 1}",
                        label=label,
                        blurb=blurbs.get(label, "Path to graduation"),
                    )
                )

            # LLM proposes pacing over the required set → engine validates each path.
            proposed = await _llm_propose_grad_paths(
                llm=deps.llm_agent,
                courses=sorted(required),
                catalog=catalog,
                graph=graph,
                start_term=term,
                start_year=start_year,
            )
            for cand in proposed:
                valid = _validate_grad_path(
                    path=cand,
                    required=required,
                    catalog=catalog,
                    graph=graph,
                    transcript=transcript,
                    student_id=student_id,
                    tenant_id=tenant_id,
                    program=program,
                )
                if valid is not None:
                    _add_card(valid, str(cand.get("label") or f"Option {len(cards) + 1}"))

            # Ensure the student gets at least 2 options: if the LLM yielded fewer engine-valid
            # paths, top up with greedy variants (each valid by construction; greedy is the
            # safety net, the LLM stays the primary proposer).
            if len(cards) < 2:
                _log.info("tool.plan_graduation.greedy_topup", llm_valid=len(cards))
                used = {c["label"] for c in cards}
                for label, cap in (("Balanced", 15), ("Fastest", 18), ("Lighter", 12)):
                    if len(cards) >= 3 or label in used:
                        continue
                    p = (
                        base
                        if cap == 15
                        else greedy_plan(
                            transcript=transcript,
                            program=program,
                            graph=graph,
                            catalog=catalog,
                            corequisites=coreqs,
                            start_term=term,
                            start_year=start_year,
                            credit_cap=cap,
                            student_id_hint=student_id,
                            prefer_codes=prefer_codes,
                        )
                    )
                    if p is not None and p.terms:
                        _add_card(p, label)

            if not cards:
                return (
                    "I couldn't compute a complete path to graduation automatically — there "
                    "may be prerequisite gaps or remaining requirements I can't schedule "
                    "within a reasonable horizon. Please consult your advisor."
                )

            # Near-term graduation risk (immediate term — same across variants).
            risk_note = ""
            if deps.model_client is not None:
                try:
                    from keel.domain.engine.risk_inputs import score_plan_term

                    audit_result = audit(
                        transcript=transcript,
                        program=program,
                        graph=graph,
                        catalog=catalog,
                        current_term=term,
                        current_year=start_year,
                    )
                    # Use the first variant's first term as the immediate-term proxy.
                    first_codes = [
                        str(c.get("code"))
                        for c in cards[0]["terms"][0]["courses"]
                        if isinstance(c, dict) and c.get("code")
                    ]
                    from keel.domain.engine.contracts import PlanTerm as _PT

                    pt0 = _PT(term=term, year=start_year, course_codes=first_codes)
                    _, vector = score_plan_term(audit_result, pt0, catalog)
                    pred = await deps.model_client.predict_grad_risk(vector.tolist())
                    if pred:
                        risk_note = f" Near-term graduation risk: {pred.label} ({pred.score:.0%})."
                except Exception as exc:  # noqa: BLE001
                    _log.warning("tool.plan_graduation.risk_failed", error=str(exc))

            # Surface the structured grad-plan cards to the widget.
            emit_plans(cards)

            opt_lines = [
                f"- **{c['label']}** — {c['termsToGrad']} terms, graduate {c['graduates']} "
                f"(heaviest: {c['heaviestTerm']})"
                for c in cards
            ]
            summary = (
                f"Here {'is' if len(cards) == 1 else 'are'} {len(cards)} path"
                f"{'' if len(cards) == 1 else 's'} to graduation — pick the one that fits:\n"
                + "\n".join(opt_lines)
                + risk_note
            )

            # Honesty check for a career-goal request: report which preferred courses actually
            # made it into the plan, and which could NOT (they aren't part of this program's
            # requirements). Prevents claiming an out-of-program course is "in the plan".
            if prefer_codes:
                planned = {
                    str(c.get("code"))
                    for card in cards
                    for t in card["terms"]
                    for c in t["courses"]
                    if isinstance(c, dict)
                }
                got = sorted(prefer_codes & planned)
                missed = sorted(prefer_codes - planned)
                if got:
                    summary += f"\n\nPrioritised toward your goal: {', '.join(got)}."
                if missed:
                    summary += (
                        f" Note: {', '.join(missed)} {'is' if len(missed) == 1 else 'are'} not "
                        "part of your current program's requirements, so "
                        f"{'it' if len(missed) == 1 else 'they'} could not be added — taking "
                        f"{'it' if len(missed) == 1 else 'them'} would require a major change. "
                        "Tell the student this honestly; do not claim a course is in the plan "
                        "when it is not."
                    )

            _log.info(
                "tool.plan_graduation.done",
                student_id=student_id,
                variants=len(cards),
                tenant_id=tenant_id,
            )
            return summary

        except Exception as exc:
            _log.error("tool.plan_graduation.error", error=str(exc))
            return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()

    return [
        propose_plan,
        plan_graduation,
        propose_sections,
        simulate_whatif,
        load_grad_plan,
        delete_grad_plan,
        swap_grad_plan_course,
    ]


# ---------------------------------------------------------------------------
# LLM helpers (internal)
# ---------------------------------------------------------------------------


async def _llm_propose_multi(
    *,
    llm: Any,
    eligible: list[str],
    catalog: dict[str, Course],
    term: Term,
    year: int,
    student_id: str,
    tenant_id: str,
    program: Any,
) -> list[dict[str, Any]]:
    """Ask the LLM to propose up to 3 labelled candidate plans."""
    eligible_desc = []
    for code in eligible[:40]:
        c = catalog.get(code)
        if c:
            eligible_desc.append(
                f"  {code}: {c.name} ({c.credits} cr, difficulty {c.difficulty}/5)"
            )

    prompt = (
        f"Plan {term.value.title()} {year} for a student in {program.program_id}.\n"
        f"Eligible courses (choose 3-5 per candidate, max 18 credits):\n"
        + "\n".join(eligible_desc)
        + f"\n\nPropose {_MAX_CANDIDATES} distinct candidates:\n"
        '  1. "balanced"          — spread of difficulty, ~12-15 credits\n'
        '  2. "graduation_focused" — prioritise remaining required courses\n'
        '  3. "lighter"            — lower workload, ≤12 credits\n'
        "\nRespond with ONLY a JSON array:\n"
        '[{"label":"balanced","courses":["CODE1","CODE2","CODE3"]},\n'
        ' {"label":"graduation_focused","courses":["CODE4","CODE5"]},\n'
        ' {"label":"lighter","courses":["CODE6"]}]'
    )

    try:
        result = await llm.ainvoke(
            [
                SystemMessage(
                    content=f"You are a course planning assistant. prompt_version={_PROMPT_VERSION}"
                ),
                HumanMessage(content=prompt),
            ]
        )
        text = _extract_llm_text(result.content).strip()
        # Strip markdown code fences if present
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()
        # Handle Python dict literals (single quotes) from weaker models
        try:
            candidates = json.loads(text)
        except json.JSONDecodeError:
            import ast

            candidates = ast.literal_eval(text)
        if isinstance(candidates, list):
            return candidates[:_MAX_CANDIDATES]
    except Exception as exc:
        _log.warning("tool.propose_plan.llm_multi_failed", error=str(exc))

    return []


async def _validate_with_repair(
    *,
    proposed: dict[str, Any],
    catalog: dict[str, Course],
    graph: Any,
    transcript: Any,
    coreqs: Any,
    term: Term,
    year: int,
    student_id: str,
    tenant_id: str,
    program: Any,
    llm: Any,
) -> Plan | None:
    """Validate a proposed candidate; repair from violations (≤ MAX_REPAIR_ATTEMPTS)."""
    codes = proposed.get("courses", [])
    if not codes:
        return None

    plan = _make_plan(codes, term, year, student_id, tenant_id, program)

    for attempt in range(1, _MAX_REPAIR_ATTEMPTS + 1):
        violations = verify(
            plan=plan,
            catalog=catalog,
            graph=graph,
            transcript=transcript,
            corequisites=coreqs,
            current_term=term,
            current_year=year,
        )
        if not violations:
            return plan
        if attempt == _MAX_REPAIR_ATTEMPTS:
            break

        # Ask LLM to repair.
        violation_text = "\n".join(f"  - {v.code}: {v.message}" for v in violations)
        eligible = [c for c in catalog if c not in [t.course_code for t in transcript if t.passed]]
        eligible_sample = ", ".join(eligible[:20])
        repair_prompt = (
            f"The plan {codes} has these violations:\n{violation_text}\n"
            f"Available alternatives: {eligible_sample}\n"
            'Respond with ONLY: {"courses": ["CODE1", "CODE2", "CODE3"]}'
        )
        try:
            r = await llm.ainvoke(
                [
                    SystemMessage(content="You are a course planning assistant."),
                    HumanMessage(content=repair_prompt),
                ]
            )
            text = _extract_llm_text(r.content).strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            text = text.strip()
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                import ast

                parsed = ast.literal_eval(text)
            new_codes = parsed.get("courses", [])
            if new_codes:
                codes = new_codes
                plan = _make_plan(codes, term, year, student_id, tenant_id, program)
        except Exception:
            break

    return None


def _make_plan(
    codes: list[str],
    term: Term,
    year: int,
    student_id: str,
    tenant_id: str,
    program: Any,
) -> Plan:
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


async def _llm_rank(
    *,
    llm: Any,
    candidates: list[dict[str, Any]],
    catalog: dict[str, Course],
    audit_result: Any,
) -> str:
    """Ask the LLM to rank validated candidates by feasibility + risk + workload."""
    if len(candidates) == 1:
        plan = candidates[0]["plan"]
        return (
            _format_plan(plan, catalog)
            + f"\n**Workload:** {candidates[0]['workload']}"
            + f"\n**Risk:** {candidates[0]['risk']}"
        )

    summaries = []
    for i, c in enumerate(candidates, 1):
        plan = c["plan"]
        term = plan.terms[0]
        courses = ", ".join(
            f"{code} ({catalog[code].credits if code in catalog else '?'} cr)"
            for code in term.course_codes
        )
        summaries.append(
            f"Option {i} ({c['label']}): {courses} | workload: {c['workload']} | risk: {c['risk']}"
        )

    prompt = (
        "Rank these verified course plans for a student from most to least recommended, "
        "considering feasibility, graduation risk, and workload balance.\n\n"
        + "\n".join(summaries)
        + "\n\nProvide a brief ranking with one sentence of explanation per option. "
        "Start with the recommended option. Be concise."
    )
    try:
        result = await llm.ainvoke(
            [
                SystemMessage(content="You are a course planning advisor. Be concise."),
                HumanMessage(content=prompt),
            ]
        )
        ranking_text = _extract_llm_text(result.content)
    except Exception:
        ranking_text = "Options ranked by risk (lower risk preferred)."

    # Format all plans with their scores.
    header = f"**{len(candidates)} Verified Plan(s) — Engine-Checked ✓**\n"
    lines = [header, ranking_text, ""]
    for c in candidates:
        lines.append(_format_plan(c["plan"], catalog))
        lines.append(f"  Workload: {c['workload']} | Risk: {c['risk']}\n")

    return "\n".join(lines)


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
