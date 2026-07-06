"""Tenant-scoped base repository.

Tenant isolation is enforced by Row-Level Security (layer 1, in the DB) plus a
``WHERE tenant_id = :tid`` filter on every query issued from the concrete
repositories (layer 2). pgvector retrieval is tenant-filtered by construction
(layer 3). Each repository is bound to one ``(session, tenant_id)`` for its
lifetime; the session must already have ``app.tenant_id`` set (open it via
``tenant_session``).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


class TenantScopedRepository:
    """Base for repositories bound to a single tenant for their lifetime.

    ``tenant_id`` is accepted as a ``UUID`` (the write-path repositories) or a
    canonical UUID string (the read-path tools pass the ``tenant_id`` string
    through unchanged, as they always did before the repository extraction). It is
    bound into SQL as ``str(self._tenant_id)`` either way, so the query text and
    parameters are identical regardless of which form the caller supplies.
    """

    def __init__(self, session: AsyncSession, tenant_id: UUID | str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> UUID | str:
        return self._tenant_id
