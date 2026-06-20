"""Admin / operator authentication — POST /auth/login.

Issues a role-stamped JWT for tenant_admin and platform_operator accounts.
Auth dependencies (require_role, assert_tenant_active) for protecting routes
also live here so every auth concern is in one place.

Token shapes:
  tenant_admin:       { sub, role, tenant_id, iat, exp }
  platform_operator:  { sub, role, iat, exp }   ← no tenant_id by design

Password hashing: bcrypt (D-missing_recovery §S1).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from keel.api.deps import get_session
from keel.logging import get_logger

router = APIRouter(prefix="/auth", tags=["auth"])
_log = get_logger(__name__)

_ALGO = "HS256"
_ACCESS_TTL = 8 * 3600  # 8 hours — admin/operator sessions are long-lived


# ---------------------------------------------------------------------------
# Token issue
# ---------------------------------------------------------------------------


def _jwt_secret(request: Request) -> str:
    return request.app.state.jwt_signing_key  # type: ignore[no-any-return]


def issue_admin_token(secret: str, user_id: str, role: str, tenant_id: str | None) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": user_id,
        "role": role,
        "iat": now,
        "exp": now + _ACCESS_TTL,
    }
    if role == "tenant_admin" and tenant_id:
        claims["tenant_id"] = tenant_id
    # platform_operator: no tenant_id in token by design (spec §S1)
    return jwt.encode(claims, secret, algorithm=_ALGO)


# ---------------------------------------------------------------------------
# Login endpoint
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str
    role: str
    tenant_id: str | None = None
    tenant_name: str | None = None
    expires_in: int = _ACCESS_TTL


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> LoginResponse:
    """Email + password → role-stamped JWT for tenant_admin or platform_operator.

    Uses keel_find_user_by_email() SECURITY DEFINER so keel_app (NOBYPASSRLS)
    can look up the user before a session/tenant context exists.
    Generic 401 on any failure — no user enumeration.
    """
    rows = await session.execute(
        text("SELECT user_id, tenant_id, role, hashed_password FROM keel_find_user_by_email(:e)"),
        {"e": body.email},
    )
    row = rows.fetchone()

    if not row or not row.hashed_password:
        _log.warning("admin_login_failed", reason="user_not_found", email=body.email)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    pw_valid = bcrypt.checkpw(body.password.encode(), row.hashed_password.encode())
    if not pw_valid:
        _log.warning("admin_login_failed", reason="bad_password", email=body.email)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # Suspend gate: tenant_admin cannot log into Keel if tenant is suspended.
    # platform_operator has no tenant — skip the gate.
    tenant_id = str(row.tenant_id) if row.tenant_id else None
    if row.role == "tenant_admin" and tenant_id:
        await assert_tenant_active(tenant_id, session)

    secret = _jwt_secret(request)
    token = issue_admin_token(secret, str(row.user_id), row.role, tenant_id)

    tenant_name: str | None = None
    if tenant_id:
        t_row = await session.execute(
            text("SELECT name FROM tenants WHERE id = :tid"), {"tid": tenant_id}
        )
        t = t_row.fetchone()
        if t:
            tenant_name = t.name

    _log.info("admin_login_success", role=row.role, user_id=str(row.user_id))
    return LoginResponse(token=token, role=row.role, tenant_id=tenant_id, tenant_name=tenant_name)


# ---------------------------------------------------------------------------
# Auth context dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdminContext:
    user_id: str
    role: str
    tenant_id: str | None  # None for platform_operator


# ---------------------------------------------------------------------------
# require_role() dependency — protects any route by role
# ---------------------------------------------------------------------------


def require_role(role: str):  # type: ignore[no-untyped-def]
    """FastAPI dependency factory: assert the Bearer JWT carries the given role.

    Usage:
        @router.get("/admin/...")
        async def ep(ctx: AdminContext = Depends(require_role("tenant_admin"))):
            ...
    """

    async def _dep(
        request: Request,
        authorization: str | None = None,
    ) -> AdminContext:
        auth = request.headers.get("Authorization", "")
        if not auth:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authorization header required",
            )
        scheme, _, token = auth.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Bearer token required",
            )

        secret = _jwt_secret(request)
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                secret,
                algorithms=[_ALGO],
                options={"require": ["sub", "role", "exp"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired"
            ) from exc
        except jwt.PyJWTError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
            ) from exc

        token_role: str = claims.get("role", "")
        if token_role != role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{role}' required; token carries '{token_role}'",
            )

        return AdminContext(
            user_id=claims["sub"],
            role=token_role,
            tenant_id=claims.get("tenant_id"),
        )

    return _dep


# ---------------------------------------------------------------------------
# assert_tenant_active() — suspend gate
# ---------------------------------------------------------------------------


async def assert_tenant_active(tenant_id: str, session: AsyncSession) -> None:
    """Raise 403 if the tenant is suspended or does not exist.

    Applied at the Keel boundary only:
      - /portal/keel-token mint (in the portal server, via its own mechanism)
      - /chat
      - Keel admin login (POST /auth/login) when role == 'tenant_admin'

    NOT applied at /portal/login or /portal/* SIS reads — suspension darkens
    Keel (the AI layer), not the university's own SIS portal.
    """
    result = await session.execute(
        text("SELECT status FROM tenants WHERE id = :t"),
        {"t": tenant_id},
    )
    row = result.fetchone()
    if not row or row.status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant is suspended — Keel services are unavailable for your institution",
        )
