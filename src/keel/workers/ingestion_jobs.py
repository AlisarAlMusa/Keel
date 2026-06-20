"""RQ job: re-ingest a single MinIO source into rag_chunks.

Called by the admin document-upload endpoint after storing a file to MinIO.
This is a sync function (RQ workers are sync by default); it sets up its own
event loop, DB engine, and clients so the worker process is self-contained.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import cohere
from sqlalchemy import text

from keel.config import get_settings
from keel.infra import storage as storage_infra
from keel.infra.database import engine as db_infra
from keel.infra.vault import VaultConfig, load_secrets
from keel.logging import get_logger
from keel.services.ingestion import ingest_file

_log = get_logger(__name__)


def run_ingest_source(
    *,
    tenant_id_str: str,
    source: str,
    chunk_type: str,
) -> dict[str, int]:
    """Sync RQ job — ingest one MinIO object into rag_chunks for a tenant.

    Args:
        tenant_id_str: Tenant UUID as a string.
        source:        MinIO key, e.g. "64a058bb-.../catalog.md".
        chunk_type:    "course" | "policy".
    """
    settings = get_settings()
    vault_cfg = VaultConfig(
        addr=settings.vault_addr,
        token=settings.vault_token,
        kv_mount=settings.vault_kv_mount,
        secret_path=settings.vault_secret_path,
    )
    secrets = load_secrets(vault_cfg)

    dsn = settings.database_url.replace(":placeholder@", f":{secrets['db_password']}@", 1)
    s3_client = storage_infra.create_s3_client(
        endpoint=settings.minio_endpoint,
        access_key=secrets["minio_access_key"],
        secret_key=secrets["minio_secret_key"],
    )
    co = cohere.AsyncClientV2(api_key=secrets["cohere_api_key"])

    async def _run() -> dict[str, int]:
        engine = db_infra.create_engine(dsn)
        session_factory = db_infra.create_session_factory(engine)
        try:
            async with session_factory() as session:
                # RLS requires app.tenant_id to be set before any writes to rag_chunks.
                await session.execute(
                    text("SELECT set_config('app.tenant_id', :tid, true)"),
                    {"tid": tenant_id_str},
                )
                result = await ingest_file(
                    tenant_id=UUID(tenant_id_str),
                    source=source,
                    chunk_type=chunk_type,
                    s3_client=s3_client,
                    bucket=settings.minio_bucket,
                    cohere_client=co,
                    embed_model=settings.embed_model,
                    session=session,
                )
                await session.commit()
            return result
        finally:
            await engine.dispose()

    result = asyncio.run(_run())
    _log.info("job.ingest_source.done", source=source, **result)
    return result
