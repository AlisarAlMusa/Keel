"""RAG pipeline smoke tests — 5 hand-written queries against seeded corpus.

Requires live infrastructure:
  - TEST_DATABASE_URL pointing to a seeded DB (northane tenant must have rag_chunks)
  - COHERE_API_KEY for embed + rerank

Skipped automatically when either is absent so the unit CI job stays green.
Queries target content known to exist in data/rag-corpus/northane_*.md.
"""

from __future__ import annotations

import os

import cohere
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from keel.config import get_settings
from keel.infra.rag import retrieve

pytestmark = pytest.mark.asyncio

_DB_URL = os.environ.get("TEST_DATABASE_URL", "")
_COHERE_KEY = os.environ.get("COHERE_API_KEY", "")

NORTHANE_TENANT_ID = "64a058bb-c16d-46aa-9621-e52aa65c48d4"

_QUERIES: list[tuple[str, str]] = [
    ("What courses satisfy the CS core requirement?", "course content"),
    ("What is the minimum GPA required for graduation?", "policy content"),
    ("Are there any prerequisites for upper-division courses?", "prereq policy"),
    ("What happens if I fail a required course twice?", "repeat policy"),
    ("Which courses are offered in the spring semester?", "offering term"),
]


@pytest.fixture(scope="module")
def cohere_client():
    if not _COHERE_KEY:
        pytest.skip("COHERE_API_KEY not set — skipping RAG smoke tests")
    return cohere.AsyncClientV2(api_key=_COHERE_KEY)


@pytest.fixture(scope="module")
def db_session_factory():
    if not _DB_URL:
        pytest.skip("TEST_DATABASE_URL not set — skipping RAG smoke tests")
    engine = create_async_engine(_DB_URL, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory


@pytest.mark.parametrize("query,label", _QUERIES)
async def test_rag_returns_results(query: str, label: str, cohere_client, db_session_factory):
    """Each query must return at least one result for the northane tenant."""
    settings = get_settings()
    async with db_session_factory() as session:
        results = await retrieve(
            query=query,
            tenant_id=NORTHANE_TENANT_ID,
            session=session,
            cohere_client=cohere_client,
            settings=settings,
        )
    assert len(results) >= 1, (
        f"RAG returned 0 results for [{label}] query: {query!r}. "
        "Corpus may not be ingested or embed/rerank failed."
    )
    # Each result must be tenant-scoped
    for r in results:
        assert r.tenant_id == NORTHANE_TENANT_ID, f"Cross-tenant leak: got {r.tenant_id}"
