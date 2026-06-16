"""Hybrid RAG retrieval: dense (pgvector) + sparse (FTS) → RRF → Cohere rerank.

Degradation chain (spec §T2b):
  • Rerank fails  → return fused-RRF order (top rerank_top_n)
  • Cohere embed fails → FTS-only (top sparse_k, then slice to rerank_top_n)
  • Never hard-fail to caller — log, degrade, return what we have

Tenant isolation:
  • Every SQL query carries a WHERE tenant_id = :tid filter.
  • pgvector query is also RLS-enforced at the DB level (defense in depth).

Usage:
    results = await retrieve(
        query="CS301 prerequisites",
        tenant_id="<uuid>",
        session=async_session,
        cohere_client=co,
        settings=cfg,
    )
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import cohere
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from keel.config import Settings
from keel.domain.schemas import RagResult
from keel.logging import get_logger

_log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


async def _embed_query(
    co: cohere.AsyncClientV2,
    text: str,
    model: str,
) -> list[float] | None:
    """Embed a single query string.  Returns None on failure (triggers FTS-only path)."""
    try:
        res = await co.embed(
            texts=[text],
            model=model,
            input_type="search_query",
            embedding_types=["float"],
        )
        floats: list[list[float]] = res.embeddings.float_  # type: ignore[union-attr]
        return floats[0]
    except Exception as exc:
        _log.warning("rag.embed_query_failed", error=str(exc))
        return None


# ---------------------------------------------------------------------------
# Dense retrieval (pgvector cosine)
# ---------------------------------------------------------------------------

_DENSE_SQL = sa.text("""
    SELECT
        chunk_id,
        source,
        type,
        content,
        code,
        doc,
        section,
        lang,
        1 - (embedding <=> CAST(:embedding AS vector(1024))) AS cosine_sim
    FROM rag_chunks
    WHERE tenant_id = :tid
      AND embedding IS NOT NULL
    ORDER BY embedding <=> CAST(:embedding AS vector(1024))
    LIMIT :k
""")


async def _dense_retrieve(
    session: AsyncSession,
    tenant_id: str,
    embedding: list[float],
    k: int,
) -> list[dict[str, Any]]:
    emb_str = "[" + ",".join(str(v) for v in embedding) + "]"
    try:
        result = await session.execute(
            _DENSE_SQL, {"tid": tenant_id, "embedding": emb_str, "k": k}
        )
        rows = result.mappings().all()
        return [dict(r) for r in rows]
    except Exception as exc:
        _log.warning("rag.dense_failed", error=str(exc))
        return []


# ---------------------------------------------------------------------------
# Sparse retrieval (Postgres FTS ts_rank)
# ---------------------------------------------------------------------------

_SPARSE_SQL = sa.text("""
    SELECT
        chunk_id,
        source,
        type,
        content,
        code,
        doc,
        section,
        lang,
        ts_rank(to_tsvector('english', content), plainto_tsquery('english', :query)) AS fts_score
    FROM rag_chunks
    WHERE tenant_id = :tid
      AND to_tsvector('english', content) @@ plainto_tsquery('english', :query)
    ORDER BY fts_score DESC
    LIMIT :k
""")


async def _sparse_retrieve(
    session: AsyncSession,
    tenant_id: str,
    query: str,
    k: int,
) -> list[dict[str, Any]]:
    try:
        result = await session.execute(
            _SPARSE_SQL, {"tid": tenant_id, "query": query, "k": k}
        )
        rows = result.mappings().all()
        return [dict(r) for r in rows]
    except Exception as exc:
        _log.warning("rag.sparse_failed", error=str(exc))
        return []


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion
# ---------------------------------------------------------------------------


def _rrf_fuse(
    dense_rows: list[dict[str, Any]],
    sparse_rows: list[dict[str, Any]],
    k: int,
) -> list[dict[str, Any]]:
    """Merge two ranked lists with RRF(k).  Returns deduplicated rows sorted by RRF score."""
    scores: dict[str, float] = {}
    meta: dict[str, dict[str, Any]] = {}

    for rank, row in enumerate(dense_rows, start=1):
        cid = row["chunk_id"]
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
        meta[cid] = row

    for rank, row in enumerate(sparse_rows, start=1):
        cid = row["chunk_id"]
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
        if cid not in meta:
            meta[cid] = row

    sorted_ids = sorted(scores, key=lambda c: scores[c], reverse=True)
    fused = []
    for cid in sorted_ids:
        row = dict(meta[cid])
        row["_rrf_score"] = scores[cid]
        fused.append(row)
    return fused


# ---------------------------------------------------------------------------
# Cohere rerank
# ---------------------------------------------------------------------------


async def _rerank(
    co: cohere.AsyncClientV2,
    query: str,
    candidates: list[dict[str, Any]],
    rerank_model: str,
    top_n: int,
) -> list[dict[str, Any]] | None:
    """Rerank candidates with Cohere.  Returns None on failure (caller uses fused order)."""
    if not candidates:
        return []
    try:
        docs = [c["content"] for c in candidates]
        res = await co.rerank(
            query=query,
            documents=docs,
            model=rerank_model,
            top_n=top_n,
        )
        reranked = []
        for item in res.results:
            row = dict(candidates[item.index])
            row["_rerank_score"] = item.relevance_score
            reranked.append(row)
        return reranked
    except Exception as exc:
        _log.warning("rag.rerank_failed", error=str(exc))
        return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def retrieve(
    *,
    query: str,
    tenant_id: str | UUID,
    session: AsyncSession,
    cohere_client: cohere.AsyncClientV2,
    settings: Settings,
) -> list[RagResult]:
    """Full hybrid retrieval pipeline.

    1. Embed query (Cohere)
    2. Dense retrieve (pgvector cosine, top dense_k)       — parallel with step 3
    3. Sparse retrieve (Postgres FTS, top sparse_k)
    4. RRF fuse, dedupe
    5. Cohere rerank (top ~12 → rerank_top_n)
    6. Return list[RagResult] (empty on full failure, never raises)
    """
    tid = str(tenant_id)

    # Embed query (may fail → FTS-only path)
    embedding = await _embed_query(cohere_client, query, settings.embed_model)

    if embedding is not None:
        # Run dense + sparse in parallel
        dense_rows, sparse_rows = await asyncio.gather(
            _dense_retrieve(session, tid, embedding, settings.dense_k),
            _sparse_retrieve(session, tid, query, settings.sparse_k),
        )
    else:
        _log.info("rag.degraded.fts_only", query_snippet=query[:60])
        dense_rows = []
        sparse_rows = await _sparse_retrieve(session, tid, query, settings.sparse_k)

    if not dense_rows and not sparse_rows:
        _log.warning("rag.no_candidates", tenant_id=tid)
        return []

    # RRF fusion
    fused = _rrf_fuse(dense_rows, sparse_rows, settings.rrf_k)

    # Rerank top ~12 candidates
    rerank_input = fused[:12]
    reranked = await _rerank(
        cohere_client, query, rerank_input, settings.rerank_model, settings.rerank_top_n
    )

    if reranked is None:
        # Rerank failed → fused order, slice to rerank_top_n
        _log.info("rag.degraded.fused_order")
        final = fused[: settings.rerank_top_n]
        score_field = "_rrf_score"
    else:
        final = reranked
        score_field = "_rerank_score"

    results: list[RagResult] = []
    for row in final:
        results.append(
            RagResult(
                chunk_id=row["chunk_id"],
                source=row["source"],
                type=row["type"],
                content=row["content"],
                score=float(row.get(score_field, 0.0)),
                code=row.get("code"),
                doc=row.get("doc"),
                section=row.get("section"),
                lang=row.get("lang", "en"),
            )
        )

    _log.info(
        "rag.retrieved",
        tenant_id=tid,
        query_snippet=query[:60],
        dense=len(dense_rows),
        sparse=len(sparse_rows),
        fused=len(fused),
        returned=len(results),
        degraded_embed=embedding is None,
        degraded_rerank=reranked is None,
    )
    return results
