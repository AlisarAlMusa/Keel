"""Waitlist repository (layer 2).

Owns the SQL for reading the ``waitlist`` table. SQL is moved verbatim from the
tool layer — same query text, parameters, and result shape.
"""

from __future__ import annotations

import sqlalchemy as sa

from keel.repositories.base import TenantScopedRepository


class WaitlistRepository(TenantScopedRepository):
    """Reads over the ``waitlist`` table."""

    async def find_waiting_entry_id(self, *, student_id: str, section_id: str) -> str | None:
        """The id of the student's active ('waiting') entry for a section, or None."""
        row = await self._session.execute(
            sa.text(
                "SELECT id FROM waitlist "
                "WHERE tenant_id = :tid AND student_id = :sid "
                "AND section_id = :secid AND status = 'waiting'"
            ),
            {"tid": str(self._tenant_id), "sid": student_id, "secid": section_id},
        )
        found = row.scalar_one_or_none()
        return str(found) if found else None
