"""RAG ingestion pipeline: load → clean → chunk → embed → upsert.

One reusable async function for two triggers:
  • seed (this phase) — called directly from scripts/seed.py
  • admin doc upload (Phase 5) — enqueued as an RQ job

Invariants:
  • chunk_id = sha256(tenant_id|source|section)[:16] — stable hash → idempotent re-ingest
  • Re-ingest upserts changed chunks and deletes orphaned chunk_ids (no stale vectors)
  • Tenant filter is applied on every DB write — a chunk always carries its tenant_id
  • Embedding calls are async-batched with timeout + tenacity retry
  • All external calls degrade gracefully (fail logs, partial success returned)
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Any
from uuid import UUID

import cohere
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from keel.infra.storage import get_text
from keel.logging import get_logger

_log = get_logger(__name__)

_EMBED_BATCH = 96  # Cohere embed API batch limit
_TRANSIENT = (Exception,)  # cohere raises generic exceptions on transient errors


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def _chunks_from_markdown(
    text: str,
    source: str,
    chunk_type: str,
    tenant_id: str,
) -> list[dict[str, Any]]:
    """Split a markdown document on '## ' headings into one chunk per section.

    For course catalogs: each '## ' heading is one course code.
    For policy docs: each '## ' heading is one policy section.
    Returns dicts ready for ORM insert (no embedding yet).
    """
    # Split on level-2 headings; keep the heading in each chunk.
    parts = re.split(r"(?=^## )", text, flags=re.MULTILINE)
    chunks = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        lines = part.splitlines()
        heading = lines[0].lstrip("#").strip() if lines else "intro"
        section = heading
        cid = hashlib.sha256(f"{tenant_id}|{source}|{section}".encode()).hexdigest()[:16]

        chunk: dict[str, Any] = {
            "chunk_id": cid,
            "source": source,
            "type": chunk_type,
            "content": part,
            "lang": "en",
        }
        if chunk_type == "course":
            chunk["code"] = heading.split()[0] if heading else None
            chunk["doc"] = None
            chunk["section"] = None
        else:
            chunk["code"] = None
            chunk["doc"] = source.split("/")[-1].replace(".md", "")
            chunk["section"] = section

        chunks.append(chunk)
    return chunks


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


async def _embed_texts(
    co: cohere.AsyncClientV2,
    texts: list[str],
    model: str,
    input_type: str = "search_document",
) -> list[list[float]]:
    """Embed a list of texts via Cohere, batched, with retry on transient errors."""
    results: list[list[float]] = []
    for i in range(0, len(texts), _EMBED_BATCH):
        batch = texts[i : i + _EMBED_BATCH]
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=0.5, max=4),
                retry=retry_if_exception_type(_TRANSIENT),
            ):
                with attempt:
                    res = await co.embed(
                        texts=batch,
                        model=model,
                        input_type=input_type,
                        embedding_types=["float"],
                    )
                    floats = (res.embeddings.float_ or []) if res.embeddings else []
                    results.extend(floats)
        except Exception as exc:
            _log.error("embed_batch_failed", batch_start=i, error=str(exc))
            results.extend([[0.0] * 1024] * len(batch))  # zero vector fallback
    return results


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------


_UPSERT_SQL = sa.text("""
    INSERT INTO rag_chunks
        (tenant_id, chunk_id, source, type, code, doc, section, content, embedding, lang)
    VALUES
        (:tenant_id, :chunk_id, :source, :type, :code, :doc, :section, :content,
         CAST(:embedding AS vector(1024)), :lang)
    ON CONFLICT (tenant_id, chunk_id) DO UPDATE SET
        content    = EXCLUDED.content,
        embedding  = EXCLUDED.embedding,
        updated_at = now()
""")

_DELETE_ORPHANS_SQL = sa.text("""
    DELETE FROM rag_chunks
    WHERE tenant_id = :tenant_id
      AND source    = :source
      AND chunk_id  != ALL(:live_ids)
""")


async def _upsert_chunks(
    session: AsyncSession,
    tenant_id: UUID,
    rows: list[dict[str, Any]],
) -> int:
    """Upsert chunk rows one by one (embedding passed as Postgres vector literal)."""
    if not rows:
        return 0
    tid = str(tenant_id)
    for r in rows:
        emb: list[float] = r.get("embedding") or []
        emb_str = "[" + ",".join(str(v) for v in emb) + "]" if emb else None
        await session.execute(
            _UPSERT_SQL,
            {
                "tenant_id": tid,
                "chunk_id": r["chunk_id"],
                "source": r["source"],
                "type": r["type"],
                "code": r.get("code"),
                "doc": r.get("doc"),
                "section": r.get("section"),
                "content": r["content"],
                "embedding": emb_str,
                "lang": r.get("lang", "en"),
            },
        )
    return len(rows)


async def _delete_orphans(
    session: AsyncSession,
    tenant_id: UUID,
    source: str,
    live_chunk_ids: set[str],
) -> int:
    """Delete chunks for this source whose chunk_id is no longer present."""
    if not live_chunk_ids:
        return 0
    result = await session.execute(
        sa.text(
            "DELETE FROM rag_chunks "
            "WHERE tenant_id = :tid AND source = :src "
            "AND chunk_id != ALL(:ids)"
        ),
        {"tid": str(tenant_id), "src": source, "ids": list(live_chunk_ids)},
    )
    return result.rowcount or 0  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def ingest_file(
    *,
    tenant_id: UUID,
    source: str,
    chunk_type: str,
    s3_client: Any,
    bucket: str,
    cohere_client: cohere.AsyncClientV2,
    embed_model: str,
    session: AsyncSession,
) -> dict[str, int]:
    """Ingest one MinIO object (catalog.md or policies.md) into rag_chunks.

    Args:
        tenant_id:      Tenant UUID — all chunks are scoped to this tenant.
        source:         MinIO key, e.g. "northane/catalog.md".
        chunk_type:     "course" | "policy".
        s3_client:      Boto3 S3 client (sync; wrapped in asyncio.to_thread).
        bucket:         MinIO bucket name.
        cohere_client:  Async Cohere client for embedding.
        embed_model:    e.g. "embed-multilingual-v3.0".
        session:        AsyncSession (caller manages transaction).

    Returns:
        {"upserted": N, "deleted": N}
    """
    tid_str = str(tenant_id)

    # Load markdown from MinIO (sync boto3 in thread).
    try:
        markdown = await asyncio.to_thread(get_text, s3_client, bucket, source)
    except Exception as exc:
        _log.error("ingest.load_failed", source=source, error=str(exc))
        return {"upserted": 0, "deleted": 0}

    # Chunk.
    chunks = _chunks_from_markdown(markdown, source, chunk_type, tid_str)
    if not chunks:
        _log.warning("ingest.no_chunks", source=source)
        return {"upserted": 0, "deleted": 0}

    # Embed.
    texts = [c["content"] for c in chunks]
    embeddings = await _embed_texts(cohere_client, texts, embed_model)
    for chunk, emb in zip(chunks, embeddings, strict=True):
        chunk["embedding"] = emb

    # Upsert.
    n_up = await _upsert_chunks(session, tenant_id, chunks)

    # Delete orphans (chunk_ids from this source that are no longer in the file).
    live_ids = {c["chunk_id"] for c in chunks}
    n_del = await _delete_orphans(session, tenant_id, source, live_ids)

    _log.info(
        "ingest.done",
        source=source,
        chunks=len(chunks),
        upserted=n_up,
        deleted=n_del,
    )
    return {"upserted": n_up, "deleted": n_del}
