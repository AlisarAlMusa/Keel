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

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from keel.api.auth import mint_widget_token
from keel.logging import get_logger

router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)
_log = get_logger(__name__)


class MintTokenRequest(BaseModel):
    tenant_id: str
    student_id: str


class MintTokenResponse(BaseModel):
    token: str
    expires_in: int


def _verify_service_secret(request: Request) -> None:
    """Validate the portal_service_secret from the Authorization header."""
    auth = request.headers.get("Authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Service token required",
        )
    expected: str = getattr(request.app.state, "portal_service_secret", "")
    if not expected or token != expected:
        _log.warning("internal.bad_service_token", remote=request.client)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid service token",
        )


@router.post("/mint-token", response_model=MintTokenResponse)
async def mint_token(body: MintTokenRequest, request: Request) -> MintTokenResponse:
    """Mint a widget JWT for a student authenticated by the portal service.

    Called by the portal Express backend after verifying the student's session
    cookie.  The browser never reaches this endpoint.
    """
    _verify_service_secret(request)

    secret: str = request.app.state.widget_token_secret
    token = mint_widget_token(
        secret=secret,
        tenant_id=body.tenant_id,
        student_id=body.student_id,
    )

    _log.info(
        "internal.token_minted",
        tenant_id=body.tenant_id,
        student_id=body.student_id,
    )
    return MintTokenResponse(token=token, expires_in=900)
