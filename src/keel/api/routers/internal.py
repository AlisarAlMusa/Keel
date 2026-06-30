"""Internal service-to-service endpoints — callable only by trusted backend services.

Phase 5 (D-P5-005): The portal Express backend calls POST /internal/mint-token to
obtain a widget JWT for an authenticated student.  The browser never sees this
endpoint; it is protected by portal_service_secret (Vault), not by a widget token.

Security invariants:
  • portal_service_secret verified before minting — wrong/missing secret → 401.
  • student_id and tenant_id come from the portal server's verified session;
    the client cannot supply arbitrary IDs.
  • Token TTL is fixed at 900 s (15 min) — not caller-configurable.
  • Endpoint is excluded from the OpenAPI spec (include_in_schema=False) so it
    does not appear in public documentation or developer tools.
"""

from __future__ import annotations

import hmac
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text

from keel.api.auth import mint_widget_token
from keel.infra.database.session import tenant_session
from keel.logging import get_logger

router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)
_log = get_logger(__name__)


class MintTokenRequest(BaseModel):
    tenant_id: str
    student_id: str


class MintTokenResponse(BaseModel):
    token: str
    expires_in: int
    persona_name: str


def _verify_service_secret(request: Request) -> str | None:
    """Validate the portal service secret and return the tenant it authorizes.

    Returns the tenant_id whose per-portal secret matched (so mint can enforce
    that a portal only mints ITS OWN tenant's tokens), or ``None`` when the
    legacy shared secret matched (authorizes any tenant — backward compat).
    Raises 401 on a missing/unknown secret.
    """
    auth = request.headers.get("Authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Service token required",
        )

    # Per-portal secrets first: compare against every tenant's secret (constant
    # time, no early exit) so a timing side-channel can't recover one.
    per_tenant: dict[str, str] = getattr(request.app.state, "portal_service_secrets", {}) or {}
    matched_tenant: str | None = None
    for tid, secret in per_tenant.items():
        if secret and hmac.compare_digest(token, secret):
            matched_tenant = tid
    if matched_tenant is not None:
        return matched_tenant

    # Legacy shared secret (authorizes any tenant) — kept for backward compat.
    shared: str = getattr(request.app.state, "portal_service_secret", "")
    if shared and hmac.compare_digest(token, shared):
        return None

    _log.warning("internal.bad_service_token", remote=request.client)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid service token",
    )


async def _assert_student_in_tenant(request: Request, tenant_id: str, student_id: str) -> None:
    """Reject minting a token for a (tenant, student) pair that does not exist.

    Defence-in-depth on top of the shared service secret: even a portal that
    presents a valid secret can only mint tokens for students that actually
    belong to the tenant it names. The lookup runs under that tenant's RLS, so a
    student_id from another tenant simply isn't visible → 403.
    """
    try:
        tid = UUID(tenant_id)
        UUID(student_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed tenant_id/student_id"
        ) from exc

    session_factory = request.app.state.session_factory
    async with tenant_session(session_factory, tid) as session:
        row = await session.execute(
            text("SELECT 1 FROM students WHERE id = :sid AND tenant_id = :tid"),
            {"sid": student_id, "tid": tenant_id},
        )
        if row.first() is None:
            _log.warning(
                "internal.student_not_in_tenant", tenant_id=tenant_id, student_id=student_id
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Student does not belong to this tenant",
            )


@router.post("/mint-token", response_model=MintTokenResponse)
async def mint_token(body: MintTokenRequest, request: Request) -> MintTokenResponse:
    """Mint a widget JWT for a student authenticated by the portal service.

    Called by the portal Express backend after verifying the student's session
    cookie.  The browser never reaches this endpoint.
    """
    authorized_tenant = _verify_service_secret(request)
    # A per-portal secret may mint ONLY its own tenant's tokens. The legacy shared
    # secret (authorized_tenant is None) is allowed to name any tenant.
    if authorized_tenant is not None and authorized_tenant != body.tenant_id:
        _log.warning(
            "internal.cross_tenant_mint_blocked",
            secret_tenant=authorized_tenant,
            requested_tenant=body.tenant_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This portal's service secret cannot mint tokens for another tenant",
        )
    await _assert_student_in_tenant(request, body.tenant_id, body.student_id)

    secret: str = request.app.state.widget_token_secret
    token = mint_widget_token(
        secret=secret,
        tenant_id=body.tenant_id,
        student_id=body.student_id,
    )

    persona_map: dict[str, str] = getattr(request.app.state, "widget_persona_map", {})
    persona_name = persona_map.get(body.tenant_id, "Keel")

    _log.info(
        "internal.token_minted",
        tenant_id=body.tenant_id,
        student_id=body.student_id,
    )
    return MintTokenResponse(token=token, expires_in=900, persona_name=persona_name)
