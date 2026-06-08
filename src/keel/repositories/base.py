"""Tenant-scoped base repository (defense-in-depth, layer 2).

Constitution Principle IV: tenant isolation is enforced at THREE layers — RLS
(DB), repository filtering (here), and pgvector filtering. Every repository
filters by ``tenant_id`` AND asserts each fetched row's tenant matches the
caller, so a misconfigured RLS policy is still caught in code.

Phase 0 provides the structure only; concrete repositories land with their
features in later phases.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from keel.domain.exceptions import PermissionDeniedError


class TenantScopedRepository:
    """Base for repositories bound to a single tenant for their lifetime."""

    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> UUID:
        return self._tenant_id

    def _assert_tenant(self, row_tenant_id: UUID) -> None:
        """Post-fetch assertion: a returned row must belong to this tenant."""
        if row_tenant_id != self._tenant_id:
            raise PermissionDeniedError("cross-tenant row access blocked at repository layer")
