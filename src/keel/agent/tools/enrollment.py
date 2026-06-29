"""Write-path enrollment tools: stage_enrollment, stage_waitlist_join, stage_waitlist_leave
(plus the read-only list_full_sections helper used to choose a section to waitlist).

The three stage_* tools are the ONLY agent tools that reach the action pattern (spec §2).
None of them write anything — they stage a pending action and interrupt the graph.
The execute_node (in graph.py) does the actual write, but only on a human-approved resume.

Safety conditions enforced here (spec §1):
  - Pydantic-validated inputs (tool args never passed raw to DB).
  - Engine validates eligibility NOW before staging — early rejection is cheap.
  - payload is built deterministically from tool args and frozen on the action row.
  - thread_id is written to the action row at stage time; the approve handler reads
    it from the row — never from the request. Closes cross-thread resume.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from keel.agent.identity import resolve_identity, resolve_thread_id
from keel.domain.schemas import ToolError
from keel.infra.database.session import tenant_session
from keel.logging import get_logger
from keel.services.actions import ActionRepo

from ._deps import AgentDeps

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Tool input schemas
# ---------------------------------------------------------------------------


class StageEnrollmentInput(BaseModel):
    """Stage an enrollment action for student approval.

    Two ways to choose sections, both verified by the engine before staging:
      • Preferred: after calling propose_sections, pass the chosen ``section_ids``
        (one per course). The engine re-verifies each is open, conflict-free, and
        belongs to a requested course before staging — the LLM proposes, the engine
        verifies (mirrors the plan loop).
      • Fallback ("you pick for me"): pass only course_codes (+ optional time prefs)
        and the engine deterministically picks an open, conflict-free section.
    """

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    course_codes: list[str] = Field(
        description=(
            "Course codes to enroll in, e.g. ['CS102', 'MATH101']. Copy these straight "
            "from the plan the student approved."
        )
    )
    term: str = Field(description="Term to enroll in: fall, spring, or summer.")
    year: int = Field(description="Calendar year of the term, e.g. 2025.")
    section_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Optional: the section UUIDs you chose from propose_sections — one per course, "
            "in the same order as course_codes. Pass these when the student has time/instructor "
            "preferences so YOU pick the matching sections; the engine re-verifies them. Leave "
            "empty to let Keel pick deterministically."
        ),
    )
    excluded_days: list[str] = Field(
        default_factory=list,
        description=(
            "Days the student wants NO classes, lowercase short names 'mon'..'fri'. Used only "
            "for the fallback pick (when section_ids is empty). E.g. ['fri']."
        ),
    )
    min_start_hour: int = Field(
        default=0,
        description=(
            "Earliest acceptable class start hour (24-h, 0 = no limit), used only for the "
            "fallback pick when section_ids is empty. 9 = avoid 8 AM sections."
        ),
    )
    thread_id: str = Field(
        description="LangGraph thread ID for this conversation — used to resume after approval."
    )


class StageWaitlistJoinInput(BaseModel):
    """Stage a waitlist join action for student approval.

    The agent works in COURSE CODES — Keel resolves the course to its (full) section.
    """

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    course_code: str = Field(
        description="Course code the student wants to waitlist for, e.g. 'CS301'."
    )
    term: str | None = Field(
        default=None,
        description="Term of the section: fall, spring, or summer. Use the student's current term.",
    )
    year: int | None = Field(default=None, description="Calendar year of the term, e.g. 2026.")
    section_id: str | None = Field(
        default=None,
        description=(
            "Optional: the UUID of the SPECIFIC full section the student chose (e.g. a "
            "particular instructor's section), taken from list_full_sections. Pass this "
            "whenever the course has more than one full section so the student is waitlisted "
            "for the section they asked for. Omit only when the course has a single section."
        ),
    )
    auto_enroll: bool = Field(
        description=(
            "True = enroll the student automatically when a seat opens (if still eligible). "
            "False = notify only; student must manually enroll. Ask the student before setting."
        )
    )
    thread_id: str = Field(
        description="LangGraph thread ID for this conversation — used to resume after approval."
    )


class ListFullSectionsInput(BaseModel):
    """List the full (waitlist-eligible) sections of a course so the student can choose."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    course_code: str = Field(description="Course code to list full sections for, e.g. 'CS301'.")
    term: str | None = Field(
        default=None,
        description="Term to look in: fall, spring, or summer. Use the student's current term.",
    )
    year: int | None = Field(default=None, description="Calendar year of the term, e.g. 2026.")


class StageWaitlistLeaveInput(BaseModel):
    """Stage a waitlist leave action for student approval."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    course_code: str = Field(
        description="Course code whose waitlist the student wants to leave, e.g. 'CS301'."
    )
    term: str | None = Field(
        default=None, description="Term of the section: fall, spring, or summer."
    )
    year: int | None = Field(default=None, description="Calendar year of the term, e.g. 2026.")
    thread_id: str = Field(
        description="LangGraph thread ID for this conversation — used to resume after approval."
    )


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def make_enrollment_tools(deps: AgentDeps) -> list[Any]:
    """Return the enrollment/waitlist tools (stage_* + list_full_sections) over deps."""

    @tool(args_schema=StageEnrollmentInput)
    async def stage_enrollment(
        student_id: str,
        tenant_id: str,
        course_codes: list[str],
        term: str,
        year: int,
        thread_id: str,
        section_ids: list[str] | None = None,
        excluded_days: list[str] | None = None,
        min_start_hour: int = 0,
    ) -> str:
        """Validate and stage a course enrollment for student approval.
        Two modes: (1) pass the section_ids you chose from propose_sections (one per
        course) — the engine RE-VERIFIES each is open, conflict-free, and belongs to a
        requested course before staging (you propose, the engine verifies). (2) Pass only
        course_codes (+ optional time prefs) and Keel picks an open, conflict-free section
        per course itself. Returns an action_id the student must approve before any write,
        plus a summary of the chosen sections (day/time, instructor) for the approval card.
        If a section is full/unavailable the engine says so — then offer the waitlist.
        NEVER writes an enrollment directly — all writes require explicit approval.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        thread_id = resolve_thread_id(thread_id)
        if not course_codes:
            err = ToolError(
                error="No course codes provided to enroll in.",
                retryable=False,
                category="validation",
            )
            return err.model_dump_json()
        try:
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as session:
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
                action_id = await ActionRepo.insert_pending(
                    session,
                    tenant_id=UUID(tenant_id),
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

    @tool(args_schema=StageWaitlistJoinInput)
    async def stage_waitlist_join(
        student_id: str,
        tenant_id: str,
        course_code: str,
        auto_enroll: bool,
        thread_id: str,
        term: str | None = None,
        year: int | None = None,
        section_id: str | None = None,
    ) -> str:
        """Validate and stage a waitlist join for student approval.
        Pass the COURSE CODE (e.g. 'CS301'). If the course has MORE THAN ONE full section,
        first call list_full_sections and ask the student which section they want (e.g. a
        specific instructor's), then pass that ``section_id`` here so they are waitlisted for
        the section they chose. For a single-section course you may omit section_id and Keel
        uses it. Keel verifies the chosen section is genuinely FULL before staging.
        When auto_enroll=True, the student's single approval also covers automatic
        enrollment when a seat opens (delegated consent — the engine re-verifies at
        execution time).  When False, a seat-open sends a notification only.
        NEVER writes anything — approval required before any DB write.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        thread_id = resolve_thread_id(thread_id)
        term_l = term.lower() if term else None
        try:
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as session:
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
                    sec = await _get_section_row(
                        session, tenant_id=tenant_id, section_id=section_id
                    )
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
                action_id = await ActionRepo.insert_pending(
                    session,
                    tenant_id=UUID(tenant_id),
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

    @tool(args_schema=ListFullSectionsInput)
    async def list_full_sections(
        student_id: str,
        tenant_id: str,
        course_code: str,
        term: str | None = None,
        year: int | None = None,
    ) -> str:
        """List the FULL sections of a course so the student can choose one to waitlist.
        Call this when a course is full and you need the student to pick WHICH section to
        join the waitlist for (e.g. "Dr. Rahal's section"). Returns each full section with
        its instructor, meeting time, number already waiting, and section_id. Present these
        to the student, let them choose (by instructor or time), then call stage_waitlist_join
        with the chosen section_id. Read-only — stages nothing.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        term_l = term.lower() if term else None
        try:
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as session:
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

    @tool(args_schema=StageWaitlistLeaveInput)
    async def stage_waitlist_leave(
        student_id: str,
        tenant_id: str,
        course_code: str,
        thread_id: str,
        term: str | None = None,
        year: int | None = None,
    ) -> str:
        """Stage a waitlist removal for student approval.
        Pass the COURSE CODE — Keel resolves it to the section the student is waiting on.
        Approval required before any write.
        """
        tenant_id, student_id = resolve_identity(tenant_id, student_id)
        thread_id = resolve_thread_id(thread_id)
        try:
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as session:
                section_id = await _resolve_section_for_course(
                    session,
                    tenant_id=tenant_id,
                    course_code=course_code,
                    term=term.lower() if term else None,
                    year=year,
                )
                # Check that a waiting entry exists for the resolved section.
                existing = None
                if section_id is not None:
                    existing = await session.execute(
                        sa.text(
                            "SELECT id FROM waitlist "
                            "WHERE tenant_id = :tid AND student_id = :sid "
                            "AND section_id = :secid AND status = 'waiting'"
                        ),
                        {
                            "tid": str(tenant_id),
                            "sid": str(student_id),
                            "secid": str(section_id),
                        },
                    )
                if section_id is None or not existing or not existing.scalar_one_or_none():
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
                action_id = await ActionRepo.insert_pending(
                    session,
                    tenant_id=UUID(tenant_id),
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

    return [
        stage_enrollment,
        list_full_sections,
        stage_waitlist_join,
        stage_waitlist_leave,
    ]


# ---------------------------------------------------------------------------
# Validation helpers (read-only — called inside stage tools)
# ---------------------------------------------------------------------------


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
    rows = await session.execute(
        sa.text(
            "SELECT course_code, bool_or(enrolled < capacity) AS any_open "
            "FROM sections WHERE tenant_id = :tid AND term = :term AND year = :yr "
            "AND course_code = ANY(:codes) GROUP BY course_code"
        ),
        {"tid": str(tenant_id), "term": term, "yr": year, "codes": list(course_codes)},
    )
    has_section: dict[str, bool] = {r["course_code"]: bool(r["any_open"]) for r in rows.mappings()}
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

    rows = await session.execute(
        sa.text(
            "SELECT id, course_code, slots, instructor, enrolled, capacity FROM sections "
            "WHERE tenant_id = :tid AND term = :term AND year = :yr AND id = ANY(:ids)"
        ),
        {"tid": str(tenant_id), "term": term, "yr": year, "ids": ids},
    )
    found: dict[str, dict[str, Any]] = {}
    for r in rows.mappings():
        found[str(r["id"])] = dict(r)

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
    rows = await session.execute(
        sa.text(
            "SELECT id, course_code, slots, instructor FROM sections "
            "WHERE tenant_id = :tid AND term = :term AND year = :yr "
            "AND course_code = ANY(:codes) AND enrolled < capacity "
            "ORDER BY course_code, created_at"
        ),
        {"tid": str(tenant_id), "term": term, "yr": year, "codes": list(course_codes)},
    )
    open_by_code: dict[str, list[dict[str, Any]]] = {}
    for row in rows.mappings():
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
    clauses = ["tenant_id = :tid", "course_code = :code"]
    params: dict[str, Any] = {"tid": str(tenant_id), "code": course_code}
    if term:
        clauses.append("term = :term")
        params["term"] = term
    if year is not None:
        clauses.append("year = :yr")
        params["yr"] = year
    row = await session.execute(
        sa.text(
            "SELECT id FROM sections WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at LIMIT 1"
        ),
        params,
    )
    found = row.scalar_one_or_none()
    return str(found) if found else None


def _term_filter(term: str | None, year: int | None) -> tuple[list[str], dict[str, Any]]:
    """Build optional term/year WHERE clauses + params (shared by the section helpers)."""
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if term:
        clauses.append("term = :term")
        params["term"] = term
    if year is not None:
        clauses.append("year = :yr")
        params["yr"] = year
    return clauses, params


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
    row = await session.execute(
        sa.text(
            "SELECT current_term, current_year FROM students WHERE id = :sid AND tenant_id = :tid"
        ),
        {"sid": str(student_id), "tid": str(tenant_id)},
    )
    r = row.mappings().first()
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
    clauses = ["s.tenant_id = :tid", "s.course_code = :code", "s.enrolled >= s.capacity"]
    params: dict[str, Any] = {"tid": str(tenant_id), "code": course_code}
    extra, eparams = _term_filter(term, year)
    clauses.extend(f"s.{c}" for c in extra)
    params.update(eparams)
    rows = await session.execute(
        sa.text(
            "SELECT s.id, s.instructor, s.slots, s.term, s.year, "
            "(SELECT COUNT(*) FROM waitlist w "
            " WHERE w.section_id = s.id AND w.status = 'waiting') AS waiting "
            "FROM sections s WHERE " + " AND ".join(clauses) + " ORDER BY s.instructor"
        ),
        params,
    )
    return [dict(r) for r in rows.mappings()]


async def _any_open_seat(
    session: Any,
    *,
    tenant_id: str,
    course_code: str,
    term: str | None,
    year: int | None,
) -> bool:
    """True if some section of the course has an open seat (term-scoped when given)."""
    clauses = ["tenant_id = :tid", "course_code = :code", "enrolled < capacity"]
    params: dict[str, Any] = {"tid": str(tenant_id), "code": course_code}
    extra, eparams = _term_filter(term, year)
    clauses.extend(extra)
    params.update(eparams)
    row = await session.execute(
        sa.text("SELECT 1 FROM sections WHERE " + " AND ".join(clauses) + " LIMIT 1"),
        params,
    )
    return row.first() is not None


async def _get_section_row(
    session: Any, *, tenant_id: str, section_id: str
) -> dict[str, Any] | None:
    """Fetch a single section's identity + capacity for a tenant, or None."""
    try:
        sid = str(UUID(section_id))
    except ValueError:
        return None
    row = await session.execute(
        sa.text(
            "SELECT id, course_code, instructor, slots, term, year, capacity, enrolled "
            "FROM sections WHERE id = :sid AND tenant_id = :tid"
        ),
        {"sid": sid, "tid": str(tenant_id)},
    )
    rec = row.mappings().first()
    return dict(rec) if rec else None
