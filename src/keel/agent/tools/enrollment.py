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

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from keel.agent.identity import resolve_identity, resolve_thread_id
from keel.logging import get_logger
from keel.services import enrollment_service

# Re-exported for backward compatibility: the section-selection helpers now live in
# enrollment_service, but tests still import them from keel.agent.tools.enrollment.
from keel.services.enrollment_service import (  # noqa: F401
    _fmt_slots,
    _resolve_sections_for_courses,
    _slots_meet_prefs,
    _verify_chosen_sections,
)

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
        return await enrollment_service.stage_enrollment(
            session_factory=deps.session_factory,
            student_id=student_id,
            tenant_id=tenant_id,
            thread_id=thread_id,
            course_codes=course_codes,
            term=term,
            year=year,
            section_ids=section_ids,
            excluded_days=excluded_days,
            min_start_hour=min_start_hour,
        )

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
        return await enrollment_service.stage_waitlist_join(
            session_factory=deps.session_factory,
            student_id=student_id,
            tenant_id=tenant_id,
            thread_id=thread_id,
            course_code=course_code,
            term=term,
            year=year,
            section_id=section_id,
            auto_enroll=auto_enroll,
        )

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
        return await enrollment_service.list_full_sections(
            session_factory=deps.session_factory,
            student_id=student_id,
            tenant_id=tenant_id,
            course_code=course_code,
            term=term,
            year=year,
        )

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
        return await enrollment_service.stage_waitlist_leave(
            session_factory=deps.session_factory,
            student_id=student_id,
            tenant_id=tenant_id,
            thread_id=thread_id,
            course_code=course_code,
            term=term,
            year=year,
        )

    return [
        stage_enrollment,
        list_full_sections,
        stage_waitlist_join,
        stage_waitlist_leave,
    ]


# ---------------------------------------------------------------------------
# Validation helpers (read-only — called inside stage tools)
# ---------------------------------------------------------------------------

