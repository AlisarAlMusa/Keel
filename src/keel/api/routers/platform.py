"""Platform operator endpoints — /platform/* (spec §S2, plan §P4).

All routes require role == 'platform_operator'.  None of these endpoints:
  - depend on tenant RLS or db_with_tenant
  - import a tenant-content repository
  - return rows from tenant-content tables (conversations, plans, transcripts, RAG)

CI gate: grep asserts nothing under /platform imports a tenant-content repository.

Endpoints:
  GET  /platform/tenants                  — list tenants + lightweight counts
  POST /platform/tenants                  — provision new tenant shell + bootstrap admin
  POST /platform/tenants/{id}/suspend     — set status='suspended'
  POST /platform/tenants/{id}/unsuspend   — set status='active'
  POST /platform/tenants/{id}/erase       — confirmation-gated → enqueue erase job
  GET  /platform/cost?period=             — aggregate usage metadata (no content)
  GET  /platform/audit?limit=             — read platform_audit log
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any
from uuid import UUID

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from keel.api.deps import get_session
from keel.api.routers.auth_admin import AdminContext, require_role
from keel.infra.database.models import PlatformAudit, Tenant, User
from keel.logging import get_logger

router = APIRouter(
    prefix="/platform",
    tags=["platform"],
    dependencies=[Depends(require_role("platform_operator"))],
)
_log = get_logger(__name__)

_REQUIRE_OPERATOR = require_role("platform_operator")



# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class TenantRow(BaseModel):
    id: str
    slug: str
    name: str
    status: str
    created_at: str
    student_count: int
    admin_count: int


class TenantListResponse(BaseModel):
    tenants: list[TenantRow]


class ProvisionRequest(BaseModel):
    name: str
    admin_email: str


class ProvisionResponse(BaseModel):
    tenant_id: str
    admin_email: str
    status: str = "provisioned"


class SuspendResponse(BaseModel):
    tenant_id: str
    status: str


class EraseRequest(BaseModel):
    confirm_name: str


class EraseResponse(BaseModel):
    tenant_id: str
    status: str = "queued"


class CostTenantRow(BaseModel):
    tenant_id: str
    kind: str
    calls: int
    tokens: int
    cost_usd: float


class CostResponse(BaseModel):
    period: str
    rows: list[CostTenantRow]
    note: str = "usage metadata only — no conversation content"


class PlatformAuditRow(BaseModel):
    id: int
    action: str
    target_tenant_id: str | None
    target_tenant_name: str | None = None
    detail: dict[str, Any] | None
    created_at: str


class PlatformAuditResponse(BaseModel):
    rows: list[PlatformAuditRow]


# ---------------------------------------------------------------------------
# Helper: write a platform_audit row (no tenant context needed)
# ---------------------------------------------------------------------------


async def _write_audit(
    session: AsyncSession,
    actor_user_id: str,
    action: str,
    target_tenant_id: str | None,
    detail: dict[str, Any] | None = None,
) -> None:
    session.add(
        PlatformAudit(
            actor_user_id=UUID(actor_user_id),
            action=action,
            target_tenant_id=UUID(target_tenant_id) if target_tenant_id else None,
            detail=detail,
        )
    )


# ---------------------------------------------------------------------------
# GET /platform/tenants
# ---------------------------------------------------------------------------


@router.get("/tenants", response_model=TenantListResponse)
async def list_tenants(
    request: Request,
    ctx: Annotated[AdminContext, Depends(require_role("platform_operator"))],
    session: AsyncSession = Depends(get_session),
) -> TenantListResponse:
    """List all tenants with lightweight platform-domain counts (no content)."""
    result = await session.execute(select(Tenant).order_by(Tenant.created_at))
    tenants = result.scalars().all()

    rows: list[TenantRow] = []
    for t in tenants:
        # SECURITY DEFINER functions bypass RLS for platform aggregate counts.
        # The operator never receives row-level content — only totals.
        st_count = await session.scalar(
            text("SELECT platform_count_students(CAST(:tid AS uuid))"),
            {"tid": str(t.id)},
        )
        adm_count = await session.scalar(
            text("SELECT platform_count_admins(CAST(:tid AS uuid))"),
            {"tid": str(t.id)},
        )
        rows.append(
            TenantRow(
                id=str(t.id),
                slug=t.slug,
                name=t.name,
                status=t.status,
                created_at=t.created_at.isoformat(),
                student_count=int(st_count or 0),
                admin_count=int(adm_count or 0),
            )
        )

    return TenantListResponse(tenants=rows)


# ---------------------------------------------------------------------------
# POST /platform/tenants — provision
# ---------------------------------------------------------------------------


@router.post("/tenants", response_model=ProvisionResponse, status_code=status.HTTP_201_CREATED)
async def provision_tenant(
    body: ProvisionRequest,
    request: Request,
    ctx: Annotated[AdminContext, Depends(require_role("platform_operator"))],
    session: AsyncSession = Depends(get_session),
) -> ProvisionResponse:
    """Provision a new Keel tenant shell + bootstrap tenant_admin.

    Creates: 1 tenants row + 1 users row (tenant_admin).
    No catalog, no students, no sis_integration.  Audit row written.
    """
    async with session.begin():
        tenant = Tenant(
            id=uuid.uuid4(),
            slug=body.name.lower().replace(" ", "-"),
            name=body.name,
            status="active",
        )
        session.add(tenant)
        await session.flush()

        # Bootstrap admin — temp password (operator must reset in production)
        temp_pw = f"keel-temp-{str(tenant.id)[:8]}"
        hashed = bcrypt.hashpw(temp_pw.encode(), bcrypt.gensalt()).decode()
        admin = User(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            email=body.admin_email,
            role="tenant_admin",
            hashed_password=hashed,
        )
        session.add(admin)

        await _write_audit(
            session,
            ctx.user_id,
            "provision",
            str(tenant.id),
            {"admin_email": body.admin_email, "name": body.name},
        )

    _log.info("platform.provision", tenant_id=str(tenant.id), admin_email=body.admin_email)
    return ProvisionResponse(tenant_id=str(tenant.id), admin_email=body.admin_email)


# ---------------------------------------------------------------------------
# POST /platform/tenants/{id}/suspend
# ---------------------------------------------------------------------------


@router.post("/tenants/{tenant_id}/suspend", response_model=SuspendResponse)
async def suspend_tenant(
    tenant_id: UUID,
    request: Request,
    ctx: Annotated[AdminContext, Depends(require_role("platform_operator"))],
    session: AsyncSession = Depends(get_session),
) -> SuspendResponse:
    async with session.begin():
        row = await session.get(Tenant, tenant_id)
        if not row:
            raise HTTPException(status_code=404, detail="Tenant not found")
        row.status = "suspended"
        await _write_audit(session, ctx.user_id, "suspend", str(tenant_id))

    _log.info("platform.suspend", tenant_id=str(tenant_id))
    return SuspendResponse(tenant_id=str(tenant_id), status="suspended")


# ---------------------------------------------------------------------------
# POST /platform/tenants/{id}/unsuspend
# ---------------------------------------------------------------------------


@router.post("/tenants/{tenant_id}/unsuspend", response_model=SuspendResponse)
async def unsuspend_tenant(
    tenant_id: UUID,
    request: Request,
    ctx: Annotated[AdminContext, Depends(require_role("platform_operator"))],
    session: AsyncSession = Depends(get_session),
) -> SuspendResponse:
    async with session.begin():
        row = await session.get(Tenant, tenant_id)
        if not row:
            raise HTTPException(status_code=404, detail="Tenant not found")
        row.status = "active"
        await _write_audit(session, ctx.user_id, "unsuspend", str(tenant_id))

    _log.info("platform.unsuspend", tenant_id=str(tenant_id))
    return SuspendResponse(tenant_id=str(tenant_id), status="active")


# ---------------------------------------------------------------------------
# POST /platform/tenants/{id}/erase — confirmation-gated, async
# ---------------------------------------------------------------------------


@router.post("/tenants/{tenant_id}/erase", response_model=EraseResponse)
async def erase_tenant(
    tenant_id: UUID,
    body: EraseRequest,
    request: Request,
    ctx: Annotated[AdminContext, Depends(require_role("platform_operator"))],
    session: AsyncSession = Depends(get_session),
) -> EraseResponse:
    """Confirmation-gated erase — enqueues an async RQ job.

    body.confirm_name must exactly match the tenant's name.
    Returns {status: 'queued'} immediately; the worker does the actual cascade.
    """
    row = await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
    if not row:
        raise HTTPException(status_code=404, detail="Tenant not found")

    if body.confirm_name != row.name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"confirm_name must exactly match the tenant name '{row.name}'",
        )

    # Enqueue the erase job via RQ
    from redis import Redis
    from rq import Queue

    from keel.config import get_settings

    settings = get_settings()
    redis_conn = Redis.from_url(settings.redis_url)
    q = Queue("keel", connection=redis_conn)
    q.enqueue(
        "keel.workers.platform_jobs.erase_tenant_job",
        kwargs={"tenant_id_str": str(tenant_id)},
        job_timeout=600,
    )

    # Audit the erase request immediately (before the job runs)
    async with session.begin():
        await _write_audit(
            session,
            ctx.user_id,
            "erase",
            str(tenant_id),
            {"requested": True, "tenant_name": row.name},
        )

    _log.info("platform.erase_queued", tenant_id=str(tenant_id))
    return EraseResponse(tenant_id=str(tenant_id))


# ---------------------------------------------------------------------------
# GET /platform/cost
# ---------------------------------------------------------------------------


@router.get("/cost", response_model=CostResponse)
async def get_cost(
    request: Request,
    ctx: Annotated[AdminContext, Depends(require_role("platform_operator"))],
    session: AsyncSession = Depends(get_session),
    period: str = "week",
) -> CostResponse:
    """Return per-tenant aggregate usage metadata — no content returned.

    Calls the platform_usage_summary() SECURITY DEFINER aggregate function;
    never queries usage_event directly.
    """
    if period not in ("day", "week", "month"):
        period = "week"

    rows_result = await session.execute(
        text("SELECT tenant_id, kind, calls, tokens, cost FROM platform_usage_summary(:p)"),
        {"p": period},
    )

    cost_rows = [
        CostTenantRow(
            tenant_id=str(r.tenant_id),
            kind=r.kind,
            calls=int(r.calls),
            tokens=int(r.tokens),
            cost_usd=float(r.cost),
        )
        for r in rows_result
    ]
    return CostResponse(period=period, rows=cost_rows)


# ---------------------------------------------------------------------------
# GET /platform/audit
# ---------------------------------------------------------------------------


@router.get("/audit", response_model=PlatformAuditResponse)
async def get_audit(
    request: Request,
    ctx: Annotated[AdminContext, Depends(require_role("platform_operator"))],
    session: AsyncSession = Depends(get_session),
    limit: int = 50,
) -> PlatformAuditResponse:
    """Read-only view of the platform_audit log."""
    result = await session.execute(
        select(PlatformAudit).order_by(PlatformAudit.created_at.desc()).limit(limit)
    )
    audit_list = list(result.scalars())

    # Build tenant_id → name map from the unique target_tenant_ids in this batch
    target_ids = {str(r.target_tenant_id) for r in audit_list if r.target_tenant_id}
    tenant_name_map: dict[str, str] = {}
    if target_ids:
        t_rows = await session.execute(
            text("SELECT id::text, name FROM tenants WHERE id::text = ANY(:ids)"),
            {"ids": list(target_ids)},
        )
        for tid, tname in t_rows:
            tenant_name_map[tid] = tname

    audit_rows = [
        PlatformAuditRow(
            id=r.id,
            action=r.action,
            target_tenant_id=str(r.target_tenant_id) if r.target_tenant_id else None,
            target_tenant_name=tenant_name_map.get(str(r.target_tenant_id)) if r.target_tenant_id else None,
            detail=r.detail,
            created_at=r.created_at.isoformat(),
        )
        for r in audit_list
    ]
    return PlatformAuditResponse(rows=audit_rows)
