"""Platform operator background jobs — RQ workers.

erase_tenant_job: cascade-delete all rows for a tenant, audit counts,
  delete MinIO prefix, delete the tenant row.  Idempotent (tenant already
  gone = no-op success).
"""

from __future__ import annotations

import asyncio
import os
from uuid import UUID

from sqlalchemy import text

from keel.infra import database as db_infra
from keel.infra import storage as storage_infra
from keel.logging import configure_logging, get_logger

log = get_logger(__name__)

# Tables with tenant_id in cascade order (children before parent).
# platform_audit is excluded — it uses ON DELETE SET NULL and must survive.
_TENANT_TABLES: tuple[str, ...] = (
    "portal_user",
    "rag_chunks",
    "student_preferences",
    "actions",
    "notifications",
    "audit_log",
    "outbox",
    "usage_event",
    "widget_config",
    "waitlist",
    "enrollments",
    "request_queue",
    "advisors",
    "student_transcript",
    "plans",
    "program_requirements",
    "sections",
    "corequisites",
    "prerequisites",
    "students",
    "courses",
    "programs",
)


def _build_dsn() -> str:
    """Resolve the database DSN for the worker process."""
    dsn = os.environ.get("DATABASE_URL", "")
    if "placeholder" in dsn:
        from keel.config import get_settings
        from keel.infra.vault import VaultConfig, load_secrets

        settings = get_settings()
        secrets = load_secrets(
            VaultConfig(
                addr=settings.vault_addr,
                token=settings.vault_token,
                kv_mount=settings.vault_kv_mount,
                secret_path=settings.vault_secret_path,
            )
        )
        dsn = dsn.replace(":placeholder@", f":{secrets['db_password']}@", 1)
    return dsn


async def _do_erase(tenant_id: UUID) -> dict[str, int]:
    """Perform the cascade erase inside an async context. Returns row counts."""
    configure_logging(service="keel-worker-erase", level="INFO")
    dsn = _build_dsn()
    engine = db_infra.create_engine(dsn)
    session_factory = db_infra.create_session_factory(engine)
    counts: dict[str, int] = {}

    try:
        async with session_factory() as session:
            async with session.begin():
                # Verify tenant exists
                row = await session.execute(
                    text("SELECT name FROM tenants WHERE id = :tid"),
                    {"tid": str(tenant_id)},
                )
                tenant_row = row.fetchone()
                if not tenant_row:
                    log.info("erase_tenant.already_gone", tenant_id=str(tenant_id))
                    return {"note": "tenant_not_found_noop"}  # type: ignore[return-value]

                tenant_name = tenant_row.name

                # Cascade delete tenant-content tables (no RLS bypass needed — we own
                # all rows by tenant_id, and we're deleting them directly).
                for table in _TENANT_TABLES:
                    result = await session.execute(
                        text(f"DELETE FROM {table} WHERE tenant_id = :tid"),
                        {"tid": str(tenant_id)},
                    )
                    n = result.rowcount
                    if n:
                        counts[table] = n

                # Delete tenant_admin users for this tenant
                result = await session.execute(
                    text("DELETE FROM users WHERE tenant_id = :tid"),
                    {"tid": str(tenant_id)},
                )
                counts["users"] = result.rowcount

                # Delete the tenant row itself
                await session.execute(
                    text("DELETE FROM tenants WHERE id = :tid"),
                    {"tid": str(tenant_id)},
                )
                counts["tenants"] = 1

                # Write the completion audit BEFORE committing (platform_audit has
                # target_tenant_id SET NULL on the FK, so it survives after tenants row
                # is deleted — but we write it in the same txn while the FK still holds).
                await session.execute(
                    text(
                        "INSERT INTO platform_audit "
                        "(actor_user_id, action, target_tenant_id, detail, created_at) "
                        "VALUES (NULL, 'erase', NULL, CAST(:detail AS jsonb), now())"
                    ),
                    {
                        "detail": (
                            f'{{"tenant_name": "{tenant_name}", '
                            f'"counts": {counts}, "completed": true}}'
                        )
                    },
                )

        # MinIO: delete all blobs prefixed by tenant_id (best-effort)
        try:
            from keel.config import get_settings
            from keel.infra.vault import VaultConfig, load_secrets

            settings = get_settings()
            secrets = load_secrets(
                VaultConfig(
                    addr=settings.vault_addr,
                    token=settings.vault_token,
                    kv_mount=settings.vault_kv_mount,
                    secret_path=settings.vault_secret_path,
                )
            )
            s3 = storage_infra.create_s3_client(
                endpoint=settings.minio_endpoint,
                access_key=secrets["minio_access_key"],
                secret_key=secrets["minio_secret_key"],
            )

            bucket = settings.minio_bucket
            prefix = str(tenant_id)
            paginator = s3.get_paginator("list_objects_v2")  # type: ignore[attr-defined]
            deleted_blobs = 0
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    s3.delete_object(Bucket=bucket, Key=obj["Key"])  # type: ignore[attr-defined]
                    deleted_blobs += 1
            if deleted_blobs:
                counts["minio_blobs"] = deleted_blobs
                log.info("erase_tenant.minio_deleted", count=deleted_blobs)
        except Exception as exc:  # noqa: BLE001
            log.warning("erase_tenant.minio_failed", error=type(exc).__name__)

    finally:
        await engine.dispose()

    log.info("erase_tenant.complete", tenant_id=str(tenant_id), counts=counts)
    return counts


def erase_tenant_job(tenant_id_str: str) -> dict[str, int]:
    """RQ-callable synchronous wrapper for the async erase logic.

    Idempotent: if the tenant no longer exists, returns immediately.
    """
    tenant_id = UUID(tenant_id_str)
    return asyncio.run(_do_erase(tenant_id))
