"""Enrollment/waitlist STAGE application services (spec section 2, stage side).

Extracted from the enrollment agent tools. Each use case validates and stages a
pending action via ActionsRepository.insert_pending; NONE writes an enrollment.
The actual writes live in services/actions/*. Behaviour unchanged.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from keel.domain.schemas import ToolError
from keel.infra.database.session import tenant_session
from keel.logging import get_logger
from keel.repositories.core import ActionsRepository
from keel.repositories.sections import SectionRepository
from keel.repositories.students import StudentRepository
from keel.repositories.waitlist import WaitlistRepository

_log = get_logger(__name__)


def _slots_conflict(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> bool:
    """True if any meeting slot in `a` overlaps in time with any slot in `b`."""
    for sa_ in a:
        for sb in b:
            if sa_.get("day") != sb.get("day"):
                continue
            if sa_["start_min"] < sb["end_min"] and sb["start_min"] < sa_["end_min"]:
                return True
    return False


def _slots_meet_prefs(
    slots: list[dict[str, Any]], excluded_days: set[str], min_start_min: int
) -> bool:
    """True if a section violates no hard time preference (avoided day / earliest start)."""
    days = {str(s["day"]).lower() for s in slots}
    if days & excluded_days:
        return False
    earliest = min((int(s["start_min"]) for s in slots), default=0)
    return earliest >= min_start_min


def _fmt_slots(slots: list[dict[str, Any]]) -> str:
    """Render meeting slots like 'Mon/Wed 9:00 AM–10:15 AM'."""
    if not slots:
        return "TBA"

    def _hm(mins: int) -> str:
        h, m = divmod(int(mins), 60)
        ampm = "AM" if h < 12 else "PM"
        return f"{h % 12 or 12}:{m:02d} {ampm}"

    ordered = sorted(slots, key=lambda s: (s["start_min"], s["day"]))
    days = "/".join(str(s["day"]).capitalize() for s in ordered)
    return f"{days} {_hm(ordered[0]['start_min'])}–{_hm(ordered[0]['end_min'])}"


async def _classify_unavailable(
    session: Any,
    *,
    tenant_id: str,
    course_codes: list[str],
    term: str,
    year: int,
) -> tuple[list[str], list[str]]:
    """Split unenrollable courses into (full, not_offered_this_term).

    FULL = the course HAS at least one section this term but none is open → waitlist.
    NOT_OFFERED = the course has NO section this term at all → suggest another term.
    """
    has_section = await SectionRepository(session, tenant_id).open_status_for_courses(
        term=term, year=year, codes=list(course_codes)
    )
    full: list[str] = []
    not_offered: list[str] = []
    for code in course_codes:
        if code in has_section:
            # Section rows exist this term but none open (any_open is False here).
            full.append(code)
        else:
            not_offered.append(code)
    return full, not_offered


async def _verify_chosen_sections(
    session: Any,
    *,
    tenant_id: str,
    section_ids: list[str],
    course_codes: list[str],
    term: str,
    year: int,
) -> tuple[list[dict[str, Any]], str | None]:
    """Engine-verify a set of LLM-chosen section UUIDs before staging.

    The LLM proposes sections (from propose_sections); the engine verifies — mirroring
    the plan propose→verify loop. Each section must: exist for this tenant/term/year,
    belong to one of the requested ``course_codes`` (so an injected id can't enroll the
    student in an unrelated course), be OPEN (enrolled < capacity), and the whole set
    must be pairwise time-conflict-free. Returns (chosen, None) on success, or
    ([], error_message) on the first failure for the LLM to repair.
    """
    try:
        ids = [str(UUID(s)) for s in section_ids]  # reject malformed ids early
    except ValueError:
        return [], "One or more section IDs are malformed."

    rows = await SectionRepository(session, tenant_id).by_ids(term=term, year=year, ids=ids)
    found: dict[str, dict[str, Any]] = {}
    for r in rows:
        found[str(r["id"])] = r

    requested = set(course_codes)
    chosen: list[dict[str, Any]] = []
    chosen_slots: list[list[dict[str, Any]]] = []
    for sid in ids:
        sec = found.get(sid)
        if sec is None:
            return [], f"Section {sid} was not found for {term.title()} {year}."
        if sec["course_code"] not in requested:
            return [], (
                f"Section {sid} is for {sec['course_code']}, which is not in the "
                "courses being enrolled. Choose a section for one of: "
                + ", ".join(sorted(requested))
                + "."
            )
        if int(sec["enrolled"]) >= int(sec["capacity"]):
            return [], (
                f"The chosen section for {sec['course_code']} is full. "
                "Pick another open section or offer the waitlist."
            )
        slots = list(sec["slots"] or [])
        if any(_slots_conflict(slots, prev) for prev in chosen_slots):
            return [], (
                f"The chosen section for {sec['course_code']} time-conflicts with another "
                "selected section. Pick a non-overlapping section."
            )
        chosen.append(
            {
                "section_id": sid,
                "course_code": sec["course_code"],
                "slots": slots,
                "instructor": sec["instructor"],
                "meets_prefs": True,  # the LLM chose it deliberately
            }
        )
        chosen_slots.append(slots)
    return chosen, None


async def _resolve_sections_for_courses(
    session: Any,
    *,
    tenant_id: str,
    course_codes: list[str],
    term: str,
    year: int,
    excluded_days: list[str] | None = None,
    min_start_hour: int = 0,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Resolve course codes → one open, conflict-free section per course, preferring
    sections that honour the student's time preferences.

    The engine — not the LLM — owns section selection. For each course we take the
    open sections (enrolled < capacity) for the term/year, rank pref-meeting sections
    first, and greedily pick the first that does not time-conflict with sections already
    chosen. Returns (chosen, unresolved) where each chosen entry is
    {section_id, course_code, slots, instructor, meets_prefs}.
    """
    ex_days = {d.lower() for d in (excluded_days or [])}
    min_min = (min_start_hour or 0) * 60
    rows = await SectionRepository(session, tenant_id).open_with_instructor_for_courses(
        term=term, year=year, codes=list(course_codes)
    )
    open_by_code: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        open_by_code.setdefault(row["course_code"], []).append(
            {
                "section_id": str(row["id"]),
                "course_code": row["course_code"],
                "slots": list(row["slots"] or []),
                "instructor": row["instructor"],
            }
        )

    chosen: list[dict[str, Any]] = []
    chosen_slots: list[list[dict[str, Any]]] = []
    unresolved: list[str] = []
    # Preserve the order the student listed the courses in.
    for code in course_codes:
        options = open_by_code.get(code, [])
        # Prefer sections that meet the time preferences; keep DB order within each tier.
        preferred = [o for o in options if _slots_meet_prefs(o["slots"], ex_days, min_min)]
        rest = [o for o in options if o not in preferred]
        picked: dict[str, Any] | None = None
        for opt in preferred + rest:
            if any(_slots_conflict(opt["slots"], prev) for prev in chosen_slots):
                continue
            picked = opt
            picked["meets_prefs"] = _slots_meet_prefs(opt["slots"], ex_days, min_min)
            chosen.append(picked)
            chosen_slots.append(opt["slots"])
            break
        if picked is None:
            unresolved.append(code)
    return chosen, unresolved


async def _resolve_section_for_course(
    session: Any,
    *,
    tenant_id: str,
    course_code: str,
    term: str | None,
    year: int | None,
) -> str | None:
    """Resolve a course code → one section UUID (for waitlist: the full section).

    Capacity is intentionally ignored — a waitlist target is a full section. When
    term/year are given they narrow the match; the demo seeds one section per course
    per tenant, so the course code alone is unambiguous. Returns None if not found.
    """
    return await SectionRepository(session, tenant_id).first_id_for_course(
        course_code=course_code, term=term, year=year
    )


async def _default_term_year(
    session: Any,
    *,
    tenant_id: str,
    student_id: str,
    term: str | None,
    year: int | None,
) -> tuple[str | None, int | None]:
    """Fill in missing term/year from the student's CURRENT registration term.

    Waitlisting (and listing the sections to waitlist) is for the term the student is
    registering in. Without this, an un-termed lookup spans every term and a course with
    a section in both fall and spring shows two same-instructor options — so a student can
    land on the wrong-term section, whose seat the registrar never frees.
    """
    if term and year is not None:
        return term, year
    r = await StudentRepository(session, tenant_id).get_current_term_year(str(student_id))
    if r is None:
        return term, year
    return (
        term or str(r["current_term"]),
        year if year is not None else int(r["current_year"]),
    )


async def _full_sections_for_course(
    session: Any,
    *,
    tenant_id: str,
    course_code: str,
    term: str | None,
    year: int | None,
) -> list[dict[str, Any]]:
    """Every FULL section (enrolled >= capacity) of a course — the waitlist candidates.

    Each entry carries the section id, instructor, meeting slots, term/year, and the
    current number of students already waiting, so the agent can present a real choice.
    """
    return await SectionRepository(session, tenant_id).full_sections_for_course(
        course_code=course_code, term=term, year=year
    )


async def _any_open_seat(
    session: Any,
    *,
    tenant_id: str,
    course_code: str,
    term: str | None,
    year: int | None,
) -> bool:
    """True if some section of the course has an open seat (term-scoped when given)."""
    return await SectionRepository(session, tenant_id).any_open_seat(
        course_code=course_code, term=term, year=year
    )


async def _get_section_row(
    session: Any, *, tenant_id: str, section_id: str
) -> dict[str, Any] | None:
    """Fetch a single section's identity + capacity for a tenant, or None."""
    try:
        sid = str(UUID(section_id))
    except ValueError:
        return None
    return await SectionRepository(session, tenant_id).get_by_id(sid)


async def stage_enrollment(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    student_id: str,
    tenant_id: str,
    thread_id: str,
    course_codes: Any,
    term: Any,
    year: Any,
    section_ids: Any,
    excluded_days: Any,
    min_start_hour: Any,
) -> str:
    """Extracted from the stage_enrollment enrollment tool (behaviour unchanged)."""
    if not course_codes:
        err = ToolError(
            error="No course codes provided to enroll in.",
            retryable=False,
            category="validation",
        )
        return err.model_dump_json()
    try:
        async with tenant_session(session_factory, UUID(tenant_id)) as session:
            if section_ids:
                # LLM-chosen sections → engine re-verifies (open, conflict-free,
                # belongs to a requested course). Invalid → ToolError, LLM repairs.
                chosen, error = await _verify_chosen_sections(
                    session,
                    tenant_id=tenant_id,
                    section_ids=section_ids,
                    course_codes=course_codes,
                    term=term.lower(),
                    year=year,
                )
                if error is not None:
                    return ToolError(
                        error=error, retryable=False, category="validation"
                    ).model_dump_json()
            else:
                # Fallback: engine deterministically picks an open, conflict-free
                # section per course, honouring any stated time preferences.
                chosen, unresolved = await _resolve_sections_for_courses(
                    session,
                    tenant_id=tenant_id,
                    course_codes=course_codes,
                    term=term.lower(),
                    year=year,
                    excluded_days=excluded_days,
                    min_start_hour=min_start_hour,
                )
                if unresolved:
                    full, not_offered = await _classify_unavailable(
                        session,
                        tenant_id=tenant_id,
                        course_codes=unresolved,
                        term=term.lower(),
                        year=year,
                    )
                    parts: list[str] = []
                    if full:
                        parts.append(
                            f"{', '.join(full)} — all sections are FULL in "
                            f"{term.title()} {year} (offer the waitlist)"
                        )
                    if not_offered:
                        parts.append(
                            f"{', '.join(not_offered)} — NOT offered in "
                            f"{term.title()} {year} (suggest another term, not a waitlist)"
                        )
                    return ToolError(
                        error="Could not enroll: " + "; ".join(parts) + ".",
                        retryable=False,
                        category="validation",
                    ).model_dump_json()

            section_ids = [c["section_id"] for c in chosen]
            payload = {
                "section_ids": section_ids,
                "student_id": student_id,
                "course_codes": course_codes,
                "term": term.lower(),
                "year": year,
            }
            action_id = await ActionsRepository(session, UUID(tenant_id)).insert_pending(
                student_id=UUID(student_id),
                thread_id=thread_id,
                action_type="enrollment",
                payload=payload,
            )

        # Human-readable schedule summary for the approval card.
        schedule_lines = []
        any_pref_conflict = False
        for c in chosen:
            instr = c.get("instructor") or "TBA"
            line = f"{c['course_code']}: {_fmt_slots(c['slots'])} · {instr}"
            if not c.get("meets_prefs", True):
                line += " (⚠ outside your time preference — best available)"
                any_pref_conflict = True
            schedule_lines.append(line)
        schedule_text = "; ".join(schedule_lines)

        _log.info(
            "tool.stage_enrollment.staged",
            action_id=str(action_id),
            student_id=student_id,
            section_count=len(section_ids),
            course_codes=course_codes,
            pref_conflict=any_pref_conflict,
            tenant_id=tenant_id,
        )
        return json.dumps(
            {
                "action_id": str(action_id),
                "type": "enrollment",
                "status": "pending",
                "course_codes": course_codes,
                "term": term.lower(),
                "year": year,
                "sections": schedule_lines,
                "message": (
                    f"Ready to enroll you in {len(section_ids)} course(s) for "
                    f"{term.title()} {year} — {schedule_text}. "
                    + (
                        "Some sections fall outside your stated preference (no better "
                        "open section exists). "
                        if any_pref_conflict
                        else ""
                    )
                    + "Approve to enroll, or reject and tell me what to change."
                ),
            }
        )

    except Exception as exc:
        _log.error("tool.stage_enrollment.error", error=str(exc))
        err = ToolError(error=str(exc), retryable=True, category="engine")
        return err.model_dump_json()


async def stage_waitlist_join(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    student_id: str,
    tenant_id: str,
    thread_id: str,
    course_code: Any,
    term: Any,
    year: Any,
    section_id: Any,
    auto_enroll: Any,
) -> str:
    """Extracted from the stage_waitlist_join enrollment tool (behaviour unchanged)."""
    term_l = term.lower() if term else None
    try:
        async with tenant_session(session_factory, UUID(tenant_id)) as session:
            # Default to the student's current term when not given, so a course-level
            # waitlist targets the term they're registering in (not another term's section).
            term_l, year = await _default_term_year(
                session, tenant_id=tenant_id, student_id=student_id, term=term_l, year=year
            )
            instructor_label: str | None = None
            if section_id:
                # The student chose a SPECIFIC section (e.g. an instructor's). Honour it
                # as long as that section is genuinely full — even if a DIFFERENT section
                # of the same course is open (the student wants this one). Only the chosen
                # section's own capacity decides whether a waitlist makes sense.
                sec = await _get_section_row(session, tenant_id=tenant_id, section_id=section_id)
                if sec is None or sec["course_code"] != course_code:
                    return ToolError(
                        error=(
                            f"That section was not found for {course_code}. Call "
                            "list_full_sections and pass one of the listed section_ids."
                        ),
                        retryable=False,
                        category="validation",
                    ).model_dump_json()
                if int(sec["enrolled"]) < int(sec["capacity"]):
                    return ToolError(
                        error=(
                            f"That {course_code} section has OPEN seats — it is NOT full, "
                            "so a waitlist does not apply. Enroll the student in it "
                            "directly with stage_enrollment instead; do not waitlist."
                        ),
                        retryable=False,
                        category="validation",
                    ).model_dump_json()
                section_id = str(sec["id"])
                instructor_label = sec["instructor"]
            else:
                # Course-level intent ("waitlist me for CS301"). If ANY section is open
                # this term, don't waitlist — enroll instead (term-scoped so a course full
                # in fall but open in spring isn't wrongly redirected away from a fall
                # waitlist). Otherwise every section is full: pick the only one, or — if
                # there are several — make the agent ask which the student wants.
                if await _any_open_seat(
                    session,
                    tenant_id=tenant_id,
                    course_code=course_code,
                    term=term_l,
                    year=year,
                ):
                    return ToolError(
                        error=(
                            f"{course_code} has OPEN seats this term — it is NOT full, so a "
                            "waitlist does not apply. Do NOT ask about auto-enroll vs "
                            "notify and do NOT re-attempt the waitlist. Tell the student "
                            "the course is open and enroll them with stage_enrollment."
                        ),
                        retryable=False,
                        category="validation",
                    ).model_dump_json()
                full = await _full_sections_for_course(
                    session,
                    tenant_id=tenant_id,
                    course_code=course_code,
                    term=term_l,
                    year=year,
                )
                if not full:
                    return ToolError(
                        error=(
                            f"{course_code} has no section this term, so there is nothing "
                            "to waitlist. Suggest a term when it is offered instead."
                        ),
                        retryable=False,
                        category="validation",
                    ).model_dump_json()
                if len(full) > 1:
                    return ToolError(
                        error=(
                            f"{course_code} has {len(full)} full sections. Call "
                            "list_full_sections, ask the student which one they want (by "
                            "instructor or meeting time), then call stage_waitlist_join "
                            "again with that section_id. Do not pick one yourself."
                        ),
                        retryable=False,
                        category="validation",
                    ).model_dump_json()
                section_id = str(full[0]["id"])
                instructor_label = full[0]["instructor"]

            payload = {
                "section_id": section_id,
                "auto_enroll": auto_enroll,
                "student_id": student_id,
                "course_code": course_code,
            }
            action_id = await ActionsRepository(session, UUID(tenant_id)).insert_pending(
                student_id=UUID(student_id),
                thread_id=thread_id,
                action_type="waitlist_join",
                payload=payload,
            )

        _log.info(
            "tool.stage_waitlist_join.staged",
            action_id=str(action_id),
            student_id=student_id,
            section_id=section_id,
            auto_enroll=auto_enroll,
            tenant_id=tenant_id,
        )
        consent_note = (
            "Approving this also gives consent to automatic enrollment when a seat "
            "opens, if you are still eligible at that time."
            if auto_enroll
            else "You will be notified when a seat opens; no automatic enrollment."
        )
        section_phrase = (
            f"{course_code} ({instructor_label}'s section)" if instructor_label else course_code
        )
        return json.dumps(
            {
                "action_id": str(action_id),
                "type": "waitlist_join",
                "status": "pending",
                "course_code": course_code,
                "section_id": section_id,
                "instructor": instructor_label,
                "auto_enroll": auto_enroll,
                "message": (
                    f"Waitlist join staged for {section_phrase}. Action ID: {action_id}. "
                    f"{consent_note} Approval required before any write."
                ),
            }
        )

    except Exception as exc:
        _log.error("tool.stage_waitlist_join.error", error=str(exc))
        err = ToolError(error=str(exc), retryable=True, category="engine")
        return err.model_dump_json()


async def list_full_sections(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    student_id: str,
    tenant_id: str,
    course_code: Any,
    term: Any,
    year: Any,
) -> str:
    """Extracted from the list_full_sections enrollment tool (behaviour unchanged)."""
    term_l = term.lower() if term else None
    try:
        async with tenant_session(session_factory, UUID(tenant_id)) as session:
            # Default to the student's current term so we never list the same instructor's
            # section from another term (which would let them waitlist the wrong one).
            term_l, year = await _default_term_year(
                session, tenant_id=tenant_id, student_id=student_id, term=term_l, year=year
            )
            full = await _full_sections_for_course(
                session,
                tenant_id=tenant_id,
                course_code=course_code,
                term=term_l,
                year=year,
            )
            if not full:
                # Distinguish "open (enroll instead)" from "not offered this term".
                if await _any_open_seat(
                    session,
                    tenant_id=tenant_id,
                    course_code=course_code,
                    term=term_l,
                    year=year,
                ):
                    return (
                        f"{course_code} is NOT full — it has open seats this term. "
                        "No waitlist is needed; offer to enroll the student instead."
                    )
                return (
                    f"{course_code} has no section this term, so there is nothing to "
                    "waitlist. Suggest a term when it is offered."
                )

            when = f"{term.title()} {year}" if term and year is not None else "this term"
            lines = [f"Full sections of {course_code} ({when}) — choose one to waitlist:"]
            for sec in full:
                instr = sec["instructor"] or "TBA"
                slots = _fmt_slots(list(sec["slots"] or []))
                waiting = int(sec["waiting"])
                plural = "" if waiting == 1 else "s"
                lines.append(
                    f"- {instr} · {slots} · {waiting} student{plural} waiting "
                    f"[section_id: {sec['id']}]"
                )
            lines.append(
                "Ask which section the student wants (by instructor or meeting time), "
                "confirm auto-enroll vs notify-only, then call stage_waitlist_join with "
                "that section_id."
            )
            _log.info(
                "tool.list_full_sections.done",
                student_id=student_id,
                course_code=course_code,
                full_count=len(full),
                tenant_id=tenant_id,
            )
            return "\n".join(lines)
    except Exception as exc:
        _log.error("tool.list_full_sections.error", error=str(exc))
        return ToolError(error=str(exc), retryable=True, category="engine").model_dump_json()


async def stage_waitlist_leave(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    student_id: str,
    tenant_id: str,
    thread_id: str,
    course_code: Any,
    term: Any,
    year: Any,
) -> str:
    """Extracted from the stage_waitlist_leave enrollment tool (behaviour unchanged)."""
    try:
        async with tenant_session(session_factory, UUID(tenant_id)) as session:
            section_id = await _resolve_section_for_course(
                session,
                tenant_id=tenant_id,
                course_code=course_code,
                term=term.lower() if term else None,
                year=year,
            )
            # Check that a waiting entry exists for the resolved section.
            waiting_id = None
            if section_id is not None:
                waiting_id = await WaitlistRepository(session, tenant_id).find_waiting_entry_id(
                    student_id=str(student_id), section_id=str(section_id)
                )
            if section_id is None or waiting_id is None:
                err = ToolError(
                    error=f"No active waitlist entry found for {course_code}.",
                    retryable=False,
                    category="validation",
                )
                return err.model_dump_json()

            payload = {
                "section_id": section_id,
                "student_id": student_id,
                "course_code": course_code,
            }
            action_id = await ActionsRepository(session, UUID(tenant_id)).insert_pending(
                student_id=UUID(student_id),
                thread_id=thread_id,
                action_type="waitlist_leave",
                payload=payload,
            )

        return json.dumps(
            {
                "action_id": str(action_id),
                "type": "waitlist_leave",
                "status": "pending",
                "course_code": course_code,
                "message": (
                    f"Waitlist removal staged for {course_code}. Action ID: {action_id}. "
                    "Approval required before any write."
                ),
            }
        )

    except Exception as exc:
        _log.error("tool.stage_waitlist_leave.error", error=str(exc))
        err = ToolError(error=str(exc), retryable=True, category="engine")
        return err.model_dump_json()
