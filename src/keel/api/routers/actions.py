"""Actions router — student approve/reject endpoints for staged write actions.

POST /actions/{action_id}/approve
POST /actions/{action_id}/reject

Safety model (spec §8 / §11 / SECURITY.md §5):
  Auth:              the verified widget Bearer JWT is the ONLY source of
                     identity. student_id / tenant_id come from the signed
                     token — never from a client-supplied header or body.
  Origin:            verify_origin_or_403 — token from a disallowed Origin → 403.
  Layer 1 (tenant):  action lookup via RLS-scoped session.
                     Cross-tenant action_id → 404, leaking nothing.
  Layer 2 (student): assert action.student_id == token student_id.
                     Student A cannot approve Student B's action → 403.
  Status guard:      action must be 'pending'; else 409.
  Thread-binding:    graph resumes using action.thread_id (written at stage time),
                     NEVER a thread_id from the request.

This endpoint IS the human approval gate for every staged write — so its
identity must be cryptographically verified, exactly like /chat. The previous
X-Student-Id / X-Tenant-Id header scheme is removed: it both (a) trusted
spoofable plaintext headers and (b) never matched what the widget sends.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from keel.api.auth import WidgetContext, get_widget_context, verify_origin_or_403
from keel.infra.database.session import tenant_session
from keel.logging import get_logger
from keel.repositories.core import ActionsRepository

router = APIRouter(prefix="/actions", tags=["actions"])
_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Auth dependency — the verified widget JWT (same dependency /chat uses)
# ---------------------------------------------------------------------------


async def _get_current_user(
    request: Request,
    ctx: Annotated[WidgetContext, Depends(get_widget_context)],
) -> WidgetContext:
    """Identity from the verified widget Bearer JWT + tenant origin check.

    ``get_widget_context`` validates signature, expiry, audience, and required
    claims; ``verify_origin_or_403`` enforces the tenant's Origin allowlist.
    A valid token from a disallowed Origin is rejected (SECURITY.md §3.1).
    """
    verify_origin_or_403(request, ctx.tenant_id)
    return ctx


def _get_session_factory(request: Request) -> Any:
    return request.app.state.session_factory


def _get_agent(request: Request) -> Any:
    """Return the compiled LangGraph agent stored on app.state."""
    return getattr(request.app.state, "compiled_agent", None)


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------


class ActionDecisionResponse(BaseModel):
    action_id: str
    status: str
    message: str
    plans: list[dict[str, Any]] = Field(default_factory=list)


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
    current_user: Annotated[WidgetContext, Depends(_get_current_user)],
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
        actions_repo = ActionsRepository(session, current_user.tenant_id)
        action = await actions_repo.get(action_uuid)
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
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your action.")

        # ---- Status guard -----------------------------------------------
        if action["status"] != "pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Action is already {action['status']}.",
            )

        # ---- All checks passed — approve --------------------------------
        await actions_repo.set_approved(action_uuid)
        thread_id: str = str(action["thread_id"])
        action_type: str = str(action["type"])
        # session commits on context-manager exit

    _log.info(
        "actions.approve.ok",
        action_id=action_id,
        student_id=current_user.student_id,
        thread_id=thread_id,
        tenant_id=current_user.tenant_id,
    )

    # Type-aware fallback message (an enrollment is NOT a registrar-queue request).
    _is_request = action_type in ("graduation", "major_change", "petition")
    result_message = (
        "Your request was filed — the registrar will review it and you'll be notified."
        if _is_request
        else "Done — your action has been completed."
    )

    # ---- Resume the suspended LangGraph thread --------------------------
    # thread_id comes from the action row — never from the request. The resumed graph
    # runs execute_node (the actual write) and the agent's wrap-up; we surface its real
    # final message so the widget shows the truth (e.g. "Enrolled in 3 course(s) ✓"),
    # not a hardcoded one.
    agent = _get_agent(request)
    if agent is not None:
        from langchain_core.messages import AIMessage
        from langgraph.types import Command

        try:
            final_state = await agent.ainvoke(
                Command(resume={"action_id": action_id}),
                config={"configurable": {"thread_id": thread_id}},
            )
            from keel.agent.graph import _extract_text

            for msg in reversed(final_state.get("messages", []) if final_state else []):
                if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
                    txt = _extract_text(msg.content)  # handles Gemini list-block content
                    if txt.strip():
                        result_message = txt.strip()
                        break
        except Exception as exc:
            _log.error(
                "actions.approve.resume_failed",
                action_id=action_id,
                error=str(exc),
            )
            # Don't surface internal errors; the action is already approved.

    plans: list[dict[str, Any]] = []
    if action_type == "enrollment":
        try:
            from keel.services.grad_plans import load_active_grad_plan

            async with tenant_session(session_factory, UUID(current_user.tenant_id)) as session:
                loaded = await load_active_grad_plan(
                    session,
                    tenant_id=UUID(current_user.tenant_id),
                    student_id=UUID(current_user.student_id),
                )
            if loaded and loaded.card:
                plans.append(loaded.card)
        except Exception as exc:  # noqa: BLE001
            _log.warning("actions.approve.load_synced_grad_plan_failed", error=str(exc))

    return ActionDecisionResponse(
        action_id=action_id,
        status="approved",
        message=result_message,
        plans=plans,
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
    current_user: Annotated[WidgetContext, Depends(_get_current_user)],
) -> ActionDecisionResponse:
    """Reject a pending staged action; resume so the agent can re-plan or close."""
    session_factory = _get_session_factory(request)
    action_uuid = _parse_uuid(action_id)

    async with tenant_session(session_factory, UUID(current_user.tenant_id)) as session:
        actions_repo = ActionsRepository(session, current_user.tenant_id)
        action = await actions_repo.get(action_uuid)
        if not action:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action not found.")

        if str(action["student_id"]) != current_user.student_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your action.")

        if action["status"] != "pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Action is already {action['status']}.",
            )

        await actions_repo.set_rejected(action_uuid)
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
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid UUID: {value}",
        ) from err
