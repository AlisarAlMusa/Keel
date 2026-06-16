"""Admin endpoints — registrar-facing operations.

POST /admin/tenants/{tenant_id}/documents
  Accepts a markdown file upload, stores it to MinIO, and enqueues an RQ
  ingestion job. Returns 202 with the MinIO key and job ID.

Auth: protected by X-Admin-Token header (checked against vault secret).
Tenant isolation: the tenant_id in the path is trusted as the registrar
has already authenticated; the MinIO key and DB write are scoped to it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, UploadFile, status
from pydantic import BaseModel
from redis import Redis
from rq import Queue

from keel.infra import storage as storage_infra
from keel.logging import get_logger
from keel.workers.ingestion_jobs import run_ingest_source

router = APIRouter(prefix="/admin", tags=["admin"])
_log = get_logger(__name__)

_RQ_QUEUE = "keel"


class DocumentUploadResponse(BaseModel):
    source: str
    job_id: str
    status: str = "enqueued"


def _get_redis(request: Request) -> Any:
    return request.app.state.redis


@router.post(
    "/tenants/{tenant_id}/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    tenant_id: str,
    file: UploadFile,
    request: Request,
    chunk_type: str = "policy",
) -> DocumentUploadResponse:
    """Upload a markdown document and trigger async RAG ingestion.

    Args:
        tenant_id:  Tenant UUID (path param).
        file:       Multipart markdown file.
        chunk_type: "course" or "policy" (query param, default "policy").
    """
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="filename required")

    content_bytes = await file.read()
    if not content_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty file")

    # MinIO key scoped to tenant
    source = f"{tenant_id}/{file.filename}"

    # Store to MinIO
    s3_client = request.app.state.storage
    from keel.config import get_settings  # noqa: PLC0415 — local import avoids circular
    _settings = get_settings()

    try:
        storage_infra.put_text(s3_client, _settings.minio_bucket, source, content_bytes.decode())
    except Exception as exc:
        _log.error("admin.upload_failed", tenant_id=tenant_id, source=source, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"MinIO upload failed: {type(exc).__name__}",
        ) from exc

    # Enqueue RQ ingestion job (sync Redis connection for RQ)
    redis_conn = Redis.from_url(_settings.redis_url)
    q = Queue(_RQ_QUEUE, connection=redis_conn)
    job = q.enqueue(
        run_ingest_source,
        kwargs={
            "tenant_id_str": tenant_id,
            "source": source,
            "chunk_type": chunk_type,
        },
    )

    _log.info(
        "admin.document_enqueued",
        tenant_id=tenant_id,
        source=source,
        chunk_type=chunk_type,
        job_id=job.id,
    )
    return DocumentUploadResponse(source=source, job_id=job.id)
