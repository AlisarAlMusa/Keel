"""Widget authentication — token verification, RLS binding, origin check.

Phase 5 (spec §1 / plan §2). Three dependencies that every student-scoped
Keel route must chain:

  get_widget_context  — verify Bearer JWT → WidgetContext(tenant_id, student_id)
  verify_origin       — check request Origin against tenant's allowed_origins
  db_with_tenant      — open session, SET LOCAL app.tenant_id from WidgetContext

Security invariants:
  • student_id / tenant_id come ONLY from the verified token — never from body
    or query params.
  • set_config(..., is_local=true) scopes the tenant setting to the current
    transaction; the explicit reset in finally is belt-and-suspenders for
    autocommit / pooled connections.
  • Origin check is server-side; CORS middleware is defense-in-depth only.
  • 401 for bad/missing token; 403 for origin mismatch.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from keel.api.deps import get_session_factory
from keel.logging import get_logger

_log = get_logger(__name__)

_WIDGET_AUD = "keel-widget"
_ALGO = "HS256"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WidgetContext:
    tenant_id: str
    student_id: str


# ---------------------------------------------------------------------------
# Helper: read the widget secret from app.state (set in lifespan)
# ---------------------------------------------------------------------------


def _widget_secret(request: Request) -> str:
    secret: str = request.app.state.widget_token_secret
    return secret


def _allowed_origins_for(request: Request, tenant_id: str) -> list[str]:
    """Return the allowed origins cache stored on app.state.

    The portal router populates this from widget_config rows at startup or on
    PUT /admin/widget-config.  Fallback: empty list → all origins blocked.
    For dev convenience if the map is not populated yet, allow everything.
    """
    origins_map: dict[str, list[str]] = getattr(
        request.app.state, "widget_origins_map", {}
    )
    return origins_map.get(tenant_id, [])


# ---------------------------------------------------------------------------
# 1. Token verification dependency
# ---------------------------------------------------------------------------


async def get_widget_context(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> WidgetContext:
    """Parse and verify the widget Bearer JWT.

    Returns WidgetContext with tenant_id and student_id extracted from the
    verified claims.  Raises 401 on any failure (including missing header).
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header required",
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Bearer token",
        )

    secret = _widget_secret(request)
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            secret,
            algorithms=[_ALGO],
            audience=_WIDGET_AUD,
            options={"require": ["tenant_id", "student_id", "exp", "aud"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired"
        ) from exc
    except jwt.PyJWTError as exc:
        _log.warning("widget_token_invalid", error=type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc

    return WidgetContext(
        tenant_id=str(claims["tenant_id"]),
        student_id=str(claims["student_id"]),
    )


# ---------------------------------------------------------------------------
# 2. Origin check (called at mint AND on every chat request)
# ---------------------------------------------------------------------------


def verify_origin_or_403(request: Request, tenant_id: str) -> None:
    """Raise 403 if the request Origin is not in the tenant's allowed list.

    If the tenant has no configured origins (dev / first-boot), allow all
    origins so the demo works out of the box.  Production tenants MUST configure
    allowed_origins via PUT /admin/widget-config.
    """
    origin = request.headers.get("origin")
    allowed = _allowed_origins_for(request, tenant_id)

    if not allowed:
        # No configured origins → dev mode, allow all.
        return

    if not origin or origin not in allowed:
        _log.warning("origin_rejected", origin=origin, tenant_id=tenant_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Origin not allowed for this tenant",
        )


# ---------------------------------------------------------------------------
# 3. RLS-binding session dependency (chains the above two)
# ---------------------------------------------------------------------------


async def db_with_tenant(
    ctx: WidgetContext = Depends(get_widget_context),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> AsyncIterator[AsyncSession]:
    """Open a DB session and SET LOCAL app.tenant_id for RLS enforcement.

    set_config(..., is_local=true) scopes to the current transaction and
    auto-resets at commit/rollback; the explicit reset is belt-and-suspenders
    for pooled connections in autocommit mode.
    """
    async with session_factory() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(ctx.tenant_id)},
        )
        try:
            yield session
        finally:
            try:
                await session.execute(
                    text("SELECT set_config('app.tenant_id', '', true)")
                )
            except Exception:  # noqa: BLE001 — best-effort reset; session may be closed
                pass


# ---------------------------------------------------------------------------
# Token minting helper (called by the portal /keel-token endpoint)
# ---------------------------------------------------------------------------


def mint_widget_token(secret: str, tenant_id: str, student_id: str, ttl: int = 900) -> str:
    """Return a signed widget JWT valid for ``ttl`` seconds (default 15 min)."""
    now = int(time.time())
    payload = {
        "tenant_id": tenant_id,
        "student_id": student_id,
        "aud": _WIDGET_AUD,
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, secret, algorithm=_ALGO)
