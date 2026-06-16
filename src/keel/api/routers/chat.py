"""Chat endpoint — the single student-facing interaction route.

Request flow:
  1. Mint request_id at entry; propagate through all calls.
  2. Run input guardrails (injection + cross-tenant refusal).
  3. Build ContextEnvelope and call the router.
  4. Redact and return the response.

No business logic here — routers parse, authorize, delegate to services, serialize.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Annotated, Any

import cohere
from fastapi import APIRouter, Depends, HTTPException, Request, status
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel

from keel.domain.schemas import ContextEnvelope, RouterResult, StudentPreference
from keel.infra.guardrails import check_input, redact
from keel.infra.model_client import ModelClient
from keel.logging import get_logger
from keel.services.router import AgentCallable, RouterResponse, route

router = APIRouter(prefix="/chat", tags=["chat"])
_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str
    student_id: str
    session_id: str
    tenant_id: str


class ChatResponse(BaseModel):
    response: str
    request_id: str
    router: RouterResult


# ---------------------------------------------------------------------------
# Dependency helpers
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
    model_client: Annotated[ModelClient, Depends(_get_model_client)],
    llm_lite: Annotated[ChatGoogleGenerativeAI, Depends(_get_llm_lite)],
) -> ChatResponse:
    """Single student message → guardrails → router → response."""
    request_id = str(uuid.uuid4())
    prompt_hash = hashlib.sha256(body.message.encode()).hexdigest()[:16]

    _log.info(
        "chat.request",
        request_id=request_id,
        tenant_id=body.tenant_id,
        student_id=body.student_id,
        session_id=body.session_id,
        prompt_hash=prompt_hash,  # never log full text
    )

    # --- Guardrails: input rail ---
    decision = check_input(body.message, tenant_id=body.tenant_id)
    if not decision.safe:
        _log.warning(
            "chat.guardrail_refused",
            request_id=request_id,
            reason=decision.reason,
            tenant_id=body.tenant_id,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Message refused by content guardrails: {decision.reason}",
        )

    # --- Build envelope ---
    envelope = ContextEnvelope(
        tenant_id=body.tenant_id,
        student_id=body.student_id,
        session_id=body.session_id,
        request_id=request_id,
        message=body.message,
        preferences=StudentPreference(),
    )

    # --- Route (tools open their own DB sessions via session_factory in AgentDeps) ---
    agent_run = _get_agent_run(request)
    fallback_threshold = _get_fallback_threshold(request)

    router_response: RouterResponse = await route(
        envelope=envelope,
        model_client=model_client,
        llm_lite=llm_lite,
        agent_run=agent_run,
        fallback_threshold=fallback_threshold,
    )

    # --- Guardrails: output rail (redact) ---
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

    return ChatResponse(
        response=safe_text,
        request_id=request_id,
        router=RouterResult(
            label=router_response.label,
            confidence=router_response.confidence,
            route_to_agent=router_response.routed_to_agent,
        ),
    )
