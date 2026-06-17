"""Actions router — student approve/reject endpoints for staged write actions.

POST /actions/{action_id}/approve
POST /actions/{action_id}/reject

Safety model (spec §1 / plan.md §1.3):
  Layer 1 (tenant): action lookup via RLS-scoped session.
                    Cross-tenant action_id → 404, leaking nothing.
  Layer 2 (student): assert action.student_id == current_user.id.
                     Student A cannot approve Student B's action → 403.
  Status guard:      action must be 'pending'; else 409.
  Thread-binding:    graph resumes using action.thread_id (written at stage time),
                     NEVER a thread_id from the request.

Auth: Bearer JWT carrying student_id + tenant_id.
For Phase 3 a lightweight JWT dep is used (same pattern as ENGINEERING_RULES.md §9).
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from keel.infra.database.session import set_tenant, tenant_session
from keel.logging import get_logger
from keel.services.actions import ActionRepo

router = APIRouter(prefix="/actions", tags=["actions"])
_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Auth dependency — lightweight JWT (Bearer token carries student_id + tenant_id)
# ---------------------------------------------------------------------------


class CurrentUser(BaseModel):
    student_id: str
    tenant_id: str


def _get_session_factory(request: Request) -> Any:
    return request.app.state.session_factory


def _get_agent(request: Request) -> Any:
    """Return the compiled LangGraph agent stored on app.state."""
    return getattr(request.app.state, "compiled_agent", None)


async def _get_current_user(request: Request) -> CurrentUser:
    """Decode the Bearer token from Authorization header.

    Phase 3: we validate the token by reading student_id + tenant_id from
    the X-Student-Id / X-Tenant-Id headers (signed widget token exchange is
    wired in Phase 5).  Return 401 if missing.
    """
    student_id = request.headers.get("X-Student-Id")
    tenant_id = request.headers.get("X-Tenant-Id")
    if not student_id or not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Student-Id or X-Tenant-Id headers.",
        )
    return CurrentUser(student_id=student_id, tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------


class ActionDecisionResponse(BaseModel):
    action_id: str
    status: str
    message: str


# ---------------------------------------------------------------------------
# Approve endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/{action_id}/approve",
    response_model=ActionDecisionResponse,
    status_code=status.HTTP_200_OK,
)
async def approve_action(
    action_id: str,
    request: Request,
    current_user: Annotated[CurrentUser, Depends(_get_current_user)],
) -> ActionDecisionResponse:
    """Approve a pending staged action and resume the agent graph.

    Two-layer isolation (plan.md §1.3):
      1. RLS-scoped action lookup → 404 on cross-tenant.
      2. student_id check → 403 if action belongs to a different student.
    Thread-binding: graph resumes on action.thread_id from the row, not the request.
    """
    session_factory = _get_session_factory(request)
    action_uuid = _parse_uuid(action_id)

    # ---- Load action (Layer 1: RLS scopes to tenant) --------------------
    async with tenant_session(session_factory, UUID(current_user.tenant_id)) as session:
        action = await ActionRepo.get(session, action_uuid)
        if not action:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action not found.")

        # ---- Layer 2: student isolation ---------------------------------
        if str(action["student_id"]) != current_user.student_id:
            _log.warning(
                "actions.approve.student_mismatch",
                action_id=action_id,
                action_student=str(action["student_id"]),
                requesting_student=current_user.student_id,
                tenant_id=current_user.tenant_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Not your action."
            )

        # ---- Status guard -----------------------------------------------
        if action["status"] != "pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Action is already {action['status']}.",
            )

        # ---- All checks passed — approve --------------------------------
        await ActionRepo.set_approved(session, action_uuid)
        thread_id: str = str(action["thread_id"])
        # session commits on context-manager exit

    _log.info(
        "actions.approve.ok",
        action_id=action_id,
        student_id=current_user.student_id,
        thread_id=thread_id,
        tenant_id=current_user.tenant_id,
    )

    # ---- Resume the suspended LangGraph thread --------------------------
    # thread_id comes from the action row — never from the request.
    agent = _get_agent(request)
    if agent is not None:
        from langgraph.types import Command

        try:
            await agent.ainvoke(
                Command(resume={"action_id": action_id}),
                config={"configurable": {"thread_id": thread_id}},
            )
        except Exception as exc:
            _log.error(
                "actions.approve.resume_failed",
                action_id=action_id,
                error=str(exc),
            )
            # Don't surface internal errors; the action is already approved.

    return ActionDecisionResponse(
        action_id=action_id,
        status="approved",
        message="Action approved. Enrollment is being processed.",
    )


# ---------------------------------------------------------------------------
# Reject endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/{action_id}/reject",
    response_model=ActionDecisionResponse,
    status_code=status.HTTP_200_OK,
)
async def reject_action(
    action_id: str,
    request: Request,
    current_user: Annotated[CurrentUser, Depends(_get_current_user)],
) -> ActionDecisionResponse:
    """Reject a pending staged action; resume so the agent can re-plan or close."""
    session_factory = _get_session_factory(request)
    action_uuid = _parse_uuid(action_id)

    async with tenant_session(session_factory, UUID(current_user.tenant_id)) as session:
        action = await ActionRepo.get(session, action_uuid)
        if not action:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action not found.")

        if str(action["student_id"]) != current_user.student_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Not your action."
            )

        if action["status"] != "pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Action is already {action['status']}.",
            )

        await ActionRepo.set_rejected(session, action_uuid)
        thread_id = str(action["thread_id"])

    _log.info(
        "actions.reject.ok",
        action_id=action_id,
        student_id=current_user.student_id,
        tenant_id=current_user.tenant_id,
    )

    agent = _get_agent(request)
    if agent is not None:
        from langgraph.types import Command

        try:
            await agent.ainvoke(
                Command(resume={"action_id": action_id, "rejected": True}),
                config={"configurable": {"thread_id": thread_id}},
            )
        except Exception as exc:
            _log.error("actions.reject.resume_failed", action_id=action_id, error=str(exc))

    return ActionDecisionResponse(
        action_id=action_id,
        status="rejected",
        message="Action rejected. You can ask Keel to propose an alternative.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid UUID: {value}",
        )
