"""Write-path enrollment tools: stage_enrollment, stage_waitlist_join, stage_waitlist_leave.

These three tools are the ONLY agent tools that reach the action pattern (spec §2).
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
    """Stage an enrollment action for student approval."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    section_ids: list[str] = Field(
        description="UUIDs of the sections to enroll in, e.g. ['uuid-1', 'uuid-2']."
    )
    thread_id: str = Field(
        description="LangGraph thread ID for this conversation — used to resume after approval."
    )


class StageWaitlistJoinInput(BaseModel):
    """Stage a waitlist join action for student approval."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    section_id: str = Field(description="UUID of the full section to join the waitlist for.")
    auto_enroll: bool = Field(
        description=(
            "True = enroll the student automatically when a seat opens (if still eligible). "
            "False = notify only; student must manually enroll. Ask the student before setting."
        )
    )
    thread_id: str = Field(
        description="LangGraph thread ID for this conversation — used to resume after approval."
    )


class StageWaitlistLeaveInput(BaseModel):
    """Stage a waitlist leave action for student approval."""

    student_id: str = Field(description="The student's UUID — copy from the system prompt.")
    tenant_id: str = Field(description="The tenant's UUID — copy from the system prompt.")
    section_id: str = Field(
        description="UUID of the section whose waitlist the student wants to leave."
    )
    thread_id: str = Field(
        description="LangGraph thread ID for this conversation — used to resume after approval."
    )


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def make_enrollment_tools(deps: AgentDeps) -> list[Any]:
    """Return [stage_enrollment, stage_waitlist_join, stage_waitlist_leave] closed over deps."""

    @tool(args_schema=StageEnrollmentInput)
    async def stage_enrollment(
        student_id: str,
        tenant_id: str,
        section_ids: list[str],
        thread_id: str,
    ) -> str:
        """Validate and stage a course enrollment for student approval.
        Returns an action_id the student must approve before any write occurs.
        Use after propose_plan when the student selects a plan to execute.
        NEVER writes an enrollment directly — all writes require explicit approval.
        """
        try:
            # Validate that the sections exist and have capacity NOW.
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as session:
                valid, reason = await _validate_sections(
                    session, tenant_id=tenant_id, section_ids=section_ids
                )
                if not valid:
                    err = ToolError(error=reason, retryable=False, category="validation")
                    return err.model_dump_json()

                payload = {"section_ids": section_ids, "student_id": student_id}
                action_id = await ActionRepo.insert_pending(
                    session,
                    tenant_id=UUID(tenant_id),
                    student_id=UUID(student_id),
                    thread_id=thread_id,
                    action_type="enrollment",
                    payload=payload,
                )

            _log.info(
                "tool.stage_enrollment.staged",
                action_id=str(action_id),
                student_id=student_id,
                section_count=len(section_ids),
                tenant_id=tenant_id,
            )
            return json.dumps(
                {
                    "action_id": str(action_id),
                    "type": "enrollment",
                    "status": "pending",
                    "section_ids": section_ids,
                    "message": (
                        f"Enrollment staged for {len(section_ids)} section(s). "
                        f"Action ID: {action_id}. "
                        "The student must approve before any enrollment is written."
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
        section_id: str,
        auto_enroll: bool,
        thread_id: str,
    ) -> str:
        """Validate and stage a waitlist join for student approval.
        When auto_enroll=True, the student's single approval also covers automatic
        enrollment when a seat opens (delegated consent — the engine re-verifies at
        execution time).  When False, a seat-open sends a notification only.
        NEVER writes anything — approval required before any DB write.
        """
        try:
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as session:
                section_ok = await _section_exists(
                    session, tenant_id=tenant_id, section_id=section_id
                )
                if not section_ok:
                    err = ToolError(
                        error=f"Section {section_id} not found.",
                        retryable=False,
                        category="validation",
                    )
                    return err.model_dump_json()

                payload = {
                    "section_id": section_id,
                    "auto_enroll": auto_enroll,
                    "student_id": student_id,
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
            return json.dumps(
                {
                    "action_id": str(action_id),
                    "type": "waitlist_join",
                    "status": "pending",
                    "section_id": section_id,
                    "auto_enroll": auto_enroll,
                    "message": (
                        f"Waitlist join staged. Action ID: {action_id}. {consent_note} "
                        "Approval required before any write."
                    ),
                }
            )

        except Exception as exc:
            _log.error("tool.stage_waitlist_join.error", error=str(exc))
            err = ToolError(error=str(exc), retryable=True, category="engine")
            return err.model_dump_json()

    @tool(args_schema=StageWaitlistLeaveInput)
    async def stage_waitlist_leave(
        student_id: str,
        tenant_id: str,
        section_id: str,
        thread_id: str,
    ) -> str:
        """Stage a waitlist removal for student approval.
        Approval required before any write.
        """
        try:
            async with tenant_session(deps.session_factory, UUID(tenant_id)) as session:
                # Check that a waiting entry exists.
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
                if not existing.scalar_one_or_none():
                    err = ToolError(
                        error="No active waitlist entry found for this section.",
                        retryable=False,
                        category="validation",
                    )
                    return err.model_dump_json()

                payload = {"section_id": section_id, "student_id": student_id}
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
                    "section_id": section_id,
                    "message": (
                        f"Waitlist removal staged. Action ID: {action_id}. "
                        "Approval required before any write."
                    ),
                }
            )

        except Exception as exc:
            _log.error("tool.stage_waitlist_leave.error", error=str(exc))
            err = ToolError(error=str(exc), retryable=True, category="engine")
            return err.model_dump_json()

    return [stage_enrollment, stage_waitlist_join, stage_waitlist_leave]


# ---------------------------------------------------------------------------
# Validation helpers (read-only — called inside stage tools)
# ---------------------------------------------------------------------------


async def _validate_sections(
    session: Any,
    *,
    tenant_id: str,
    section_ids: list[str],
) -> tuple[bool, str]:
    """Check that sections exist and have remaining capacity."""
    if not section_ids:
        return False, "No section IDs provided."

    for section_id in section_ids:
        row = await session.execute(
            sa.text(
                "SELECT capacity, enrolled FROM sections WHERE id = :secid AND tenant_id = :tid"
            ),
            {"secid": str(section_id), "tid": str(tenant_id)},
        )
        r = row.mappings().first()
        if not r:
            return False, f"Section {section_id} not found."
        if int(r["enrolled"]) >= int(r["capacity"]):
            return False, f"Section {section_id} is at full capacity."

    return True, ""


async def _section_exists(
    session: Any,
    *,
    tenant_id: str,
    section_id: str,
) -> bool:
    row = await session.execute(
        sa.text("SELECT id FROM sections WHERE id = :secid AND tenant_id = :tid"),
        {"secid": str(section_id), "tid": str(tenant_id)},
    )
    return row.scalar_one_or_none() is not None
