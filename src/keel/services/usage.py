"""Per-tenant usage/cost accounting (kept out of the API router).

CLAUDE.md §7: routers parse/authorize/delegate/serialize — no business logic.
The chat router previously computed a token/cost estimate and wrote the
``usage_event`` row inline; that logic lives here now and is best-effort (a
failure never breaks the user-facing chat response).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from keel.infra.database.session import tenant_session
from keel.logging import get_logger

_log = get_logger(__name__)

# Rough char→token ratio and a per-token cost estimate for the lite model tier.
_CHARS_PER_TOKEN = 4
_COST_PER_TOKEN = 0.000_000_18


async def record_chat_usage(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    tenant_id: str,
    routed_to_agent: bool,
    model: str,
    message_len: int,
    response_len: int,
) -> None:
    """Best-effort insert of one usage_event row (tenant-scoped). Never raises."""
    tokens = (message_len + response_len) // _CHARS_PER_TOKEN
    cost = round(tokens * _COST_PER_TOKEN, 8)
    # usage_event.kind is constrained to 'llm' | 'embedding'. A chat turn (agent or
    # direct classifier path) is an LLM cost; the model column records which model.
    # The previous 'agent'/'classifier' values violated the CHECK and were silently
    # swallowed, so no cost was ever recorded.
    try:
        async with tenant_session(session_factory, UUID(tenant_id)) as session:
            await session.execute(
                text(
                    "INSERT INTO usage_event (tenant_id, kind, model, tokens, cost_estimate) "
                    "VALUES (:tid, 'llm', :model, :tokens, :cost)"
                ),
                {"tid": tenant_id, "model": model, "tokens": tokens, "cost": cost},
            )
    except SQLAlchemyError as exc:
        # Usage accounting is best-effort and must not break chat; a DB hiccup is
        # logged and swallowed. Non-DB errors (a bug in this block) are NOT caught
        # here — they surface so they can be fixed rather than silently masked.
        _log.warning("chat.usage_event_failed", error=str(exc))
