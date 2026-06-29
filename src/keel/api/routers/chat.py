"""Chat endpoint — the single student-facing interaction route.

Phase 5: wrapped with widget auth (get_widget_context + verify_origin +
db_with_tenant).  student_id and tenant_id now come ONLY from the verified
JWT — the request body carries only the message.

Request flow:
  1. get_widget_context  — verify Bearer JWT → WidgetContext
  2. verify_origin_or_403 — Origin in allowed list, else 403
  3. Mint request_id; run input guardrails.
  4. Build ContextEnvelope from the verified context + body.message.
  5. Route (classifier → workflow | agent).
  6. Redact and return.

No business logic here — routers parse, authorize, delegate to services.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Annotated, Any

import cohere
from fastapi import APIRouter, Depends, Request, status
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field

from keel.api.auth import WidgetContext, get_widget_context, verify_origin_or_403
from keel.api.deps import get_session
from keel.api.routers.auth_admin import assert_tenant_active
from keel.config import get_settings
from keel.domain.schemas import ContextEnvelope, RouterResult, StudentPreference
from keel.infra.guardrails import check_input, redact
from keel.infra.model_client import ModelClient
from keel.logging import get_logger
from keel.services.router import AgentCallable, RouterResponse, route
from keel.services.usage import record_chat_usage

router = APIRouter(prefix="/chat", tags=["chat"])
_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str
    session_id: str  # client-generated; used for Redis memory key


class ChatResponse(BaseModel):
    response: str
    request_id: str
    router: RouterResult
    # When the agent stages a write action, the widget needs the action_id and a
    # pending-approval flag to render the Approve/Decline control.
    action_id: str | None = None
    pending_approval: bool = False
    # G3: structured plan cards (widget PlanData shape) from propose_plan. The dicts
    # are produced internally by the planning tool and already match the widget
    # contract (camelCase totalCredits), so they pass straight through.
    plans: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Dependency helpers (singletons from app.state)
# ---------------------------------------------------------------------------


def _get_model_client(request: Request) -> ModelClient:
    return request.app.state.model_client  # type: ignore[no-any-return]


def _get_llm_lite(request: Request) -> ChatGoogleGenerativeAI:
    return request.app.state.llm_lite  # type: ignore[no-any-return]


def _get_cohere(request: Request) -> cohere.AsyncClientV2:
    return request.app.state.cohere_client  # type: ignore[no-any-return]


def _get_agent_run(request: Request) -> AgentCallable | None:
    return getattr(request.app.state, "agent_run", None)


def _get_redis(request: Request) -> Any:
    return request.app.state.redis


def _get_fallback_threshold(request: Request) -> float:
    return getattr(request.app.state, "fallback_threshold", 0.5115)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("", response_model=ChatResponse, status_code=status.HTTP_200_OK)
async def chat(
    body: ChatRequest,
    request: Request,
    ctx: Annotated[WidgetContext, Depends(get_widget_context)],
    model_client: Annotated[ModelClient, Depends(_get_model_client)],
    llm_lite: Annotated[ChatGoogleGenerativeAI, Depends(_get_llm_lite)],
    session: Annotated[Any, Depends(get_session)] = None,
) -> ChatResponse:
    """Single student message → guardrails → router → response.

    student_id and tenant_id come from the verified JWT (ctx), never from body.
    """
    # Origin check (defense-in-depth; JWT already verifies identity)
    verify_origin_or_403(request, ctx.tenant_id)

    # Suspend gate: if the tenant is suspended, the widget goes dark (403).
    # Note: tenants table has no RLS, so a plain session suffices here.
    if session is not None:
        await assert_tenant_active(ctx.tenant_id, session)

    request_id = str(uuid.uuid4())
    prompt_hash = hashlib.sha256(body.message.encode()).hexdigest()[:16]

    _log.info(
        "chat.request",
        request_id=request_id,
        tenant_id=ctx.tenant_id,
        student_id=ctx.student_id,
        session_id=body.session_id,
        prompt_hash=prompt_hash,
    )

    # --- Guardrails: input rail ---
    # Build list of other tenants' slugs+names for cross-tenant name detection.
    all_tenants: list[tuple[str, str, str]] = getattr(request.app.state, "tenant_names", [])
    other_tenant_names = [(slug, name) for tid, slug, name in all_tenants if tid != ctx.tenant_id]
    decision = check_input(
        body.message,
        tenant_id=ctx.tenant_id,
        other_tenant_names=other_tenant_names,
    )
    if not decision.safe:
        _log.warning(
            "chat.guardrail_refused",
            request_id=request_id,
            reason=decision.reason,
            tenant_id=ctx.tenant_id,
        )
        if decision.reason == "cross_tenant_probe":
            refusal = (
                "I can only access information for your institution. "
                "Accessing data from other universities is not permitted — "
                "each institution's data is private and isolated."
            )
        else:
            refusal = (
                "I'm not able to process that request. "
                "I'm here to help with your courses, plans, and academic advising."
            )
        return ChatResponse(
            response=refusal,
            request_id=request_id,
            router=RouterResult(label="guardrail", confidence=1.0, route_to_agent=False),
        )

    # --- Build envelope (identity from JWT, never from body) ---
    persona_prompt_map: dict[str, str] = getattr(request.app.state, "widget_persona_prompt_map", {})
    persona_prompt = persona_prompt_map.get(
        ctx.tenant_id, "You are Keel, a helpful AI academic advisor."
    )
    persona_name_map: dict[str, str] = getattr(request.app.state, "widget_persona_map", {})
    persona_name = persona_name_map.get(ctx.tenant_id, "Keel")
    envelope = ContextEnvelope(
        tenant_id=ctx.tenant_id,
        student_id=ctx.student_id,
        session_id=body.session_id,
        request_id=request_id,
        message=body.message,
        preferences=StudentPreference(),
        persona_prompt=persona_prompt,
        persona_name=persona_name,
    )

    # --- Route ---
    agent_run = _get_agent_run(request)
    fallback_threshold = _get_fallback_threshold(request)

    router_response: RouterResponse = await route(
        envelope=envelope,
        model_client=model_client,
        llm_lite=llm_lite,
        agent_run=agent_run,
        fallback_threshold=fallback_threshold,
    )

    # --- Guardrails: output rail ---
    safe_text = redact(router_response.text)
    content_hash = hashlib.sha256(safe_text.encode()).hexdigest()[:16]

    _log.info(
        "chat.response",
        request_id=request_id,
        label=router_response.label,
        confidence=router_response.confidence,
        routed_to_agent=router_response.routed_to_agent,
        content_hash=content_hash,
    )

    # --- Record usage event (best-effort — delegated to a service) ---
    await record_chat_usage(
        request.app.state.session_factory,
        tenant_id=ctx.tenant_id,
        routed_to_agent=router_response.routed_to_agent,
        model=get_settings().gemini_model,
        message_len=len(body.message),
        response_len=len(safe_text),
    )

    return ChatResponse(
        response=safe_text,
        request_id=request_id,
        router=RouterResult(
            label=router_response.label,
            confidence=router_response.confidence,
            route_to_agent=router_response.routed_to_agent,
        ),
        action_id=router_response.action_id,
        pending_approval=router_response.pending_approval,
        plans=router_response.plans,
    )


class NotificationItem(BaseModel):
    id: int
    kind: str
    body: str
    created_at: str


class NotificationsResponse(BaseModel):
    notifications: list[NotificationItem]


@router.get("/notifications", response_model=NotificationsResponse)
async def get_notifications(
    request: Request,
    ctx: Annotated[WidgetContext, Depends(get_widget_context)],
) -> NotificationsResponse:
    """Return + mark-read the student's UNREAD in-app notifications.

    The widget polls this so async events (e.g. a waitlist seat opening and the worker
    auto-enrolling) surface as Keel chat messages, not just email. Identity comes from
    the verified widget JWT; the read is RLS-scoped to the caller's tenant + student.
    Returning a row marks it read so it shows exactly once.
    """
    from sqlalchemy import text as _sql

    from keel.infra.database.session import tenant_session as _ts

    out: list[NotificationItem] = []
    async with _ts(request.app.state.session_factory, uuid.UUID(ctx.tenant_id)) as session:
        rows = await session.execute(
            _sql(
                "UPDATE notifications SET read_at = now() "
                "WHERE id IN (SELECT id FROM notifications "
                "WHERE tenant_id = :tid AND student_id = :sid AND read_at IS NULL "
                "ORDER BY created_at ASC LIMIT 20) "
                "RETURNING id, kind, body, created_at"
            ),
            {"tid": ctx.tenant_id, "sid": ctx.student_id},
        )
        for r in rows.mappings():
            out.append(
                NotificationItem(
                    id=int(r["id"]),
                    kind=str(r["kind"]),
                    body=str(r["body"]),
                    created_at=r["created_at"].isoformat() if r["created_at"] else "",
                )
            )
    return NotificationsResponse(notifications=out)
