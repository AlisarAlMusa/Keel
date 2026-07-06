"""Program / degree-requirement repository (layer 2).

Owns the SQL for reading academic programs and their requirement groups. The
row→engine-object mapping (``Program`` / requirement contracts) stays in the
caller — this layer only fetches rows. SQL is moved verbatim from the tool layer,
so behavior is unchanged.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from keel.repositories.base import TenantScopedRepository


class ProgramRepository(TenantScopedRepository):
    """Reads for programs and their requirement groups."""

    async def get_by_code(self, program_code: str) -> dict[str, Any] | None:
        """Fetch one program row by its code, or ``None`` if absent for this tenant."""
        row = await self._session.execute(
            sa.text(
                "SELECT id, code, name, total_credits_required, tenant_id "
                "FROM programs WHERE code = :code AND tenant_id = :tid"
            ),
            {"code": program_code, "tid": str(self._tenant_id)},
        )
        r = row.mappings().first()
        return dict(r) if r else None

    async def requirements_for(self, program_id: Any) -> list[dict[str, Any]]:
        """Fetch the requirement-group rows for one program id."""
        rq = await self._session.execute(
            sa.text(
                "SELECT group_name, required_credits, eligible_course_codes "
                "FROM program_requirements WHERE program_id = :pid AND tenant_id = :tid"
            ),
            {"pid": program_id, "tid": str(self._tenant_id)},
        )
        return [dict(r) for r in rq.mappings().all()]
