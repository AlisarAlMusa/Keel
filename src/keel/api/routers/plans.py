"""Student graduation-plan endpoints.

These are widget-authenticated, student-owned metadata operations. They do not
write SIS state and therefore do not use the staged action approval gate.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from keel.api.auth import WidgetContext, db_with_tenant, get_widget_context, verify_origin_or_403
from keel.services.grad_plans import (
    GradPlanConflict,
    delete_active_grad_plan,
    load_active_grad_plan,
    save_active_grad_plan,
)

router = APIRouter(prefix="/plans", tags=["plans"])


class GradPlanCourseIn(BaseModel):
    code: str


class GradPlanTermIn(BaseModel):
    term: str = Field(description="fall | spring | summer")
    year: int
    courses: list[GradPlanCourseIn | str]


class SaveGradPlanRequest(BaseModel):
    name: str
    terms: list[GradPlanTermIn]
    replace: bool = False


class GradPlanResponse(BaseModel):
    message: str
    plan: dict[str, Any] | None = None
    conflict: bool = False
    existing_plan_id: str | None = None
    existing_name: str | None = None


async def _get_current_user(
    request: Request,
    ctx: Annotated[WidgetContext, Depends(get_widget_context)],
) -> WidgetContext:
    verify_origin_or_403(request, ctx.tenant_id)
    return ctx


def _terms_payload(req: SaveGradPlanRequest) -> list[dict[str, Any]]:
    terms: list[dict[str, Any]] = []
    for t in req.terms:
        codes: list[str] = []
        for c in t.courses:
            codes.append(c if isinstance(c, str) else c.code)
        terms.append({"term": t.term, "year": t.year, "course_codes": codes})
    return terms


@router.get("/grad/active", response_model=GradPlanResponse)
async def get_active_grad_plan(
    current_user: Annotated[WidgetContext, Depends(_get_current_user)],
    session: Annotated[Any, Depends(db_with_tenant)],
) -> GradPlanResponse:
    result = await load_active_grad_plan(
        session,
        tenant_id=UUID(current_user.tenant_id),
        student_id=UUID(current_user.student_id),
    )
    if result is None:
        return GradPlanResponse(message="You do not have a saved graduation plan.")
    return GradPlanResponse(message=result.message, plan=result.card)


@router.post("/grad/active", response_model=GradPlanResponse)
async def save_grad_plan(
    payload: SaveGradPlanRequest,
    current_user: Annotated[WidgetContext, Depends(_get_current_user)],
    session: Annotated[Any, Depends(db_with_tenant)],
) -> GradPlanResponse:
    try:
        result = await save_active_grad_plan(
            session,
            tenant_id=UUID(current_user.tenant_id),
            student_id=UUID(current_user.student_id),
            name=payload.name,
            terms=_terms_payload(payload),
            replace=payload.replace,
        )
    except GradPlanConflict as exc:
        return GradPlanResponse(
            message="Saving this plan would replace your existing graduation plan.",
            conflict=True,
            existing_plan_id=exc.existing_id,
            existing_name=exc.existing_name,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return GradPlanResponse(message=result.message, plan=result.card)


@router.delete("/grad/active", response_model=GradPlanResponse)
async def delete_grad_plan(
    current_user: Annotated[WidgetContext, Depends(_get_current_user)],
    session: Annotated[Any, Depends(db_with_tenant)],
) -> GradPlanResponse:
    result = await delete_active_grad_plan(
        session,
        tenant_id=UUID(current_user.tenant_id),
        student_id=UUID(current_user.student_id),
    )
    return GradPlanResponse(message=result.message)
