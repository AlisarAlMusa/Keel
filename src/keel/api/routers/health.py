"""Health endpoints (contracts/health-endpoints.md).

- ``/healthz``  liveness — pure, never touches dependencies.
- ``/readyz``   readiness — checks DB and Redis reachability. (Vault is checked
  at boot; if Vault was down the app never starts, so reaching /readyz at all
  implies the Vault gate passed.)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from keel import __version__

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, Any]:
    """Liveness: the process is up and serving."""
    return {"status": "ok", "service": "keel-api", "version": __version__}


@router.get("/readyz")
async def readyz(request: Request, response: Response) -> dict[str, Any]:
    """Readiness: core dependencies are reachable."""
    checks: dict[str, str] = {"vault": "ok"}  # boot-gated; see module docstring

    # DB
    try:
        session_factory = request.app.state.session_factory
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception:  # noqa: BLE001
        checks["db"] = "error"

    # Redis
    try:
        from keel.infra.redis import ping

        checks["redis"] = "ok" if await ping(request.app.state.redis) else "error"
    except Exception:  # noqa: BLE001
        checks["redis"] = "error"

    ready = all(v == "ok" for v in checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", "checks": checks}
    return {"status": "ready", "checks": checks}
