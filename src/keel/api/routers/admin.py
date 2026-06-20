"""Admin endpoints — tenant-admin configuration + RAG upload.

Auth: Bearer JWT with role=tenant_admin (issued by POST /auth/login).
      Tenant context is read from the JWT claim — no extra header needed.

Routes:
  POST /admin/rag/upload              — chunk + embed prose docs → pgvector
  GET  /admin/widget-config           — per-tenant widget config
  PUT  /admin/widget-config           — update persona / origins / tools
  GET  /admin/widget-snippet          — <script> embed tag
  GET  /admin/cost?period=week        — usage_event aggregation
  GET  /admin/audit?limit=            — read-only audit log
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from pydantic import BaseModel
from redis import Redis
from rq import Queue
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from keel.api.routers.auth_admin import AdminContext, require_role
from keel.infra import storage as storage_infra
from keel.infra.database.models import AuditLog, UsageEvent, WidgetConfig
from keel.logging import get_logger
from keel.workers.ingestion_jobs import run_ingest_source

router = APIRouter(prefix="/admin", tags=["admin"])
_log = get_logger(__name__)

_RQ_QUEUE = "keel"

_require_admin = Depends(require_role("tenant_admin"))


def _get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.session_factory  # type: ignore[no-any-return]


async def _scoped_session(
    session_factory: async_sessionmaker[AsyncSession], tenant_id: str
) -> AsyncSession:
    session = session_factory()
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"),
        {"tid": tenant_id},
    )
    return session


# ---------------------------------------------------------------------------
# RAG upload
# ---------------------------------------------------------------------------


class DocumentUploadResponse(BaseModel):
    source: str
    job_id: str
    chunks_estimated: int
    status: str = "enqueued"


@router.post(
    "/rag/upload",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_rag_document(
    file: UploadFile,
    request: Request,
    ctx: AdminContext = _require_admin,
) -> DocumentUploadResponse:
    """Upload a prose markdown doc → chunk → embed into pgvector."""
    tenant_id = ctx.tenant_id
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant_id missing from token",
        )

    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="filename required")

    content_bytes = await file.read()
    if not content_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty file")

    source = f"{tenant_id}/{file.filename}"
    s3_client = request.app.state.storage

    from keel.config import get_settings

    settings = get_settings()
    try:
        storage_infra.put_text(s3_client, settings.minio_bucket, source, content_bytes.decode())
    except Exception as exc:
        _log.error("admin.upload_failed", tenant_id=tenant_id, source=source, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"MinIO upload failed: {type(exc).__name__}",
        ) from exc

    redis_conn = Redis.from_url(settings.redis_url)
    q = Queue(_RQ_QUEUE, connection=redis_conn)
    job = q.enqueue(
        run_ingest_source,
        kwargs={"tenant_id_str": tenant_id, "source": source, "chunk_type": "policy"},
    )

    chunks_est = max(1, len(content_bytes) // 400)
    _log.info("admin.rag_enqueued", tenant_id=tenant_id, source=source, job_id=job.id)
    return DocumentUploadResponse(source=source, job_id=job.id, chunks_estimated=chunks_est)


# Keep old path alive so existing scripts don't break.
@router.post(
    "/tenants/{tenant_id}/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    include_in_schema=False,
)
async def upload_document_legacy(
    tenant_id: str,
    file: UploadFile,
    request: Request,
    ctx: AdminContext = _require_admin,
) -> DocumentUploadResponse:
    return await upload_rag_document(file=file, request=request, ctx=ctx)


# ---------------------------------------------------------------------------
# Widget config
# ---------------------------------------------------------------------------


class WidgetConfigPayload(BaseModel):
    persona: str | None = None
    persona_name: str | None = None
    allowed_origins: list[str] | None = None
    enabled_tools: list[str] | None = None


class WidgetConfigResponse(BaseModel):
    tenant_id: str
    persona: str
    persona_name: str
    allowed_origins: list[str]
    enabled_tools: list[str]
    safety_rails: str = "hardcoded — not configurable"


@router.get("/widget-config", response_model=WidgetConfigResponse)
async def get_widget_config(
    request: Request,
    ctx: AdminContext = _require_admin,
) -> WidgetConfigResponse:
    tenant_id = ctx.tenant_id
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant_id missing from token",
        )
    sf = _get_session_factory(request)

    async with sf() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
        )
        row = await session.execute(
            select(WidgetConfig).where(WidgetConfig.tenant_id == UUID(tenant_id))
        )
        cfg = row.scalar_one_or_none()

    if not cfg:
        return WidgetConfigResponse(
            tenant_id=tenant_id,
            persona="You are Keel, a helpful AI academic advisor.",
            persona_name="Keel",
            allowed_origins=[],
            enabled_tools=[],
        )

    return WidgetConfigResponse(
        tenant_id=tenant_id,
        persona=cfg.persona,
        persona_name=cfg.persona_name,
        allowed_origins=list(cfg.allowed_origins),
        enabled_tools=list(cfg.enabled_tools),
    )


@router.put("/widget-config", response_model=WidgetConfigResponse)
async def put_widget_config(
    body: WidgetConfigPayload,
    request: Request,
    ctx: AdminContext = _require_admin,
) -> WidgetConfigResponse:
    """Upsert per-tenant widget configuration. Safety rails are locked in code."""
    tenant_id = ctx.tenant_id
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant_id missing from token",
        )
    sf = _get_session_factory(request)

    async with sf() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
            )
            row = await session.execute(
                select(WidgetConfig).where(WidgetConfig.tenant_id == UUID(tenant_id))
            )
            cfg = row.scalar_one_or_none()

            if not cfg:
                cfg = WidgetConfig(
                    tenant_id=UUID(tenant_id),
                    persona=body.persona or "You are Keel, a helpful AI academic advisor.",
                    persona_name=body.persona_name or "Keel",
                    allowed_origins=body.allowed_origins or [],
                    enabled_tools=body.enabled_tools or [],
                )
                session.add(cfg)
            else:
                if body.persona is not None:
                    cfg.persona = body.persona
                if body.persona_name is not None:
                    cfg.persona_name = body.persona_name
                if body.allowed_origins is not None:
                    cfg.allowed_origins = body.allowed_origins
                if body.enabled_tools is not None:
                    cfg.enabled_tools = body.enabled_tools

    if not hasattr(request.app.state, "widget_origins_map"):
        request.app.state.widget_origins_map = {}
    request.app.state.widget_origins_map[tenant_id] = list(body.allowed_origins or [])

    _log.info("admin.widget_config_updated", tenant_id=tenant_id)
    return WidgetConfigResponse(
        tenant_id=tenant_id,
        persona=body.persona or cfg.persona,
        persona_name=body.persona_name or cfg.persona_name,
        allowed_origins=body.allowed_origins or list(cfg.allowed_origins),
        enabled_tools=body.enabled_tools or list(cfg.enabled_tools),
    )


# ---------------------------------------------------------------------------
# Widget snippet
# ---------------------------------------------------------------------------


class WidgetSnippetResponse(BaseModel):
    snippet: str
    widget_id: str


@router.get("/widget-snippet", response_model=WidgetSnippetResponse)
async def get_widget_snippet(
    request: Request,
    ctx: AdminContext = _require_admin,
) -> WidgetSnippetResponse:
    tenant_id = ctx.tenant_id
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant_id missing from token",
        )
    base_url = str(request.base_url).rstrip("/")
    snippet = (
        f'<script src="{base_url}/widget.js" '
        f'data-widget-id="{tenant_id}"></script>'
    )
    return WidgetSnippetResponse(snippet=snippet, widget_id=tenant_id)


# ---------------------------------------------------------------------------
# Cost dashboard
# ---------------------------------------------------------------------------


class CostRow(BaseModel):
    kind: str
    model: str | None
    total_tokens: int
    total_cost_usd: float
    event_count: int


class CostResponse(BaseModel):
    period: str
    tenant_id: str
    rows: list[CostRow]
    total_cost_usd: float


@router.get("/cost", response_model=CostResponse)
async def get_cost(
    request: Request,
    period: str = "week",
    ctx: AdminContext = _require_admin,
) -> CostResponse:
    tenant_id = ctx.tenant_id
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant_id missing from token",
        )
    sf = _get_session_factory(request)

    interval_map = {"day": "1 day", "week": "7 days", "month": "30 days"}
    interval = interval_map.get(period, "7 days")

    async with sf() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
        )
        rows = await session.execute(
            select(
                UsageEvent.kind,
                UsageEvent.model,
                func.sum(UsageEvent.tokens).label("total_tokens"),
                func.sum(UsageEvent.cost_estimate).label("total_cost"),
                func.count(UsageEvent.id).label("event_count"),
            )
            .where(
                UsageEvent.tenant_id == UUID(tenant_id),
                UsageEvent.created_at >= text(f"now() - interval '{interval}'"),
            )
            .group_by(UsageEvent.kind, UsageEvent.model)
            .order_by(UsageEvent.kind)
        )

    cost_rows = [
        CostRow(
            kind=r.kind,
            model=r.model,
            total_tokens=int(r.total_tokens or 0),
            total_cost_usd=float(r.total_cost or 0),
            event_count=int(r.event_count or 0),
        )
        for r in rows
    ]
    total = sum(r.total_cost_usd for r in cost_rows)
    return CostResponse(period=period, tenant_id=tenant_id, rows=cost_rows, total_cost_usd=total)


# ---------------------------------------------------------------------------
# Audit log (read-only)
# ---------------------------------------------------------------------------


class AuditRow(BaseModel):
    id: int
    actor: str
    actor_name: str | None = None
    action: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    created_at: str


class AuditResponse(BaseModel):
    rows: list[AuditRow]
    total: int


@router.get("/audit", response_model=AuditResponse)
async def get_audit(
    request: Request,
    limit: int = 50,
    ctx: AdminContext = _require_admin,
) -> AuditResponse:
    tenant_id = ctx.tenant_id
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant_id missing from token",
        )
    sf = _get_session_factory(request)

    async with sf() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
        )
        rows = await session.execute(
            select(AuditLog)
            .where(AuditLog.tenant_id == UUID(tenant_id))
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
        total_row = await session.execute(
            select(func.count(AuditLog.id)).where(
                AuditLog.tenant_id == UUID(tenant_id)
            )
        )
        audit_list = list(rows.scalars())

        # Resolve actor UUIDs → portal email (first part as display name)
        actor_ids = {r.actor for r in audit_list if r.actor and len(r.actor) == 36}
        actor_names: dict[str, str] = {}
        if actor_ids:
            name_rows = await session.execute(
                text(
                    "SELECT student_id::text, email FROM portal_user "
                    "WHERE tenant_id = :tid AND student_id::text = ANY(:ids) AND role = 'student'"
                ),
                {"tid": tenant_id, "ids": list(actor_ids)},
            )
            for sid, email in name_rows:
                actor_names[sid] = email.split("@")[0].capitalize()

    audit_rows = [
        AuditRow(
            id=r.id,
            actor=r.actor,
            actor_name=actor_names.get(r.actor),
            action=r.action,
            before=r.before,
            after=r.after,
            created_at=r.created_at.isoformat(),
        )
        for r in audit_list
    ]
    return AuditResponse(rows=audit_rows, total=int(total_row.scalar() or 0))
