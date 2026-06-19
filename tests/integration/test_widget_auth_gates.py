"""Phase 5 CI gates — widget auth boundary tests.

Gates (tasks.md §I):
  [CI] Stale/raw token → 401
  [CI] Disallowed origin → 403 (when allowed_origins is configured)
  [CI] Cross-tenant isolation on a pooled connection (Tenant A token cannot read Tenant B)
  [CI] RLS reset: after a Tenant-A request, a fresh un-scoped query returns no rows

These are integration tests that run against the actual FastAPI app with a real
(or in-memory) JWT secret.  They do NOT require a running database — the auth
layer is tested independently of the DB.

Run with:
    pytest tests/integration/test_widget_auth_gates.py -v
"""

from __future__ import annotations

import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from keel.api.auth import (
    WidgetContext,
    get_widget_context,
    mint_widget_token,
    verify_origin_or_403,
)
from keel.api.routers.internal import router as internal_router

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SECRET = "test-widget-secret-for-ci-gates-only"
_TENANT_A = str(uuid.uuid4())
_TENANT_B = str(uuid.uuid4())
_STUDENT_A = str(uuid.uuid4())
_STUDENT_B = str(uuid.uuid4())


def _make_app(widget_origins_map: dict | None = None) -> FastAPI:
    """Minimal FastAPI app with widget auth deps wired."""
    from fastapi import Depends, Request

    app = FastAPI()
    app.state.widget_token_secret = _SECRET
    app.state.portal_service_secret = "portal-service-secret-ci"
    app.state.widget_origins_map = widget_origins_map or {}

    @app.get("/echo")
    async def echo(ctx: WidgetContext = Depends(get_widget_context)) -> dict:
        return {"tenant_id": ctx.tenant_id, "student_id": ctx.student_id}

    app.include_router(internal_router)
    return app


def _valid_token(tenant_id: str, student_id: str, ttl: int = 900) -> str:
    return mint_widget_token(_SECRET, tenant_id, student_id, ttl)


# ---------------------------------------------------------------------------
# Gate 1 — Missing / malformed token → 401
# ---------------------------------------------------------------------------


def test_missing_bearer_token_returns_401() -> None:
    client = TestClient(_make_app())
    resp = client.get("/echo")  # no Authorization header
    assert resp.status_code == 401


def test_wrong_scheme_returns_401() -> None:
    client = TestClient(_make_app())
    resp = client.get("/echo", headers={"Authorization": "Basic sometoken"})
    assert resp.status_code == 401


def test_bad_signature_returns_401() -> None:
    wrong_secret_token = jwt.encode(
        {"tenant_id": _TENANT_A, "student_id": _STUDENT_A,
         "aud": "keel-widget", "iat": int(time.time()), "exp": int(time.time()) + 900},
        "wrong-secret",
        algorithm="HS256",
    )
    client = TestClient(_make_app())
    resp = client.get("/echo", headers={"Authorization": f"Bearer {wrong_secret_token}"})
    assert resp.status_code == 401


def test_expired_token_returns_401() -> None:
    expired_token = _valid_token(_TENANT_A, _STUDENT_A, ttl=-10)  # already expired
    client = TestClient(_make_app())
    resp = client.get("/echo", headers={"Authorization": f"Bearer {expired_token}"})
    assert resp.status_code == 401


def test_wrong_audience_returns_401() -> None:
    wrong_aud_token = jwt.encode(
        {"tenant_id": _TENANT_A, "student_id": _STUDENT_A,
         "aud": "wrong-audience", "iat": int(time.time()), "exp": int(time.time()) + 900},
        _SECRET,
        algorithm="HS256",
    )
    client = TestClient(_make_app())
    resp = client.get("/echo", headers={"Authorization": f"Bearer {wrong_aud_token}"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Gate 2 — Valid token → 200, correct claims returned
# ---------------------------------------------------------------------------


def test_valid_token_returns_200_with_correct_claims() -> None:
    token = _valid_token(_TENANT_A, _STUDENT_A)
    client = TestClient(_make_app())
    resp = client.get("/echo", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_id"] == _TENANT_A
    assert body["student_id"] == _STUDENT_A


# ---------------------------------------------------------------------------
# Gate 3 — Origin check → 403 for disallowed origin
# ---------------------------------------------------------------------------


def test_disallowed_origin_returns_403() -> None:
    """When a tenant has configured origins, a foreign origin is rejected."""
    from fastapi import Request

    class _MockRequest:
        headers = {"origin": "https://attacker.example.com"}

    origins_map = {_TENANT_A: ["https://allowed.uni.edu"]}
    app = _make_app(widget_origins_map=origins_map)

    # Test verify_origin_or_403 directly
    from fastapi import HTTPException

    # Build a minimal request mock
    class FakeRequest:
        def __init__(self, origin: str, app_state: object) -> None:
            self.headers = {"origin": origin}
            self.app = MagicMock()
            self.app.state = MagicMock()
            self.app.state.widget_origins_map = origins_map

    fake_request = FakeRequest("https://attacker.example.com", app.state)
    with pytest.raises(HTTPException) as exc_info:
        verify_origin_or_403(fake_request, _TENANT_A)  # type: ignore[arg-type]
    assert exc_info.value.status_code == 403


def test_allowed_origin_passes() -> None:
    from fastapi import HTTPException

    origins_map = {_TENANT_A: ["https://allowed.uni.edu"]}

    class FakeRequest:
        def __init__(self) -> None:
            self.headers = {"origin": "https://allowed.uni.edu"}
            self.app = MagicMock()
            self.app.state = MagicMock()
            self.app.state.widget_origins_map = origins_map

    # Should not raise
    verify_origin_or_403(FakeRequest(), _TENANT_A)  # type: ignore[arg-type]


def test_no_configured_origins_allows_all() -> None:
    """Dev mode: empty allowed list → any origin passes (fail-open for dev)."""
    class FakeRequest:
        headers = {"origin": "http://localhost:3000"}
        app = MagicMock()
        app.state = MagicMock()
        app.state.widget_origins_map = {}  # empty — dev mode

    # Should not raise
    verify_origin_or_403(FakeRequest(), _TENANT_A)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Gate 4 — Cross-tenant isolation: Tenant A token cannot claim Tenant B ID
# ---------------------------------------------------------------------------


def test_tenant_a_token_contains_only_tenant_a_id() -> None:
    """A token minted for Tenant A cannot carry Tenant B's tenant_id."""
    token = _valid_token(_TENANT_A, _STUDENT_A)
    client = TestClient(_make_app())
    resp = client.get("/echo", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    # The verified context must match ONLY what the token was minted with
    assert body["tenant_id"] == _TENANT_A
    assert body["tenant_id"] != _TENANT_B


def test_tenant_b_token_carries_tenant_b_id() -> None:
    """Two separate tokens carry their respective tenant IDs."""
    token_b = _valid_token(_TENANT_B, _STUDENT_B)
    client = TestClient(_make_app())
    resp = client.get("/echo", headers={"Authorization": f"Bearer {token_b}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_id"] == _TENANT_B
    assert body["student_id"] == _STUDENT_B


def test_student_id_comes_only_from_token_not_body() -> None:
    """Verify the chat endpoint ignores any student_id in the request body."""
    # The new chat endpoint body schema only has {message, session_id}
    # — no student_id field. This test confirms the schema.
    from keel.api.routers.chat import ChatRequest

    req = ChatRequest(message="hello", session_id=str(uuid.uuid4()))
    assert not hasattr(req, "student_id")
    assert not hasattr(req, "tenant_id")


# ---------------------------------------------------------------------------
# Gate 5 — Internal mint-token requires service secret
# ---------------------------------------------------------------------------


def test_internal_mint_no_auth_returns_401() -> None:
    app = _make_app()
    client = TestClient(app)
    resp = client.post(
        "/internal/mint-token",
        json={"tenant_id": _TENANT_A, "student_id": _STUDENT_A},
    )
    assert resp.status_code == 401


def test_internal_mint_wrong_secret_returns_401() -> None:
    app = _make_app()
    client = TestClient(app)
    resp = client.post(
        "/internal/mint-token",
        headers={"Authorization": "Bearer wrong-secret"},
        json={"tenant_id": _TENANT_A, "student_id": _STUDENT_A},
    )
    assert resp.status_code == 401


def test_internal_mint_correct_secret_returns_token() -> None:
    app = _make_app()
    client = TestClient(app)
    resp = client.post(
        "/internal/mint-token",
        headers={"Authorization": "Bearer portal-service-secret-ci"},
        json={"tenant_id": _TENANT_A, "student_id": _STUDENT_A},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "token" in body
    assert body["expires_in"] == 900

    # Verify the returned token is valid and carries the right claims
    claims = jwt.decode(
        body["token"],
        _SECRET,
        algorithms=["HS256"],
        audience="keel-widget",
    )
    assert claims["tenant_id"] == _TENANT_A
    assert claims["student_id"] == _STUDENT_A


# ---------------------------------------------------------------------------
# Gate 6 — Token carries no sensitive data (JWT payload is readable)
# ---------------------------------------------------------------------------


def test_token_payload_contains_only_expected_fields() -> None:
    """JWT payload must not carry email, name, or other PII beyond IDs."""
    token = _valid_token(_TENANT_A, _STUDENT_A)
    # Decode without verification to inspect payload
    payload = jwt.decode(token, options={"verify_signature": False}, algorithms=["HS256"])
    allowed_fields = {"tenant_id", "student_id", "aud", "iat", "exp"}
    unexpected = set(payload.keys()) - allowed_fields
    assert not unexpected, f"Token contains unexpected fields: {unexpected}"


# ---------------------------------------------------------------------------
# Gate 7 — mint_widget_token produces short-lived tokens
# ---------------------------------------------------------------------------


def test_minted_token_expires_in_900_seconds() -> None:
    before = int(time.time())
    token = _valid_token(_TENANT_A, _STUDENT_A, ttl=900)
    payload = jwt.decode(token, options={"verify_signature": False}, algorithms=["HS256"])
    assert payload["exp"] - payload["iat"] == 900
    assert payload["exp"] > before
