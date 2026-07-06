"""Section repository (layer 2).

Owns the SQL for reading course sections (offerings with seats/slots/instructor).
SQL is moved verbatim from the tool layer — same query text, parameters, and
result shape — so behavior is unchanged.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from keel.repositories.base import TenantScopedRepository


def _term_filter(term: str | None, year: int | None) -> tuple[list[str], dict[str, Any]]:
    """Build optional term/year WHERE clauses + params (shared by the section reads)."""
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if term:
        clauses.append("term = :term")
        params["term"] = term
    if year is not None:
        clauses.append("year = :yr")
        params["yr"] = year
    return clauses, params


class SectionRepository(TenantScopedRepository):
    """Reads over the ``sections`` table for a term/year."""

    async def open_status_for_courses(
        self, *, term: str, year: int, codes: list[str]
    ) -> dict[str, bool]:
        """Map each course code → whether it has at least one section with an open seat."""
        rows = await self._session.execute(
            sa.text(
                "SELECT course_code, "
                "bool_or(enrolled < capacity) AS has_open "
                "FROM sections "
                "WHERE tenant_id = :tid AND term = :term AND year = :yr "
                "AND course_code = ANY(:codes) GROUP BY course_code"
            ),
            {"tid": str(self._tenant_id), "term": term, "yr": year, "codes": codes},
        )
        return {r[0]: bool(r[1]) for r in rows}

    async def open_section_slots_for_courses(
        self, *, term: str, year: int, codes: list[str]
    ) -> list[dict[str, Any]]:
        """Course code + slots for every section with an open seat."""
        rows = await self._session.execute(
            sa.text(
                "SELECT course_code, slots FROM sections "
                "WHERE tenant_id = :tid AND term = :term AND year = :yr "
                "AND course_code = ANY(:codes) AND enrolled < capacity"
            ),
            {"tid": str(self._tenant_id), "term": term, "yr": year, "codes": codes},
        )
        return [dict(r) for r in rows.mappings()]

    async def list_for_courses(
        self, *, term: str, year: int, codes: list[str]
    ) -> list[dict[str, Any]]:
        """All sections for the given courses, ordered by course then creation."""
        rows = await self._session.execute(
            sa.text(
                "SELECT id, course_code, slots, instructor, capacity, enrolled "
                "FROM sections WHERE tenant_id = :tid AND term = :term "
                "AND year = :yr AND course_code = ANY(:codes) "
                "ORDER BY course_code, created_at"
            ),
            {"tid": str(self._tenant_id), "term": term, "yr": year, "codes": codes},
        )
        return [dict(r) for r in rows.mappings()]

    async def by_ids(
        self, *, term: str, year: int, ids: list[str]
    ) -> list[dict[str, Any]]:
        """Sections matching the given ids within a term/year (with seat counts)."""
        rows = await self._session.execute(
            sa.text(
                "SELECT id, course_code, slots, instructor, enrolled, capacity FROM sections "
                "WHERE tenant_id = :tid AND term = :term AND year = :yr AND id = ANY(:ids)"
            ),
            {"tid": str(self._tenant_id), "term": term, "yr": year, "ids": ids},
        )
        return [dict(r) for r in rows.mappings()]

    async def open_with_instructor_for_courses(
        self, *, term: str, year: int, codes: list[str]
    ) -> list[dict[str, Any]]:
        """Open sections (id/course/slots/instructor) for courses, ordered for stable picks."""
        rows = await self._session.execute(
            sa.text(
                "SELECT id, course_code, slots, instructor FROM sections "
                "WHERE tenant_id = :tid AND term = :term AND year = :yr "
                "AND course_code = ANY(:codes) AND enrolled < capacity "
                "ORDER BY course_code, created_at"
            ),
            {"tid": str(self._tenant_id), "term": term, "yr": year, "codes": codes},
        )
        return [dict(r) for r in rows.mappings()]

    async def first_id_for_course(
        self, *, course_code: str, term: str | None, year: int | None
    ) -> str | None:
        """First section id for a course (term/year optional), or None. Ignores capacity."""
        clauses = ["tenant_id = :tid", "course_code = :code"]
        params: dict[str, Any] = {"tid": str(self._tenant_id), "code": course_code}
        extra, eparams = _term_filter(term, year)
        clauses.extend(extra)
        params.update(eparams)
        row = await self._session.execute(
            sa.text(
                "SELECT id FROM sections WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at LIMIT 1"
            ),
            params,
        )
        found = row.scalar_one_or_none()
        return str(found) if found else None

    async def full_sections_for_course(
        self, *, course_code: str, term: str | None, year: int | None
    ) -> list[dict[str, Any]]:
        """Every FULL section (enrolled >= capacity) of a course, with waiting counts."""
        clauses = ["s.tenant_id = :tid", "s.course_code = :code", "s.enrolled >= s.capacity"]
        params: dict[str, Any] = {"tid": str(self._tenant_id), "code": course_code}
        extra, eparams = _term_filter(term, year)
        clauses.extend(f"s.{c}" for c in extra)
        params.update(eparams)
        rows = await self._session.execute(
            sa.text(
                "SELECT s.id, s.instructor, s.slots, s.term, s.year, "
                "(SELECT COUNT(*) FROM waitlist w "
                " WHERE w.section_id = s.id AND w.status = 'waiting') AS waiting "
                "FROM sections s WHERE " + " AND ".join(clauses) + " ORDER BY s.instructor"
            ),
            params,
        )
        return [dict(r) for r in rows.mappings()]

    async def any_open_seat(
        self, *, course_code: str, term: str | None, year: int | None
    ) -> bool:
        """True if some section of the course has an open seat (term-scoped when given)."""
        clauses = ["tenant_id = :tid", "course_code = :code", "enrolled < capacity"]
        params: dict[str, Any] = {"tid": str(self._tenant_id), "code": course_code}
        extra, eparams = _term_filter(term, year)
        clauses.extend(extra)
        params.update(eparams)
        row = await self._session.execute(
            sa.text("SELECT 1 FROM sections WHERE " + " AND ".join(clauses) + " LIMIT 1"),
            params,
        )
        return row.first() is not None

    async def get_by_id(self, section_id: str) -> dict[str, Any] | None:
        """Fetch a single section's identity + capacity for this tenant, or None."""
        row = await self._session.execute(
            sa.text(
                "SELECT id, course_code, instructor, slots, term, year, capacity, enrolled "
                "FROM sections WHERE id = :sid AND tenant_id = :tid"
            ),
            {"sid": section_id, "tid": str(self._tenant_id)},
        )
        rec = row.mappings().first()
        return dict(rec) if rec else None
